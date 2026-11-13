"""Hierarchical leg-performance model.

For each scheduled leg the model predicts four distributions:

* departure delay (17 bins, minutes) for operated flights,
* en-route deviation = arrival delay - departure delay (9 bins), so a sampled arrival is
  consistent with the sampled departure of the same leg,
* cancellation probability, and
* diversion probability for operated flights.

Each distribution is a Dirichlet-smoothed backoff chain from global -> carrier -> ... ->
the specific recurring flight. A level's counts are pulled toward its parent with a
concentration alpha tuned on a later held-out period (empirical Bayes). Counts are kept by
calendar month and reweighted by seasonal distance to the travel month at query time, so one
fitted model serves any travel date.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np

INF = math.inf
DEP_EDGES = np.array([-INF, -15, -10, -5, 0, 5, 10, 15, 20, 30, 45, 60, 90, 120, 180, 240, 360, INF])
ENR_EDGES = np.array([-INF, -20, -10, -5, 0, 5, 10, 20, 40, INF])
BIN2 = np.array([-INF, 0.5, INF])
HB_EDGES = np.array([420, 600, 780, 960, 1140])  # local departure blocks: <7,7-10,10-13,13-16,16-19,>=19

# Level definitions: names of columns forming each key. () is the global level.
LEVELS = {
    'dep': [(), ('op',), ('op', 'origin'), ('op', 'origin', 'hb'), ('op', 'op_fn', 'origin', 'dest')],
    'enr': [(), ('dest',), ('origin', 'dest'), ('op', 'origin', 'dest')],
    'cxl': [(), ('op',), ('op', 'origin'), ('op', 'origin', 'dest'), ('op', 'op_fn', 'origin', 'dest')],
    'div': [(), ('op',), ('op', 'dest')],
}
EDGES = {'dep': DEP_EDGES, 'enr': ENR_EDGES, 'cxl': BIN2, 'div': BIN2}
# Departure delay uses a hurdle decomposition: P(>=15 min) and the shapes below/above 15 min are
# smoothed with separate strengths. One strength for all 17 bins would be dominated by the
# on-time bins, which look alike everywhere, and would flatten the late-departure signal.
DEP_SPLIT = int(np.flatnonzero(DEP_EDGES == 15)[0])
PARTS = {'dep': ['dep_gate', 'dep_lo', 'dep_hi'], 'enr': ['enr'], 'cxl': ['cxl'], 'div': ['div']}
ALPHA_GRID = [0.5, 1, 2, 4, 8, 16, 32, 64, 128, 256, 1024, 4096, 16384]
DEFAULT_ALPHA = 8.0
SEASON_SIGMA = 1.0   # months
SEASON_FLOOR = 0.2   # weight retained by distant months


FLOOR_GRID = [0.0, 0.02, 0.05, 0.1, 0.2, 0.4, 1.0]


def season_weights(month: int, floor: float = SEASON_FLOOR, sigma: float = SEASON_SIGMA) -> np.ndarray:
    """Weight of each calendar month's counts for a travel month (1.0 = same month)."""
    m = np.arange(1, 13)
    d = np.minimum(np.abs(m - month), 12 - np.abs(m - month))
    return floor + (1 - floor) * np.exp(-(d ** 2) / (2 * sigma ** 2))


def hour_block(dep_local):
    return np.searchsorted(HB_EDGES, dep_local, side='right')


@dataclass
class History:
    """Column arrays of observed operations; strings are factorized."""
    op: np.ndarray
    op_fn: np.ndarray
    origin: np.ndarray
    dest: np.ndarray
    hb: np.ndarray
    month: np.ndarray
    date: np.ndarray          # days since epoch
    dep_delay: np.ndarray
    arr_delay: np.ndarray
    cancelled: np.ndarray
    diverted: np.ndarray
    vocab: dict               # column -> list of strings

    def __len__(self):
        return len(self.op)

    def subset(self, mask):
        return History(**{k: (v[mask] if isinstance(v, np.ndarray) else v) for k, v in self.__dict__.items()})


def load_history(con, date_from: str | None = None, date_to: str | None = None, chunk: int = 250_000) -> History:
    where, args = [], []
    if date_from:
        where.append('flight_date>=?')
        args.append(date_from)
    if date_to:
        where.append('flight_date<=?')
        args.append(date_to)
    sql = ('SELECT op, op_fn, origin, dest, dep_local, month, CAST(julianday(flight_date)-2440587.5 AS INTEGER), '
           'dep_delay, arr_delay, cancelled, diverted FROM flights' + (' WHERE ' + ' AND '.join(where) if where else ''))
    maps = {'op': {}, 'op_fn': {}, 'ap': {}}
    parts = {k: [] for k in ('op', 'op_fn', 'origin', 'dest', 'dep_local', 'month', 'date', 'dep', 'arr', 'cxl', 'div')}
    cur = con.execute(sql, args)
    while rows := cur.fetchmany(chunk):
        cols = list(zip(*rows))
        for name, src, m in (('op', 0, 'op'), ('op_fn', 1, 'op_fn'), ('origin', 2, 'ap'), ('dest', 3, 'ap')):
            mp = maps[m]
            parts[name].append(np.fromiter((mp.setdefault(s, len(mp)) for s in cols[src]), np.int32, len(rows)))
        parts['dep_local'].append(np.array(cols[4], np.int16))
        parts['month'].append(np.array(cols[5], np.int8))
        parts['date'].append(np.array(cols[6], np.int32))
        parts['dep'].append(np.array([np.nan if v is None else v for v in cols[7]], np.float32))
        parts['arr'].append(np.array([np.nan if v is None else v for v in cols[8]], np.float32))
        parts['cxl'].append(np.array(cols[9], np.int8))
        parts['div'].append(np.array(cols[10], np.int8))
    if not parts['op']:
        raise ValueError('No flight history in the selected period; import BTS on-time data first')
    cat = {k: np.concatenate(v) for k, v in parts.items()}
    vocab = {k: list(v) for k, v in (('op', maps['op']), ('op_fn', maps['op_fn']), ('ap', maps['ap']))}
    return History(cat['op'], cat['op_fn'], cat['origin'], cat['dest'], hour_block(cat['dep_local']).astype(np.int8),
                   cat['month'], cat['date'], cat['dep'], cat['arr'], cat['cxl'], cat['div'], vocab)


def _target(h: History, name: str):
    """Return (row mask, bin index) for a target."""
    if name == 'dep':
        m = (h.cancelled == 0) & (h.diverted == 0) & ~np.isnan(h.dep_delay)
        return m, np.searchsorted(DEP_EDGES, h.dep_delay[m], side='right') - 1
    if name == 'enr':
        m = (h.cancelled == 0) & (h.diverted == 0) & ~np.isnan(h.dep_delay) & ~np.isnan(h.arr_delay)
        return m, np.searchsorted(ENR_EDGES, (h.arr_delay - h.dep_delay)[m], side='right') - 1
    if name == 'cxl':
        m = np.ones(len(h), bool)
        return m, h.cancelled.astype(np.int64)
    if name == 'div':
        m = h.cancelled == 0
        return m, h.diverted[m].astype(np.int64)
    raise KeyError(name)


def _col_strings(h: History, col: str) -> list[str]:
    if col == 'hb':
        return [str(i) for i in range(6)]
    return h.vocab['ap' if col in ('origin', 'dest') else col]


def _key_codes(h: History, cols: tuple, mask) -> np.ndarray:
    if not cols:
        return np.zeros((int(mask.sum()), 0), np.int64)
    return np.stack([getattr(h, c)[mask].astype(np.int64) for c in cols], axis=1)


def _codes_to_keys(h: History, cols: tuple, uniq: np.ndarray) -> np.ndarray:
    if not cols:
        return np.array([''])
    tables = [_col_strings(h, c) for c in cols]
    return np.array(['|'.join(tables[j][code] for j, code in enumerate(row)) for row in uniq.tolist()])


class DelayModel:
    def __init__(self, counts: dict, alphas: dict, tails: dict, meta: dict, season: dict | None = None):
        self.counts = counts      # target -> list of (keys ndarray[str], counts ndarray[K,12,B])
        self.alphas = alphas      # part -> list of alpha per level (level 0 unused)
        self.season = season or {t: SEASON_FLOOR for t in LEVELS}  # target -> distant-month weight floor
        self.tails = tails        # target -> (lower-tail mean excess, upper-tail mean excess)
        self.meta = meta
        self._index = {t: [{k: i for i, k in enumerate(keys)} for keys, _ in lv] for t, lv in counts.items()}
        self._cache: dict = {}

    # ---------- fitting ----------
    @classmethod
    def fit(cls, h: History, meta: dict | None = None) -> 'DelayModel':
        counts, tails = {}, {}
        for t, levels in LEVELS.items():
            mask, y = _target(h, t)
            months = h.month[mask].astype(np.int64) - 1
            nb = len(EDGES[t]) - 1
            lv = []
            for cols in levels:
                codes = _key_codes(h, cols, mask)
                if codes.shape[1]:
                    uniq, inv = np.unique(codes, axis=0, return_inverse=True)
                    inv = inv.reshape(-1)
                else:
                    uniq, inv = np.zeros((1, 0), np.int64), np.zeros(len(y), np.int64)
                flat = (inv * 12 + months) * nb + y
                c = np.bincount(flat, minlength=len(uniq) * 12 * nb).reshape(len(uniq), 12, nb).astype(np.float32)
                lv.append((_codes_to_keys(h, cols, uniq), c))
            counts[t] = lv
            if t in ('dep', 'enr'):
                vals = h.dep_delay[mask] if t == 'dep' else (h.arr_delay - h.dep_delay)[mask]
                lo, hi = EDGES[t][1], EDGES[t][-2]
                below, above = vals[vals < lo], vals[vals >= hi]
                tails[t] = (float(np.mean(lo - below)) if len(below) else 5.0,
                            float(np.mean(above - hi)) if len(above) else 60.0)
        alphas = {part: [0.0] + [DEFAULT_ALPHA] * (len(lv) - 1) for t, lv in LEVELS.items() for part in PARTS[t]}
        days = h.date
        info = dict(meta or {})
        info.update(rows=int(len(h)), first_day=int(days.min()), last_day=int(days.max()),
                    months_present=sorted(set(int(m) for m in np.unique(h.month))))
        return cls(counts, alphas, tails, info)

    # ---------- inference ----------
    def _level_vectors(self, t: str, keys: list[str], w: np.ndarray):
        """Weighted count vectors for each level (None where the key is unseen)."""
        out = []
        for lvl, key in enumerate(keys):
            i = self._index[t][lvl].get(key)
            out.append(None if i is None else np.tensordot(w, self.counts[t][lvl][1][i], axes=(0, 0)))
        return out

    @staticmethod
    def keys_for(t: str, op, op_fn, origin, dest, hb) -> list[str]:
        vals = {'op': op, 'op_fn': op_fn, 'origin': origin, 'dest': dest, 'hb': str(hb)}
        return ['|'.join(vals[c] for c in cols) for cols in LEVELS[t]]

    def leg(self, op: str, op_fn: str, origin: str, dest: str, dep_local: int, month: int) -> 'LegForecast':
        key = (op, op_fn, origin, dest, int(hour_block(dep_local)), month)
        hit = self._cache.get(key)
        if hit is not None:
            return hit
        res = {}
        for t in LEVELS:
            w = season_weights(month, self.season[t])
            vecs = self._level_vectors(t, self.keys_for(t, *key[:5]), w)
            nb = len(EDGES[t]) - 1
            present = np.array([[v is not None for v in vecs]])
            mats = np.stack([v if v is not None else np.zeros(nb, np.float32) for v in vecs])[None]
            deepest = int(np.flatnonzero(present[0])[-1])
            res[t] = (self.predict_batch(t, mats, present)[0], float(mats[0, deepest].sum()), deepest)
        f = LegForecast(dep=res['dep'][0], enr=res['enr'][0], p_cancel=float(res['cxl'][0][1]),
                        p_divert=float(res['div'][0][1]), evidence_n=res['dep'][1],
                        evidence_level=LEVEL_NAMES['dep'][res['dep'][2]], cancel_n=res['cxl'][1],
                        tails=self.tails)
        self._cache[key] = f
        return f

    # ---------- tuning ----------
    def sample_rows(self, h: History, t: str, sample: int, rng) -> np.ndarray:
        idx = np.flatnonzero(_target(h, t)[0])
        if len(idx) > sample:
            idx = np.sort(idx[rng.choice(len(idx), sample, replace=False)])
        return idx

    def row_keys(self, h: History, t: str, idx: np.ndarray) -> np.ndarray:
        """Index of each row's key in the fitted counts, per level (-1 = unseen): (n, L)."""
        vocab = {c: _col_strings(h, c) for c in ('op', 'op_fn', 'origin', 'dest', 'hb')}
        cols_data = {c: getattr(h, c)[idx] for c in ('op', 'op_fn', 'origin', 'dest', 'hb')}
        out = np.full((len(idx), len(LEVELS[t])), -1, np.int64)
        for lvl, cols in enumerate(LEVELS[t]):
            index = self._index[t][lvl]
            for j in range(len(idx)):
                out[j, lvl] = index.get('|'.join(vocab[cn][int(cols_data[cn][j])] for cn in cols), -1)
        return out

    def rows_matrix(self, h: History, t: str, idx: np.ndarray, floor: float | None = None, keys=None):
        """Seasonally weighted count vectors per level for rows idx of h: (n, L, B) and presence (n, L)."""
        floor = self.season[t] if floor is None else floor
        keys = self.row_keys(h, t, idx) if keys is None else keys
        nb = len(EDGES[t]) - 1
        mats = np.zeros((len(idx), keys.shape[1], nb), np.float32)
        months = h.month[idx]
        for month in np.unique(months):
            w = season_weights(int(month), floor).astype(np.float32)
            sel = np.flatnonzero(months == month)
            for lvl in range(keys.shape[1]):
                k = keys[sel, lvl]
                ok = k >= 0
                if ok.any():
                    mats[sel[ok], lvl] = np.tensordot(self.counts[t][lvl][1][k[ok]], w, axes=(1, 0))
        return mats, keys >= 0

    def target_bins(self, h: History, t: str, idx: np.ndarray) -> np.ndarray:
        mask, y = _target(h, t)
        pos = np.cumsum(mask) - 1
        return y[pos[idx]]

    def predict_batch(self, t: str, mats, present, alphas: dict | None = None) -> np.ndarray:
        """Smoothed distributions for rows: mats (n, L, B) weighted counts, present (n, L)."""
        al = alphas or self.alphas
        if t != 'dep':
            return self.chain_batch(mats, present, al[t])
        S = DEP_SPLIT
        gate = self.chain_batch(np.stack([mats[..., :S].sum(-1), mats[..., S:].sum(-1)], -1), present, al['dep_gate'])
        lo = self.chain_batch(mats[..., :S], present, al['dep_lo'])
        hi = self.chain_batch(mats[..., S:], present, al['dep_hi'])
        return np.concatenate([gate[:, :1] * lo, gate[:, 1:] * hi], axis=1)

    @staticmethod
    def part_view(part: str, mats, y):
        """(mats, y, row mask) for one part of a target, so each part is tuned on its own likelihood."""
        S = DEP_SPLIT
        if part == 'dep_gate':
            return np.stack([mats[..., :S].sum(-1), mats[..., S:].sum(-1)], -1), (y >= S).astype(np.int64), None
        if part == 'dep_lo':
            m = y < S
            return mats[m][..., :S], y[m], m
        if part == 'dep_hi':
            m = y >= S
            return mats[m][..., S:], y[m] - S, m
        return mats, y, None

    @staticmethod
    def chain_batch(mats, present, alphas):
        p = mats[:, 0] + 1e-3
        p = p / p.sum(1, keepdims=True)
        for lvl in range(1, mats.shape[1]):
            v = mats[:, lvl]
            a = alphas[lvl]
            q = (v + a * p) / (v.sum(1, keepdims=True) + a)
            p = np.where(present[:, lvl:lvl + 1], q, p)
        return p

    def tune(self, valid: History, sample: int = 60_000, seed: int = 7) -> dict:
        """Empirical-Bayes tuning on a later period: seasonal floor, then per-level alphas by
        coordinate descent, maximizing held-out log-likelihood per target."""
        rng = np.random.default_rng(seed)
        report = {}
        for t in LEVELS:
            idx = self.sample_rows(valid, t, sample, rng)
            if not len(idx):
                continue
            y = self.target_bins(valid, t, idx)
            keys = self.row_keys(valid, t, idx)
            rows = np.arange(len(y))

            def ll_full(mats, present):
                p = self.predict_batch(t, mats, present)
                return float(np.mean(np.log(p[rows, y] + 1e-12)))
            by_floor = {}
            for fl in FLOOR_GRID:
                mats, present = self.rows_matrix(valid, t, idx, fl, keys)
                by_floor[fl] = (ll_full(mats, present), mats, present)
            floor = max(by_floor, key=lambda f: by_floor[f][0])
            _, mats, present = by_floor[floor]
            self.season[t] = floor
            for part in PARTS[t]:
                pm, py, m = self.part_view(part, mats, y)
                pp = present if m is None else present[m]
                if not len(py):
                    continue
                prow = np.arange(len(py))
                alphas = list(self.alphas[part])

                def ll(a):
                    p = self.chain_batch(pm, pp, a)
                    return float(np.mean(np.log(p[prow, py] + 1e-12)))
                best = ll(alphas)
                for _ in range(3):
                    improved = False
                    for lvl in range(1, len(alphas)):
                        for a_ in ALPHA_GRID:
                            trial = alphas.copy()
                            trial[lvl] = a_
                            sc = ll(trial)
                            if sc > best + 1e-6:
                                best, alphas, improved = sc, trial, True
                    if not improved:
                        break
                self.alphas[part] = alphas
                report[part] = dict(alphas=alphas, season_floor=floor, heldout_logloss=-best, n=int(len(py)))
        self._cache.clear()
        self.meta['tuning'] = report
        return report

    # ---------- persistence ----------
    def save(self, directory: str | Path) -> None:
        d = Path(directory)
        d.mkdir(parents=True, exist_ok=True)
        arrays = {}
        for t, lv in self.counts.items():
            for i, (keys, c) in enumerate(lv):
                arrays[f'{t}_{i}_keys'] = keys.astype(str)
                arrays[f'{t}_{i}_counts'] = c
        np.savez_compressed(d / 'delay_model.npz', **arrays)
        (d / 'delay_model.json').write_text(json.dumps(
            dict(alphas=self.alphas, tails=self.tails, meta=self.meta, season_sigma=SEASON_SIGMA,
                 season_floor=self.season, levels={t: [list(c) for c in v] for t, v in LEVELS.items()}), indent=2))

    @classmethod
    def load(cls, directory: str | Path) -> 'DelayModel':
        d = Path(directory)
        info = json.loads((d / 'delay_model.json').read_text())
        if info['levels'] != {t: [list(c) for c in v] for t, v in LEVELS.items()} or set(info['alphas']) != {
                p for v in PARTS.values() for p in v}:
            raise ValueError('Saved delay model uses a different level layout; run `flightopt build` again')
        with np.load(d / 'delay_model.npz', allow_pickle=False) as z:
            counts = {t: [(z[f'{t}_{i}_keys'], z[f'{t}_{i}_counts']) for i in range(len(LEVELS[t]))] for t in LEVELS}
        tails = {k: tuple(v) for k, v in info['tails'].items()}
        return cls(counts, info['alphas'], tails, info['meta'], info.get('season_floor'))


LEVEL_NAMES = {
    'dep': ['all flights', 'carrier', 'carrier at origin', 'carrier at origin in this time block', 'this flight'],
}


def sample_from_bins(probs: np.ndarray, edges: np.ndarray, tails: tuple, u_bin, u_pos) -> np.ndarray:
    """Inverse-CDF sampling: pick a bin, then a point uniform inside it (exponential tails)."""
    cdf = np.cumsum(probs)
    cdf[-1] = 1.0
    b = np.minimum(np.searchsorted(cdf, u_bin, side='right'), len(probs) - 1)
    lo, hi = edges[b], edges[b + 1]
    out = np.empty(len(u_bin))
    mid = np.isfinite(lo) & np.isfinite(hi)
    out[mid] = lo[mid] + u_pos[mid] * (hi[mid] - lo[mid])
    low = ~np.isfinite(lo)
    out[low] = hi[low] + np.log(np.maximum(u_pos[low], 1e-12)) * tails[0]
    high = ~np.isfinite(hi)
    out[high] = lo[high] - np.log(np.maximum(u_pos[high], 1e-12)) * tails[1]
    return out


def bin_means(probs: np.ndarray, edges: np.ndarray, tails: tuple) -> float:
    lo, hi = edges[:-1].copy(), edges[1:].copy()
    mids = np.where(np.isfinite(lo) & np.isfinite(hi), (lo + hi) / 2, 0.0)
    mids[0] = edges[1] - tails[0]
    mids[-1] = edges[-2] + tails[1]
    return float(probs @ mids)


@dataclass
class LegForecast:
    dep: np.ndarray
    enr: np.ndarray
    p_cancel: float
    p_divert: float
    evidence_n: float
    evidence_level: str
    cancel_n: float
    tails: dict

    @property
    def p_dep15(self) -> float:
        return float(self.dep[DEP_EDGES[:-1] >= 15].sum())

    @property
    def mean_arr_delay(self) -> float:
        return bin_means(self.dep, DEP_EDGES, self.tails['dep']) + bin_means(self.enr, ENR_EDGES, self.tails['enr'])

    def p_arr_at_least(self, minutes: float, n: int = 4000, seed: int = 11) -> float:
        rng = np.random.default_rng(seed)
        d = sample_from_bins(self.dep, DEP_EDGES, self.tails['dep'], rng.random(n), rng.random(n))
        e = sample_from_bins(self.enr, ENR_EDGES, self.tails['enr'], rng.random(n), rng.random(n))
        return float(np.mean(d + e >= minutes))
