"""Paper-trade replay on the recorder's live data (ms timestamps, real books, websocket fills).

Strategies decide on a 100 ms grid using only events received at or before the decision time.
Takers fill against the recorded top of book (and top-5 depth) at decision + latency.
Makers post bids that must not cross the book; they fill only when a real websocket print trades
through the quote (taker SELL of the outcome below q, or taker BUY of the mirror above 1 - q).
Fee per share for takers: 0.07 * p * (1 - p). Makers pay none.

Usage:
  python3 -I live.py calibrate
  python3 -I live.py run <strategy> key=value ...
"""

import bisect
import glob
import gzip
import json
import math
import os
import statistics
import sys
from collections import defaultdict

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
LIVE = os.path.join(HERE, "data", "live")


def load_rows():
    rows = []
    for f in sorted(glob.glob(os.path.join(LIVE, "*.jsonl*"))) + glob.glob(os.path.join(LIVE, "..", "labels", "*.jsonl")):
        op = gzip.open if f.endswith(".gz") else open
        with op(f, "rt") as fh:
            for line in fh:
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    pass  # partially written last line
    rows.sort(key=lambda r: r["t"])
    return rows


ASSETS = ["btc", "eth", "sol", "xrp", "doge", "bnb", "hype"]
LAGS = {"hype": 6}   # Binance -> Chainlink-average lag in seconds, calibrated per asset (default 4)


def fee(p):
    return 0.07 * p * (1 - p)


def phi(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def num(v):
    if isinstance(v, str):
        return int(v) / 1e18 if v.isdigit() and len(v) > 12 else float(v)
    return float(v)


class Live:
    def __init__(self, lag=None, delta_n=12, sigma_halflife=600, source="bn", asset="btc", rows=None):
        lag = LAGS.get(asset, 4) if lag is None else lag
        self.lag, self.source, self.asset = lag, source, asset
        rows = [r for r in (rows if rows is not None else load_rows()) if r.get("as", "btc") == asset]
        spot_t, spot_v = [], []
        self.twap = []          # (recv_ms, ts_s, value)
        self.labels, self.windows = {}, {}
        self.tob = defaultdict(list)    # (w, a) -> [(t, bb, bbs, ba, bas)]
        self.depth = defaultdict(list)  # (w, a) -> [(t, bids, asks)]
        self.trades = defaultdict(list) # w -> [(ts_ms, a, price, size, side)]
        mix = {}
        for r in rows:
            k = r["k"]
            if source == "mix" and k in ("bn", "cb"):
                mix.setdefault(k, ([], []))
                mix[k][0].append(r["t"]); mix[k][1].append((num(r["b"]) + num(r["a"])) / 2)
            if k == source and source in ("bn", "cb", "kr"):
                spot_t.append(r["t"]); spot_v.append((num(r["b"]) + num(r["a"])) / 2)
            elif k == "cl" and source == "cl":
                spot_t.append(r["t"]); spot_v.append(num(r["v"]))
            elif k == "twap":
                self.twap.append((r["t"], r["ts"] // 1000, num(r["v"])))
            elif k == "label":
                self.labels[r["w"]] = (r["open"], r["close"])
            elif k == "window":
                self.windows[r["w"]] = r
            elif k == "tob":
                self.tob[(r["w"], r["a"])].append((r["t"], r["bb"], r["bbs"], r["ba"], r["bas"]))
            elif k == "depth":
                self.depth[(r["w"], r["a"])].append((r["t"], r["bids"], r["asks"]))
            elif k == "trade":
                self.trades[r["w"]].append((r["ts"], r["a"], r["p"], r["s"], r["sd"]))
        for v in self.trades.values():
            v.sort()
        if source == "mix" and len(mix) == 2:
            # merge: at every update of either feed, value = mean of latest bn and latest cb mids
            ev = sorted([(t, 0, v) for t, v in zip(*mix["bn"])] + [(t, 1, v) for t, v in zip(*mix["cb"])])
            last = [None, None]
            for t, i, v in ev:
                last[i] = v
                if last[0] is not None and last[1] is not None:
                    spot_t.append(t); spot_v.append((last[0] + last[1]) / 2)
        self.spot_t = np.array(spot_t, dtype=np.int64)
        self.spot_v = np.array(spot_v)
        # per-second spot (value at end of each second, by receive time)
        s0, s1 = int(self.spot_t[0] // 1000), int(self.spot_t[-1] // 1000)
        self.s0 = s0
        idx = np.searchsorted(self.spot_t, (np.arange(s0, s1 + 1) + 1) * 1000, side="left") - 1
        idx = np.clip(idx, 0, len(self.spot_v) - 1)
        self.bsec = self.spot_v[idx]
        self.cb = np.concatenate([[0.0], np.cumsum(self.bsec)])
        d = np.diff(self.bsec, prepend=self.bsec[0])
        a = 1 - 0.5 ** (1 / sigma_halflife)
        var, acc = np.empty(len(d)), float(np.var(d[:600])) if len(d) > 600 else 25.0
        for i in range(len(d)):
            acc = (1 - a) * acc + a * d[i] * d[i]
            var[i] = acc
        self.sigma = np.sqrt(var)
        # delta points: TWAP(T) - mean spot over (T-lag-60, T-lag]
        self.twap.sort()
        self.d_recv, self.d_val, raw = [], [], []
        for recv, T, v in self.twap:
            e = T - lag
            if e - 60 < s0 or e > s1:
                continue
            raw.append(v - self.bmean(e))
            self.d_recv.append(recv)
            self.d_val.append(float(np.median(raw[-delta_n:])))
        self.raw_delta = raw
        # K and F straight from the TWAP stream (exactly the official open/close); labels cross-check.
        tw_at = {T: v for _, T, v in self.twap}
        self.K = {w: tw_at[w] for w in self.windows if w in tw_at}
        self.F = {w: tw_at[w + 300] for w in self.windows if w + 300 in tw_at}
        for w, (o, c) in self.labels.items():
            self.K.setdefault(w, o); self.F.setdefault(w, c)
        res = np.array(raw[1:]) - np.array(self.d_val[:-1]) if len(raw) > 30 else np.array([0.0])
        self.meas = float(np.std(res)) if len(res) > 30 else None   # calibrated measurement error (price units)
        self.tob_t = {k: [x[0] for x in v] for k, v in self.tob.items()}
        self.depth_t = {k: [x[0] for x in v] for k, v in self.depth.items()}

    def bmean(self, end, length=60):
        i = end - self.s0 + 1
        return (self.cb[i] - self.cb[i - length]) / length

    def spot_at(self, r):
        i = bisect.bisect_right(self.spot_t, r) - 1
        return float(self.spot_v[i]) if i >= 0 else None

    def delta_at(self, r):
        i = bisect.bisect_right(self.d_recv, r) - 1
        return self.d_val[i] if i >= 0 else None

    def book(self, w, a, r):
        key = (w, a)
        i = bisect.bisect_right(self.tob_t.get(key, []), r) - 1
        return self.tob[key][i] if i >= 0 else None

    def depth_at(self, w, a, r):
        key = (w, a)
        i = bisect.bisect_right(self.depth_t.get(key, []), r) - 1
        return self.depth[key][i] if i >= 0 else None

    def fair_fast(self, w, r, sig=1.0, meas=1.0, step=100):
        """Cached fair value on a 100 ms grid (floor of r to the grid; uses info at the grid time)."""
        key = (w, sig, meas)
        g = self.__dict__.setdefault("_fg", {}).get(key)
        if g is None:
            r0 = (w - 120) * 1000
            vals = []
            for r_ in range(r0, (w + 305) * 1000, step):
                f = self.fair(w, r_, sig, meas)
                vals.append(f[0] if f else np.nan)
            g = (r0, np.array(vals))
            self._fg[key] = g
        r0, arr = g
        i = (r - r0) // step
        if i < 0 or i >= len(arr) or np.isnan(arr[i]):
            return None
        return (float(arr[i]), None)

    def fair(self, w, r, sig=1.0, meas=1.0):
        """meas multiplies the calibrated measurement error (self.meas)."""
        K = self.K.get(w)
        if self.meas is None or K is None:
            return None
        meas = meas * self.meas
        L = self.lag
        E1 = w + 300 - L
        t = r / 1000
        s_now = int(t)
        bnow = self.spot_at(r)
        delta = self.delta_at(r)
        if bnow is None or delta is None or s_now - 1 - self.s0 < 0 or s_now - self.s0 >= len(self.bsec):
            return None
        if s_now > E1 and E1 - self.s0 >= len(self.bsec):
            return None
        sigma = self.sigma[min(s_now - 1 - self.s0, len(self.sigma) - 1)] * sig
        if s_now < E1 - 60:
            m = bnow + delta
            v = sigma ** 2 * ((E1 - 60 - t) + 20.5)
        elif s_now <= E1:
            known = self.cb[s_now - 1 - self.s0 + 1] - self.cb[E1 - 60 - self.s0 + 1] if s_now - 1 > E1 - 60 else 0.0
            tau = E1 - (s_now - 1)
            m = (known + tau * bnow) / 60 + delta
            v = sigma ** 2 * tau * (tau + 1) * (2 * tau + 1) / (6 * 3600)
        else:
            m = self.bmean(E1) + delta
            v = 0.0
        sd = math.sqrt(v + meas ** 2)
        return phi((m - K) / sd), m


# ---------------------------------------------------------------- strategies
def taker_value(L, w, P):
    """Buy the outcome whose fair value beats the live ask by > edge + fee."""
    out = []
    S = w * 1000
    for r in range(S + int(P.get("t_from", 0) * 1000), S + int(P.get("t_to", 300) * 1000), int(P.get("step_ms", 100))):
        f = L.fair_fast(w, r, P.get("sig", 1.0), P.get("meas", 1.0))
        if not f:
            continue
        p_up = f[0]
        for a, fp in ((0, p_up), (1, 1 - p_up)):
            bk = L.book(w, a, r)
            if not bk or bk[3] is None:
                continue
            ask = bk[3]
            if not (P.get("pmin", 0.02) <= ask <= P.get("pmax", 0.99)):
                continue
            if fp - ask - fee(ask) > P.get("edge", 0.02):
                out.append((r, a, min(0.999, fp - fee(ask) - P.get("edge", 0.02) / 2), P.get("shares", 100), fp))
                if len(out) >= P.get("max_trades", 1):
                    return out
                break
    return out


def maker_value(L, w, P):
    """Post bids at fair - margin on both outcomes (no crossing), requote every requote_ms."""
    out = []  # (post_ms, cancel_ms, a, q, shares, fair)
    S = w * 1000
    step = int(P.get("requote_ms", 1000))
    for r in range(S + int(P.get("t_from", 5) * 1000), S + int(P.get("t_to", 295) * 1000), step):
        f = L.fair_fast(w, r, P.get("sig", 1.0), P.get("meas", 1.0))
        if not f:
            continue
        for a, fp in ((0, f[0]), (1, 1 - f[0])):
            q = math.floor((fp - P.get("margin", 0.03)) * 100) / 100
            bk = L.book(w, a, r)
            if not bk or bk[3] is None or q >= bk[3]:
                continue
            if P.get("join_only", 0) and bk[1] is not None and q < bk[1]:
                continue
            if P.get("pmin", 0.05) <= q <= P.get("pmax", 0.95):
                out.append((r, r + step, a, round(q, 2), P.get("shares", 50), fp))
    return out


def taker_snipe(L, w, P):
    """After a fast fair-value jump (look_ms window), take the stale ask on the side the jump favours."""
    out, last = [], -10 ** 12
    S = w * 1000
    look, jump = int(P.get("look_ms", 1000)), P.get("jump", 0.04)
    for r in range(S + int(P.get("t_from", 5) * 1000), S + int(P.get("t_to", 299) * 1000), int(P.get("step_ms", 100))):
        if r - last < P.get("gap_ms", 5000):
            continue
        f1 = L.fair_fast(w, r, P.get("sig", 1.0), P.get("meas", 1.0))
        f0 = L.fair_fast(w, r - look, P.get("sig", 1.0), P.get("meas", 1.0))
        if not f0 or not f1:
            continue
        d = f1[0] - f0[0]
        if abs(d) < jump:
            continue
        a, fp = (0, f1[0]) if d > 0 else (1, 1 - f1[0])
        bk = L.book(w, a, r)
        if not bk or bk[3] is None or not (P.get("pmin", 0.03) <= bk[3] <= P.get("pmax", 0.97)):
            continue
        ask = bk[3]
        if fp - ask - fee(ask) > P.get("edge", 0.02):
            out.append((r, a, min(0.999, fp - fee(ask) - P.get("edge", 0.02) / 2), P.get("shares", 100), fp))
            last = r
            if len(out) >= P.get("max_trades", 3):
                break
    return out


def snipe_dir(L, w, P):
    """Direction-only sniper: after a fair-value jump, buy the favoured side if its ask is within
    `slack` of the decision-time mid (limit is relative to the market, not to the model's level)."""
    out, last = [], -10 ** 12
    S = w * 1000
    look, jump = int(P.get("look_ms", 1000)), P.get("jump", 0.04)
    for r in range(S + int(P.get("t_from", 5) * 1000), S + int(P.get("t_to", 299) * 1000), int(P.get("step_ms", 100))):
        if r - last < P.get("gap_ms", 5000):
            continue
        f1 = L.fair_fast(w, r, P.get("sig", 1.0), P.get("meas", 1.0))
        f0 = L.fair_fast(w, r - look, P.get("sig", 1.0), P.get("meas", 1.0))
        if not f0 or not f1 or abs(f1[0] - f0[0]) < jump:
            continue
        a = 0 if f1[0] > f0[0] else 1
        bk = L.book(w, a, r)
        if not bk or bk[1] is None or bk[3] is None:
            continue
        mid = (bk[1] + bk[3]) / 2
        if not (P.get("pmin", 0.10) <= mid <= P.get("pmax", 0.90)):
            continue
        out.append((r, a, min(0.999, mid + P.get("slack", 0.02)), P.get("shares", 100), f1[0] if a == 0 else 1 - f1[0]))
        last = r
        if len(out) >= P.get("max_trades", 3):
            break
    return out


STRATS = {"taker_value": taker_value, "maker_value": maker_value, "taker_snipe": taker_snipe, "taker_dir": snipe_dir}


def run(L, name, P, lat_ms=250):
    fills = []
    for w in sorted(L.F):
        if w not in L.windows or (w, 0) not in L.tob or w not in L.K:
            continue
        K, F = L.K[w], L.F[w]
        up = F >= K
        if name.startswith("taker"):
            for (r, a, limit, shares, fp) in STRATS[name](L, w, P):
                bk = L.book(w, a, r + lat_ms)
                if not bk or bk[3] is None or bk[3] > limit:
                    continue
                left, cost, got = shares, 0.0, 0.0
                dp = L.depth_at(w, a, r + lat_ms)
                levels = [[bk[3], bk[4] or 0]]
                if dp and abs(dp[0] - (r + lat_ms)) < 1500:
                    levels += [lv for lv in dp[2] if lv[0] > bk[3]]
                for price, size in levels:
                    if price > limit or left <= 0:
                        break
                    q = min(left, size)
                    got += q; cost += q * (price + fee(price)); left -= q
                if got <= 0:
                    continue
                won = (a == 0) == up
                fills.append({"w": w, "t": (r - w * 1000) / 1000, "a": a, "price": round(cost / got, 4), "q": got,
                              "won": won, "pnl": got * (1.0 if won else 0.0) - cost, "fair": fp})
        else:
            quotes = STRATS[name](L, w, P)
            tr = L.trades.get(w, [])
            tts = [x[0] for x in tr]
            pos = [0.0, 0.0]
            cap = P.get("cap", 300)
            for (post, cancel, a, q, shares, fp) in quotes:
                j = bisect.bisect_right(tts, post + lat_ms)
                filled = 0.0
                while j < len(tr) and tr[j][0] <= cancel + lat_ms and filled < shares and pos[a] < cap:
                    ts, ta, price, size, side = tr[j]
                    hit = (ta == a and side == "SELL" and price < q) or (ta == 1 - a and side == "BUY" and price > 1 - q)
                    if hit:
                        x = min(size, shares - filled, cap - pos[a])
                        filled += x; pos[a] += x
                    j += 1
                if filled > 0:
                    won = (a == 0) == up
                    fills.append({"w": w, "t": (post - w * 1000) / 1000, "a": a, "price": q, "q": filled, "won": won,
                                  "pnl": filled * ((1.0 if won else 0.0) - q), "fair": fp})
    return fills


def summarize(fills, nwin):
    if not fills:
        return {"trades": 0, "of_windows": nwin}
    pnl = np.array([f["pnl"] for f in fills]); q = np.array([f["q"] for f in fills])
    by_w, by_t = defaultdict(float), defaultdict(float)
    for f in fills:
        by_w[(f.get("as", "btc"), f["w"])] += f["pnl"]
        by_t[f["w"]] += f["pnl"]
    wp = np.array(list(by_w.values()))
    tp = np.array(list(by_t.values()))
    return {"trades": len(fills), "windows_traded": len(by_w), "of_windows": nwin,
            "win_rate": round(float(np.mean([f["won"] for f in fills])), 3),
            "avg_price": round(float(np.average([f["price"] for f in fills], weights=q)), 3),
            "shares": round(float(q.sum()), 1), "pnl_total": round(float(pnl.sum()), 2),
            "pnl_per_share": round(float(pnl.sum() / q.sum()), 4),
            "t_stat": round(float(wp.mean() / (wp.std(ddof=1) / math.sqrt(len(wp)))), 2) if len(wp) > 2 and wp.std() > 0 else None,
            "time_slots": len(tp),
            "t_time": round(float(tp.mean() / (tp.std(ddof=1) / math.sqrt(len(tp)))), 2) if len(tp) > 2 and tp.std() > 0 else None}


def parse(args):
    P = {}
    for a in args:
        k, v = a.split("=", 1)
        P[k] = float(v) if any(c in v for c in ".e") else int(v)
    return P


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "calibrate":
        for src in ("bn", "cb", "kr", "cl"):
            for lag in range(0, 8):
                try:
                    L = Live(lag=lag, source=src)
                except Exception as e:
                    print(src, lag, "error", e); break
                r = np.array(L.raw_delta)
                res = r[1:] - np.array(L.d_val[:-1])
                print(f"source={src} lag={lag} n={len(res)} median|res|={np.median(np.abs(res)):.3f} std={res.std():.3f} p99={np.percentile(np.abs(res), 99):.3f}")
    elif cmd == "run":
        P = parse(sys.argv[3:])
        L = Live(lag=P.get("lag", 5), source={0: "bn", 1: "cb", 2: "kr", 3: "cl"}[P.get("src", 0)])
        n = len([w for w in L.labels if w in L.windows])
        print(json.dumps(summarize(run(L, sys.argv[2], P, P.get("lat_ms", 250)), n)))
