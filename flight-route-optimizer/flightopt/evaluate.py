"""Time-split backtest of the leg-performance model.

Train on months before the validation month, tune smoothing on the validation month, refit
on train+validation with those settings, and score the final month, which the model never
saw. Baselines use the same data. When the data is synthetic, the generating probabilities
give an oracle reference.
"""
from __future__ import annotations

import csv
import datetime as dt
import gzip
from pathlib import Path

import numpy as np

from .delay_model import DEP_EDGES, ENR_EDGES, LEVELS, PARTS, DelayModel, load_history, sample_from_bins

BIG = 1e9


def _day(s: str) -> int:
    return (dt.date.fromisoformat(s) - dt.date(1970, 1, 1)).days


def _logloss(p, y):
    return float(-np.mean(np.log(np.clip(p[np.arange(len(y)), y], 1e-12, 1))))


def _brier(p, y):
    return float(np.mean((p - y) ** 2))


def _calibration(p, y, bins=10):
    order = np.argsort(p)
    out = []
    for chunk in np.array_split(order, bins):
        if len(chunk):
            out.append(dict(mean_predicted=round(float(p[chunk].mean()), 4), observed=round(float(y[chunk].mean()), 4),
                            n=int(len(chunk))))
    return out


def _arr15_matrix(n=400, seed=5) -> np.ndarray:
    """M[i, j] = P(dep in bin i + enr in bin j >= 15) under the model's within-bin sampling."""
    rng = np.random.default_rng(seed)
    nd, ne = len(DEP_EDGES) - 1, len(ENR_EDGES) - 1
    M = np.zeros((nd, ne))
    for i in range(nd):
        pd = np.zeros(nd)
        pd[i] = 1
        d = sample_from_bins(pd, DEP_EDGES, (6.0, 120.0), rng.random(n), rng.random(n))
        for j in range(ne):
            pe = np.zeros(ne)
            pe[j] = 1
            e = sample_from_bins(pe, ENR_EDGES, (8.0, 25.0), rng.random(n), rng.random(n))
            M[i, j] = np.mean(d + e >= 15)
    return M


def _naive(mats, present, min_n=20):
    """Most specific level with at least min_n observations, unsmoothed."""
    p = mats[:, 0] + 1e-3
    for lvl in range(1, mats.shape[1]):
        n = mats[:, lvl].sum(1)
        use = present[:, lvl] & (n >= min_n)
        p = np.where(use[:, None], mats[:, lvl], p)
    p = p + 1e-3
    return p / p.sum(1, keepdims=True)


def backtest(con, truth: str | Path | None = None, sample: int = 80_000, seed: int = 3) -> dict:
    periods = [r[0] for r in con.execute('SELECT DISTINCT period FROM flights ORDER BY period')]
    if len(periods) < 3:
        raise ValueError(f'Backtest needs at least 3 monthly periods; found {periods}')
    test_p, valid_p, train_ps = periods[-1], periods[-2], periods[:-2]
    h = load_history(con)
    day_of = lambda p: _day(p + '-01')
    t_valid, t_test = day_of(valid_p), day_of(test_p)
    train = h.subset(h.date < t_valid)
    valid = h.subset((h.date >= t_valid) & (h.date < t_test))
    test = h.subset(h.date >= t_test)
    m1 = DelayModel.fit(train)
    tuning = m1.tune(valid)
    m = DelayModel.fit(h.subset(h.date < t_test))
    m.alphas = {t: list(v) for t, v in m1.alphas.items()}
    m.season = dict(m1.season)
    rng = np.random.default_rng(seed)
    report = dict(train_periods=train_ps, valid_period=valid_p, test_period=test_p,
                  train_rows=len(train), valid_rows=len(valid), test_rows=len(test), tuning=tuning, targets={})
    def fixed(t, depth):
        return {p: list(m.alphas[p][:depth]) + [BIG] * (len(LEVELS[t]) - depth) for p in PARTS[t]}
    variants = {
        'global_rate': lambda t, M, P: m.predict_batch(t, M, P, fixed(t, 1)),
        'carrier_only': lambda t, M, P: m.predict_batch(t, M, P, fixed(t, 2)),
        'naive_backoff_n20': lambda t, M, P: _naive(M, P),
        'hierarchical_model': lambda t, M, P: m.predict_batch(t, M, P),
    }
    store = {}
    for t, event_from in (('dep', 15), ('cxl', None)):
        idx = m.sample_rows(test, t, sample, rng)
        M, P = m.rows_matrix(test, t, idx)
        y = m.target_bins(test, t, idx)
        res = {}
        for name, fn in variants.items():
            p = fn(t, M, P)
            if t == 'dep':
                ev = p[:, DEP_EDGES[:-1] >= event_from].sum(1)
                yb = (DEP_EDGES[y] >= event_from).astype(float)
            else:
                ev, yb = p[:, 1], y.astype(float)
            res[name] = dict(logloss=round(_logloss(p, y), 5), brier=round(_brier(ev, yb), 5))
            store[(t, name)] = (idx, ev, yb)
        _, ev, yb = store[(t, 'hierarchical_model')]
        res['calibration'] = _calibration(ev, yb)
        res['event'] = 'departure delay >= 15 min (operated flights)' if t == 'dep' else 'cancellation'
        res['n'] = int(len(idx))
        report['targets'][t] = res
    # arrival >= 15 via dep x en-route composition on rows where both are observed
    idx = m.sample_rows(test, 'enr', sample, rng)
    Md, Pd = m.rows_matrix(test, 'dep', idx)
    Me, Pe = m.rows_matrix(test, 'enr', idx)
    pd = m.predict_batch('dep', Md, Pd)
    pe = m.predict_batch('enr', Me, Pe)
    A = _arr15_matrix()
    p_arr = np.einsum('ni,ij,nj->n', pd, A, pe)
    y_arr = (test.arr_delay[idx] >= 15).astype(float)
    base = float(np.mean((train.arr_delay[~np.isnan(train.arr_delay)] >= 15)))
    report['targets']['arr15'] = dict(event='arrival delay >= 15 min (operated flights)', n=int(len(idx)),
                                      hierarchical_model=dict(brier=round(_brier(p_arr, y_arr), 5)),
                                      global_rate=dict(brier=round(_brier(np.full(len(idx), base), y_arr), 5)),
                                      calibration=_calibration(p_arr, y_arr))
    if truth:
        report['oracle'] = _oracle(test, store, truth)
    return report


def _oracle(test, store, truth_path) -> dict:
    want = {}
    for t in ('dep', 'cxl'):
        idx = store[(t, 'hierarchical_model')][0]
        for j in idx:
            want[(int(test.date[j]), test.vocab['op'][test.op[j]], test.vocab['op_fn'][test.op_fn[j]],
                  test.vocab['ap'][test.origin[j]])] = None
    with gzip.open(truth_path, 'rt', encoding='utf-8') as f:
        for r in csv.DictReader(f):
            k = (_day(r['flight_date']), r['op'], r['op_fn'], r['origin'])
            if k in want:
                want[k] = (float(r['p_dep15']), float(r['p_cancel']),
                           float(r.get('p_dep15_structural') or r['p_dep15']),
                           float(r.get('p_cancel_structural') or r['p_cancel']))
    out = {}
    for t, col in (('dep', 0), ('cxl', 1)):
        idx = store[(t, 'hierarchical_model')][0]
        rows = [want[(int(test.date[j]), test.vocab['op'][test.op[j]], test.vocab['op_fn'][test.op_fn[j]],
                      test.vocab['ap'][test.origin[j]])] for j in idx]
        day_truth = np.array([r[col] for r in rows])
        struct = np.array([r[col + 2] for r in rows])
        _, ev, yb = store[(t, 'hierarchical_model')]
        _, ev_g, _ = store[(t, 'global_rate')]
        b_model, b_global = _brier(ev, yb), _brier(ev_g, yb)
        b_day, b_struct = _brier(day_truth, yb), _brier(struct, yb)
        share = lambda ref: round((b_global - b_model) / (b_global - ref), 3) if b_global > ref else None
        out[t] = dict(
            brier_model=round(b_model, 5), brier_global=round(b_global, 5),
            brier_structural_oracle=round(b_struct, 5), brier_day_oracle=round(b_day, 5),
            share_of_structural_gain=share(b_struct), share_of_day_oracle_gain=share(b_day),
            mae_vs_structural_probability=round(float(np.mean(np.abs(ev - struct))), 4),
            note='Structural oracle = true probability averaged over the unknown weather state: the best possible '
                 'booking-time forecast. Day oracle also knows that day\'s weather, which needs same-day data.')
    return out
