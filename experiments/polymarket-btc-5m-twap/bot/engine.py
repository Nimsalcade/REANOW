"""Replay engine for BTC 5-minute Up/Down paper strategies on backfilled history.

Fair value (TWAP-aware): Up iff TWAP60(E) >= TWAP60(S) = K. Spot proxy is the
Binance BTCUSDT 1 s close b(t); Chainlink's level is b + delta, where delta is
calibrated causally from official TWAP points published at least PUB_DELAY s ago.

Fills: a taker buy of outcome o at time t with limit c fills at the price of the
first real BUY trade on o in [t + latency, t + latency + FILL_WINDOW] whose price
<= c, for at most that trade's size. Fee per share = 0.07 * p * (1 - p)
(recovered from Polymarket's web bundle: shares * rate * (p(1-p))^exponent).
"""

import bisect
import glob
import json
import math
import os
import sys
from dataclasses import dataclass, field

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
HIST = os.path.join(HERE, "data", "hist" if os.environ.get("ASSET", "btc") == "btc" else "hist_" + os.environ["ASSET"])
FEE_RATE, FEE_EXP = 0.07, 1
PUB_DELAY = 2      # seconds before an official TWAP point is assumed visible
FILL_WINDOW = 3    # seconds a resting taker intent may match a real print
FILL_SHIFT = 4     # data-api trade timestamps lag the real match by ~1.7-2.6 s (+truncation);
                   # a print only counts as fillable if api_ts - FILL_SHIFT >= decision + latency


def fee(p):
    return FEE_RATE * (p * (1 - p)) ** FEE_EXP


def phi(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


# ---------------------------------------------------------------- data
@dataclass
class Window:
    w: int
    K: float
    F: float
    up: bool
    trades: list = field(default_factory=list)  # (ts, oi, side, price, size)


class Data:
    def __init__(self, lag=5, delta_n=12, sigma_halflife=600):
        self.lag, self.delta_n, self.sigma_halflife = lag, delta_n, sigma_halflife
        ev = [json.loads(l) for l in open(os.path.join(HIST, "events.jsonl"))]
        rows = []
        for f in glob.glob(os.path.join(HIST, "binance1s", "*.json")):
            rows.extend(json.load(open(f)))
        rows.sort()
        ts = np.array([r[0] // 1000 for r in rows], dtype=np.int64)
        close = np.array([r[4] for r in rows])
        self.t0 = int(ts[0])
        n = int(ts[-1]) - self.t0 + 1
        b = np.full(n, np.nan)
        b[ts - self.t0] = close
        # forward fill
        idx = np.where(~np.isnan(b), np.arange(n), 0)
        np.maximum.accumulate(idx, out=idx)
        self.b = b[idx]
        self.cb = np.concatenate([[0.0], np.cumsum(self.b)])  # prefix sums
        # sigma ($ per sqrt s), causal EWMA of squared 1 s changes
        d = np.diff(self.b, prepend=self.b[0])
        a = 1 - 0.5 ** (1 / sigma_halflife)
        var = np.empty(n)
        acc = (np.nanstd(d[:3600]) or 5.0) ** 2
        for i in range(n):
            acc = (1 - a) * acc + a * d[i] * d[i]
            var[i] = acc
        self.sigma = np.sqrt(var)
        # official TWAP points
        tw = {}
        for f in glob.glob(os.path.join(HIST, "twap5s", "*.json")):
            for t_ms, v in json.load(open(f)):
                tw[t_ms // 1000] = v
        self.tw_t = np.array(sorted(tw), dtype=np.int64)
        self.tw_v = np.array([tw[t] for t in self.tw_t])
        self._build_delta()
        self.windows = []
        for e in ev:
            if e["open"] is None or e["close"] is None:
                continue
            path = os.path.join(HIST, "trades", "%d.json" % e["w"])
            trades = [tuple(x) for x in json.load(open(path))] if os.path.exists(path) else []
            if e["w"] - 600 < self.t0 or e["w"] + 400 > self.t0 + n:
                continue
            # need official TWAP points shortly before and throughout the window (fresh delta)
            lo = bisect.bisect_left(self.tw_t, e["w"] - 120)
            hi = bisect.bisect_right(self.tw_t, e["w"] + 300)
            if hi - lo < 50:
                continue
            self.windows.append(Window(e["w"], e["open"], e["close"], e["close"] >= e["open"], trades))

    def bmean(self, end, length=60):
        """mean of b over (end-length, end]"""
        i = end - self.t0 + 1
        return (self.cb[i] - self.cb[i - length]) / length

    def _build_delta(self):
        L = self.lag
        ok = (self.tw_t - L - 60 - self.t0 >= 0) & (self.tw_t - self.t0 < len(self.b))
        self.tw_t, self.tw_v = self.tw_t[ok], self.tw_v[ok]
        bm = np.array([self.bmean(int(t) - L) for t in self.tw_t])
        self.raw_delta = self.tw_v - bm
        # causal rolling median of the last delta_n points
        n = self.delta_n
        self.delta_pts = np.array([np.median(self.raw_delta[max(0, i - n + 1):i + 1]) for i in range(len(self.raw_delta))])
        r = self.raw_delta[1:] - self.delta_pts[:-1]
        self.meas = float(np.std(r)) if len(r) > 30 else 1.2   # calibrated measurement error, price units

    def delta_at(self, t):
        """delta usable at time t (points with ts <= t - PUB_DELAY)."""
        i = bisect.bisect_right(self.tw_t, t - PUB_DELAY) - 1
        return float(self.delta_pts[i]) if i >= 0 else 0.0

    def twap_at(self, t):
        i = bisect.bisect_right(self.tw_t, t - PUB_DELAY) - 1
        return (int(self.tw_t[i]), float(self.tw_v[i])) if i >= 0 else (None, None)

    def calibration(self):
        """Residual of TWAP vs b-mean + causal delta (previous point's median)."""
        r = self.raw_delta[1:] - self.delta_pts[:-1]
        return float(np.median(np.abs(r))), float(np.std(r)), float(np.percentile(np.abs(r), 99))


# ---------------------------------------------------------------- model
def fair(data, win, t, sig_mult=1.0, meas=None, fat=0.0):
    """P(Up) at integer second t (S <= t), using info available at t. meas defaults to calibration."""
    meas = data.meas if meas is None else meas
    L = data.lag
    E1 = win.w + 300 - L          # last binance second inside the final average
    i = t - data.t0
    bt = data.b[i]
    sigma = data.sigma[i] * sig_mult
    delta = data.delta_at(t)
    if t < E1 - 60:
        m = bt + delta
        v = sigma ** 2 * ((E1 - 60 - t) + 20.5)
    elif t < E1:
        known = data.cb[i + 1] - data.cb[E1 - 60 - data.t0 + 1]
        tau = E1 - t
        m = (known + tau * bt) / 60 + delta
        v = sigma ** 2 * tau * (tau + 1) * (2 * tau + 1) / (6 * 3600)
    else:
        m = data.bmean(E1) + delta
        v = 0.0
    sd = math.sqrt(v + meas ** 2)
    z = (m - win.K) / sd
    if fat:
        z = z / (1 + fat * abs(z) / 10)   # crude tail-widening knob
    return phi(z), m


# ---------------------------------------------------------------- market from trades
class Tape:
    def __init__(self, win):
        self.tr = win.trades
        self.ts = [x[0] for x in self.tr]

    def last_price(self, t, oi, max_age=10):
        """last trade price for outcome oi at or before t (implied from the other side if fresher)."""
        j = bisect.bisect_right(self.ts, t) - 1
        while j >= 0 and self.tr[j][0] >= t - max_age:
            x = self.tr[j]
            if x[1] == oi:
                return x[3]
            if x[1] == 1 - oi:
                return 1 - x[3]
            j -= 1
        return None

    def buy_fill(self, t, oi, limit, lat):
        lo = t + lat + FILL_SHIFT
        hi = lo + FILL_WINDOW
        j = bisect.bisect_left(self.ts, lo)
        while j < len(self.tr) and self.tr[j][0] <= hi:
            x = self.tr[j]
            if x[1] == oi and x[2] == 1 and x[3] <= limit:
                return x[3], x[4], x[0]
            j += 1
        return None


# ---------------------------------------------------------------- strategies
# Each strategy: fn(data, win, tape, P) -> list of (t, oi, limit, shares) intents.

def s_value(data, win, tape, P):
    """Buy the side whose fair value exceeds the market by > edge + fee, inside [t_from, t_to]."""
    out, used = [], 0
    for t in range(win.w + P.get("t_from", 0), win.w + P.get("t_to", 300), P.get("step", 1)):
        p_up, _ = fair(data, win, t, P.get("sig", 1.0), P.get("meas", None), P.get("fat", 0.0))
        for oi, fp in ((0, p_up), (1, 1 - p_up)):
            mk = tape.last_price(t, oi)
            if mk is None or not (P.get("pmin", 0.02) <= mk <= P.get("pmax", 0.98)):
                continue
            if fp - mk - fee(mk) > P.get("edge", 0.03):
                out.append((t, oi, fp - fee(mk) - P.get("edge", 0.03) * P.get("limit_frac", 0.5), P.get("shares", 50)))
                used += 1
                break
        if used >= P.get("max_trades", 1):
            break
    return out


def s_favorite(data, win, tape, P):
    """Baseline: at a fixed second buy whichever side the market favours."""
    t = win.w + P.get("at", 285)
    mk = tape.last_price(t, 0)
    if mk is None:
        return []
    oi = 0 if mk >= 0.5 else 1
    price = mk if oi == 0 else 1 - mk
    return [(t, oi, min(0.999, price + P.get("slip", 0.02)), P.get("shares", 50))]


def s_spot_sign(data, win, tape, P):
    """Baseline: at a fixed second buy the side spot (not TWAP) points to."""
    t = win.w + P.get("at", 240)
    m = data.b[t - data.t0] + data.delta_at(t)
    oi = 0 if m >= win.K else 1
    mk = tape.last_price(t, oi)
    if mk is None or mk > P.get("pmax", 0.95):
        return []
    return [(t, oi, mk + P.get("slip", 0.02), P.get("shares", 50))]


STRATS = {"value": s_value, "favorite": s_favorite, "spot_sign": s_spot_sign}


# ---------------------------------------------------------------- runner
def run(data, name, P, windows=None, lat=1):
    fn = STRATS[name]
    fills = []
    for win in windows or data.windows:
        tape = Tape(win)
        for (t, oi, limit, shares) in fn(data, win, tape, P):
            f = tape.buy_fill(t, oi, min(limit, 0.999), lat)
            if not f:
                continue
            price, size, ft = f
            q = min(shares, size)
            won = (oi == 0) == win.up
            pnl = q * ((1.0 if won else 0.0) - price - fee(price))
            fills.append({"w": win.w, "t": t - win.w, "oi": oi, "price": price, "q": q, "won": won, "pnl": pnl})
    return fills


def run_maker(data, P, windows=None):
    """Quote resting bids on both outcomes around fair value; fill on trade-through prints.

    Each second t in [t_from, t_to) the quotes are fair(t - qlat) - margin (per outcome), rounded
    down to the tick. A resting Up bid at q fills on a print in (t, t+1] that is a taker SELL of
    Up below q, or a taker BUY of Down above 1 - q (mirrored book). Makers pay no fee.
    """
    margin, tick = P.get("margin", 0.03), P.get("tick", 0.01)
    qlat, cap = P.get("qlat", 1), P.get("cap", 200)
    lo_p, hi_p = P.get("pmin", 0.05), P.get("pmax", 0.95)
    fills = []
    for win in windows or data.windows:
        tape = Tape(win)
        pos = [0.0, 0.0]
        for t in range(win.w + P.get("t_from", 5), win.w + P.get("t_to", 290)):
            p_up, _ = fair(data, win, t - qlat, P.get("sig", 1.0), P.get("meas", None))
            quotes = []
            for oi, fp in ((0, p_up), (1, 1 - p_up)):
                q = math.floor((fp - margin) / tick) * tick
                if lo_p <= q <= hi_p and pos[oi] < cap:
                    quotes.append((oi, round(q, 4)))
            if not quotes:
                continue
            j = bisect.bisect_right(tape.ts, t + FILL_SHIFT)
            while j < len(tape.tr) and tape.tr[j][0] <= t + 1 + FILL_SHIFT:
                ts, toi, side, price, size = tape.tr[j]
                for oi, q in quotes:
                    hit = (toi == oi and side == -1 and price < q) or (toi == 1 - oi and side == 1 and price > 1 - q)
                    if hit and pos[oi] < cap:
                        qty = min(size, cap - pos[oi], P.get("clip", 50))
                        pos[oi] += qty
                        won = (oi == 0) == win.up
                        fills.append({"w": win.w, "t": t - win.w, "oi": oi, "price": q, "q": qty, "won": won,
                                      "pnl": qty * ((1.0 if won else 0.0) - q), "fair": p_up if oi == 0 else 1 - p_up})
                j += 1
    return fills


def summarize(fills, nwin):
    if not fills:
        return {"trades": 0}
    pnl = np.array([f["pnl"] for f in fills])
    q = np.array([f["q"] for f in fills])
    per_share = pnl / q
    by_w = {}
    for f in fills:
        by_w[f["w"]] = by_w.get(f["w"], 0) + f["pnl"]
    wp = np.array(list(by_w.values()))
    return {
        "trades": len(fills),
        "windows_traded": len(by_w),
        "of_windows": nwin,
        "win_rate": round(float(np.mean([f["won"] for f in fills])), 3),
        "avg_price": round(float(np.mean([f["price"] for f in fills])), 3),
        "pnl_total": round(float(pnl.sum()), 2),
        "pnl_per_share": round(float(per_share.mean()), 4),
        "shares": round(float(q.sum()), 1),
        "t_stat": round(float(wp.mean() / (wp.std(ddof=1) / math.sqrt(len(wp)))), 2) if len(wp) > 2 and wp.std() > 0 else None,
    }


def parse_params(args):
    P = {}
    for a in args:
        k, v = a.split("=", 1)
        P[k] = float(v) if any(c in v for c in ".e") else int(v)
    return P


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "calibrate":
        for lag in range(0, 7):
            d = Data(lag=lag)
            print("lag", lag, "median|r| std p99:", d.calibration(), "windows", len(d.windows))
    elif cmd == "run":
        d = Data()
        P = parse_params(sys.argv[3:])
        fills = run_maker(d, P) if sys.argv[2] == "maker" else run(d, sys.argv[2], P, lat=P.get("lat", 1))
        print(json.dumps(summarize(fills, len(d.windows))))
