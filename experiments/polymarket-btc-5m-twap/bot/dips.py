"""Buy both legs on dips: take the first side whose ask falls to <= X, then the other side once the pair
locks >= margin after fees; an unpaired leg is held to the official result.

Leg 2 is bought as a taker (ask2 + fee2 <= 1 - p1 - fee1 - margin) or by a resting bid at that limit
from the moment leg 1 fills (queue model of preopen.py: behind the resting size, or at the front).

Because Down's ask = 1 - Up's bid, completing the pair needs leg 1's price to recover to about
H = p1 + fees + margin. If prices are a fair game (martingale) that happens with probability
mid1 / H, which makes the expected value zero before costs. `hindsight` reports, after the first dip to
X, the cheapest the other side ever got in the rest of the window (the best possible second leg).

Usage: python3 -I dips.py [hindsight | grid | run key=value ...]
"""
import json
import math
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
import live as LV

STEP = 250   # ms
TICK = 0.01


def fee(p):
    return 0.07 * p * (1 - p)


def grid(L, w):
    """Top of book on a 250 ms grid from open + 5 s to open + 295 s: asks, ask sizes, bids."""
    S = w * 1000
    rs = np.arange(S + 5000, S + 295000, STEP)
    A, Z, B = np.full((2, len(rs)), np.nan), np.zeros((2, len(rs))), np.full((2, len(rs)), np.nan)
    for s in (0, 1):
        ts = L.tob_t.get((w, s))
        if not ts:
            return None
        rows = L.tob[(w, s)]
        for k, i in enumerate(np.searchsorted(ts, rs, side="right") - 1):
            if i >= 0:
                _, bb, _, ba, bas = rows[i]
                if ba is not None:
                    A[s, k], Z[s, k] = ba, bas or 0.0
                if bb is not None:
                    B[s, k] = bb
    return rs, A, Z, B


def leg1(G, P):
    rs, A, Z, B = G
    X, lat = P["x"], max(1, P.get("lat_ms", 250) // STEP)
    last = int((P.get("last1_s", 240) * 1000 - 5000) / STEP)
    with np.errstate(invalid="ignore"):
        cond = A <= X + 1e-9
    for k in np.where(cond[0, :last] | cond[1, :last])[0]:
        s1, ke = (0 if cond[0, k] else 1), k + lat
        if ke < len(rs) and A[s1, ke] <= X + 1e-9 and Z[s1, ke] > 0:
            return s1, ke, A[s1, ke], min(P.get("size", 10), Z[s1, ke])
    return None


def run_window(L, w, G, P):
    l1 = leg1(G, P)
    if not l1:
        return None
    rs, A, Z, B = G
    s1, ke, p1, n1 = l1
    s2, lat, m = 1 - s1, max(1, P.get("lat_ms", 250) // STEP), P.get("margin", 0.0)
    lim = 1 - p1 - fee(p1) - m              # all-in cost allowed for leg 2
    q2 = c2 = 0.0
    with np.errstate(invalid="ignore"):
        ok = A[s2] + fee(A[s2]) <= lim + 1e-9
    if P.get("leg2", "taker") == "taker":
        for j in np.where(ok[ke + 1:])[0] + ke + 1:
            je = j + lat
            if je >= len(rs):
                break
            a2 = A[s2, je]
            if a2 + fee(a2) <= lim + 1e-9 and Z[s2, je] > 0:
                x = min(n1 - q2, Z[s2, je]); q2 += x; c2 += x * (a2 + fee(a2))
                if q2 >= n1 - 1e-9:
                    break
    else:   # resting bid at Y from leg-1 time + latency; maker pays no fee
        Y = math.floor(lim / TICK + 1e-9) * TICK
        t0 = rs[min(ke + lat, len(rs) - 1)]
        k0 = min(ke + lat, len(rs) - 1)
        if A[s2, k0] <= Y + 1e-9:          # already marketable: take it
            a2 = A[s2, k0]
            if a2 + fee(a2) <= lim + 1e-9:
                q2 = min(n1, Z[s2, k0]); c2 = q2 * (a2 + fee(a2))
        if q2 < n1 - 1e-9:
            bb2 = B[s2, k0]
            ahead = 0.0
            if P["leg2"] == "maker_book" and not (bb2 is None or np.isnan(bb2) or Y > bb2 + 1e-9):
                d = L.depth_at(w, s2, t0)
                ahead = next((sz for p, sz in d[1] if abs(p - Y) < 1e-9), 0.0) if d else 0.0
            # an ask at or below our bid would have traded with us: first grid step where that happened
            with np.errstate(invalid="ignore"):
                cross = np.where(A[s2, k0 + 1:] <= Y + 1e-9)[0]
            t_cross = rs[k0 + 1 + cross[0]] if len(cross) else None
            for ts, ta, price, sz, side in L.trades.get(w, []):
                if ts < t0:
                    continue
                if t_cross is not None and ts >= t_cross:
                    x = min(n1 - q2, Z[s2, k0 + 1 + cross[0]]); q2 += x; c2 += x * Y
                    t_cross = None
                    if q2 >= n1 - 1e-9:
                        break
                if not ((ta == s2 and side == "SELL") or (ta == s1 and side == "BUY")):
                    continue
                hit = price if ta == s2 else 1 - price
                if hit < Y - 1e-9:
                    avail = sz
                elif abs(hit - Y) < 1e-9:
                    eat = min(ahead, sz); ahead -= eat; avail = sz - eat
                else:
                    continue
                x = min(avail, n1 - q2)
                if x > 0:
                    q2 += x; c2 += x * Y
                if q2 >= n1 - 1e-9:
                    break
    up = L.F[w] >= L.K[w]
    won1 = (s1 == 0) == up
    pairs, leftover = min(n1, q2), n1 - min(n1, q2)
    cost = n1 * (p1 + fee(p1)) + c2
    pnl = pairs + (leftover if won1 else 0.0) - cost
    mid1 = (A[s1, ke] + B[s1, ke]) / 2 if not np.isnan(B[s1, ke]) else p1
    a_star = lim   # leg-2 ask that just completes (solve a + fee(a) = lim)
    for _ in range(20):
        a_star = lim - fee(a_star)
    H = 1 - a_star  # leg-1 bid needed
    return {"w": w, "s1": s1, "p1": p1, "t1": (rs[ke] - w * 1000) / 1000, "complete": q2 >= n1 - 1e-9, "pairs": pairs,
            "leftover": leftover, "won1": won1, "pnl": pnl, "pred": min(1.0, mid1 / (H + TICK / 2))}


def run(lives, P, grids):
    out = []
    for (as_, w), G in grids.items():
        if P.get("assets") and as_ not in P["assets"].split(","):
            continue
        r = run_window(lives[as_], w, G, P)
        if r:
            r["as"] = as_
            out.append(r)
    return out


def summarize(rows, n_windows):
    if not rows:
        return None
    pnl = np.array([r["pnl"] for r in rows])
    by_t = defaultdict(float)
    for r in rows:
        by_t[r["w"]] += r["pnl"]
    tv = np.array(list(by_t.values()))
    lo = [r for r in rows if r["leftover"] > 0]
    return {"traded": len(rows), "of": n_windows, "complete": float(np.mean([r["complete"] for r in rows])),
            "pred": float(np.mean([r["pred"] for r in rows])), "pairs": sum(r["pairs"] for r in rows),
            "lo_won": float(np.mean([r["won1"] for r in lo])) if lo else float("nan"), "pnl": float(pnl.sum()),
            "per": float(pnl.mean()), "t": float(tv.mean() / (tv.std(ddof=1) / math.sqrt(len(tv)))) if len(tv) > 2 and tv.std() > 0 else float("nan")}


def hindsight(lives, grids):
    print("After the first time one side's ask touches X (taker, 250 ms latency), the cheapest the OTHER side ever got")
    print("for the rest of the window, as the best possible pair cost incl. both fees:")
    print("   X     windows  pair < $1.00  < $0.98  < $0.95  < $0.90   median best   no pair possible: leg 1 won")
    for X in (0.45, 0.40, 0.35, 0.30, 0.25, 0.20, 0.15, 0.10):
        best, lost = [], []
        for (as_, w), G in grids.items():
            l1 = leg1(G, {"x": X})
            if not l1:
                continue
            rs, A, Z, B = G
            s1, ke, p1, _ = l1
            a2 = A[1 - s1, ke + 1:]
            a2 = a2[~np.isnan(a2)]
            if not len(a2):
                continue
            b = p1 + fee(p1) + float(np.min(a2 + fee(a2)))
            best.append(b)
            if b >= 1.0:
                L = lives[as_]
                lost.append((s1 == 0) == (L.F[w] >= L.K[w]))
        best = np.array(best)
        print(f"  {X:.2f}   {len(best):6d}     {np.mean(best < 1):5.1%}    {np.mean(best < .98):5.1%}   {np.mean(best < .95):5.1%}   "
              f"{np.mean(best < .90):5.1%}      {np.median(best):.3f}        {np.mean(lost) if lost else float('nan'):5.1%} of {len(lost)}")


def load():
    rows = LV.load_rows()
    lives = {a: LV.Live(asset=a, rows=rows) for a in LV.ASSETS}
    grids = {}
    for a, L in lives.items():
        for w in sorted(L.F):
            if w in L.K and w in L.windows:
                G = grid(L, w)
                if G is not None:
                    grids[(a, w)] = G
    return lives, grids


if __name__ == "__main__":
    lives, grids = load()
    n = len(grids)
    cmd = sys.argv[1] if len(sys.argv) > 1 else "grid"
    if cmd == "hindsight":
        hindsight(lives, grids)
    elif cmd == "run":
        P = {}
        for kv in sys.argv[2:]:
            k, v = kv.split("=", 1)
            for cast in (int, float, str):
                try:
                    P[k] = cast(v)
                    break
                except ValueError:
                    pass
        print(json.dumps(summarize(run(lives, P, grids), n)))
    else:
        hindsight(lives, grids)
        print(f"\nStrategy, 10 shares, leg 1 until +240 s; {n} asset-windows")
        print("   X    margin leg2         traded  completed  fair-game  pairs  leftover won   P&L $   $/traded  t_time")
        for X in (0.45, 0.40, 0.35, 0.30, 0.25, 0.20, 0.15):
            for m in (0.0, 0.02, 0.05, 0.10):
                for leg2 in ("taker", "maker_book", "maker_front"):
                    s = summarize(run(lives, {"x": X, "margin": m, "leg2": leg2, "size": 10}, grids), n)
                    if s:
                        print(f"  {X:.2f}  {m:.2f}  {leg2:11s}  {s['traded']:5d}    {s['complete']:6.1%}    {s['pred']:6.1%}  {s['pairs']:5.0f}     "
                              f"{s['lo_won']:5.1%}    {s['pnl']:7.2f}   {s['per']:7.3f}  {s['t']:6.2f}", flush=True)
