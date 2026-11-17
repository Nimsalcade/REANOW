"""Pre-open pair resting: post both legs of the NEXT window during the previous one, then see what fills.

For each 5 m window S: at S - lead, rest a bid of `size` shares at `px` on Up and on Down (a locked pair
costs 2 x px, so 0.49 locks 2 c per pair). Fills use the price-time queue model of pairmaker.py
(fill=queue): the bid joins behind the size resting at its price (`ahead=book`) or at the front of
the queue as if posted hours earlier (`ahead=front`). Prints at our price eat the queue ahead first,
and prints through the price fill fully. Both a taker SELL of that outcome and a taker BUY of the
mirror outcome hit it. Cancellations ahead of us are ignored. Unfilled size is cancelled at S + cancel_s.
Matched pairs merge for $1. The leftover is held to the official result. Optional `chase=1` buys the
missing leg as a taker when that completes the pair at <= 1 - margin including the fee. Maker rebates
are estimated separately and not included in the P&L.

Usage: python3 -I preopen.py [grid | run key=value ...]
"""
import bisect
import json
import math
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
import live as LV


def fee(p):
    return 0.07 * p * (1 - p)


def size_at(L, w, a, px, r):
    d = L.depth_at(w, a, r)
    return next((s for p, s in d[1] if abs(p - px) < 1e-9), 0.0) if d else 0.0


def run_window(L, w, P):
    S = w * 1000
    lead, lat, px, size = int(P.get("lead_s", 60) * 1000), int(P.get("lat_ms", 250)), P.get("px", 0.49), P.get("size", 50)
    t_place, t_cancel = S - lead + lat, S + int(P.get("cancel_s", 290) * 1000)
    if L.depth_at(w, 0, t_place) is None or L.book(w, 0, t_place) is None:
        return None
    books = [L.book(w, s, t_place) for s in (0, 1)]
    if any(b is None or (b[3] is not None and px >= b[3] - 1e-9) for b in books):
        return {"w": w, "skip": True}   # a bid at or above the ask would take liquidity (post-only rejects it)
    legs = []
    for s in (0, 1):
        bb = books[s][1]
        ahead = 0.0 if P.get("ahead", "book") == "front" or (bb is not None and px > bb + 1e-9) else size_at(L, w, s, px, t_place)
        legs.append({"ahead": ahead, "left": float(size), "q": 0.0, "first": None})
    tr = L.trades.get(w, [])
    j = bisect.bisect_left([x[0] for x in tr], t_place)
    q_taker, c_taker, fees_paid, rebate = [0.0, 0.0], [0.0, 0.0], 0.0, 0.0
    for ts, ta, price, sz, side in tr[j:]:
        if ts > t_cancel:
            break
        for s, lg in enumerate(legs):
            if lg["left"] <= 0:
                continue
            hit = price if ta == s else 1 - price
            if not ((ta == s and side == "SELL") or (ta == 1 - s and side == "BUY")):
                continue
            if hit < px - 1e-9:
                avail = sz
            elif abs(hit - px) < 1e-9:
                eat = min(lg["ahead"], sz); lg["ahead"] -= eat; avail = sz - eat
            else:
                continue
            x = min(avail, lg["left"])
            if x > 0:
                lg["left"] -= x; lg["q"] += x; rebate += 0.2 * x * fee(px)
                lg["first"] = lg["first"] or ts
        if P.get("chase", 0):
            for s in (0, 1):
                o = 1 - s
                gap = legs[o]["q"] + q_taker[o] - legs[s]["q"] - q_taker[s]
                if gap > 0:   # complete the pair on side s as a taker if it still locks >= margin
                    b = L.book(w, s, ts + lat)
                    if b and b[3] is not None and px + b[3] + fee(b[3]) <= 1 - P.get("margin", 0.0) + 1e-9:
                        x = min(gap, b[4] or 0.0)
                        q_taker[s] += x; c_taker[s] += x * b[3]; fees_paid += x * fee(b[3])
                        legs[s]["left"] = max(0.0, legs[s]["left"] - x)
    q = [legs[s]["q"] + q_taker[s] for s in (0, 1)]
    cost = px * (legs[0]["q"] + legs[1]["q"]) + c_taker[0] + c_taker[1] + fees_paid
    up = L.F[w] >= L.K[w]
    pairs, heavy = min(q), (0 if q[0] > q[1] else 1)
    leftover = abs(q[0] - q[1])
    won = (heavy == 0) == up
    payout = pairs + (leftover if won else 0.0)
    both = legs[0]["q"] > 0 and legs[1]["q"] > 0
    pre_fill = [lg["first"] is not None and lg["first"] < S for lg in legs]
    return {"w": w, "q": q, "pairs": pairs, "leftover": leftover, "leftover_won": won if leftover > 0 else None,
            "cost": cost, "pnl": payout - cost, "rebate_est": rebate, "both": both,
            "any": legs[0]["q"] > 0 or legs[1]["q"] > 0, "pre_fill": any(pre_fill),
            "ahead0": [round(lg["ahead"], 1) for lg in legs]}


def run(lives, P):
    out = []
    for as_, L in lives.items():
        if P.get("assets") and as_ not in P["assets"].split(","):
            continue
        for w in sorted(L.F):
            if w in L.K and w in L.windows:
                r = run_window(L, w, P)
                if r:
                    r["as"] = as_
                    out.append(r)
    return out


def summarize(rows):
    skipped = sum(1 for r in rows if r.get("skip"))
    rows = [r for r in rows if not r.get("skip")]
    n = len(rows)
    if not n:
        return {"windows": 0, "skipped": skipped}
    pnl = np.array([r["pnl"] for r in rows])
    by_t = defaultdict(float)
    for r in rows:
        by_t[r["w"]] += r["pnl"]
    tv = np.array(list(by_t.values()))
    pairs = sum(r["pairs"] for r in rows)
    lo = [r for r in rows if r["leftover"] > 0]
    filled = [r for r in rows if r["any"]]
    return {"windows": n, "skipped": skipped, "slots": len(tv),
            "any_fill": round(len(filled) / n, 3), "both_filled": round(sum(r["both"] for r in rows) / n, 3),
            "filled_pre_open": round(sum(r["pre_fill"] for r in rows) / n, 3),
            "pairs": round(pairs, 1), "leftover_sh": round(sum(r["leftover"] for r in rows), 1),
            "leftover_win_rate": round(float(np.mean([r["leftover_won"] for r in lo])), 3) if lo else None,
            "pnl": round(float(pnl.sum()), 2), "pnl_per_window": round(float(pnl.mean()), 3),
            "pnl_per_filled_window": round(float(pnl.sum()) / len(filled), 3) if filled else None,
            "rebate_est": round(sum(r["rebate_est"] for r in rows), 2),
            "t_time": round(float(tv.mean() / (tv.std(ddof=1) / math.sqrt(len(tv)))), 2) if len(tv) > 2 and tv.std() > 0 else None}


def parse(args):
    P = {}
    for kv in args:
        k, v = kv.split("=", 1)
        try:
            P[k] = int(v)
        except ValueError:
            try:
                P[k] = float(v)
            except ValueError:
                P[k] = v
    return P


if __name__ == "__main__":
    rows = LV.load_rows()
    lives = {a: LV.Live(asset=a, rows=rows) for a in LV.ASSETS}
    if len(sys.argv) > 1 and sys.argv[1] == "run":
        print(json.dumps(summarize(run(lives, parse(sys.argv[2:])))))
    else:
        print("ahead  lead_s  px    size  cancel_s chase | windows skip any  both  pre   pairs  leftover lo_win  P&L $   $/window  t_time  rebate")
        for ahead in ("book", "front"):
            for lead in (240, 60, 10):
                for px in (0.49, 0.48, 0.45):
                    for size in (10, 100):
                        for cancel, chase in ((290, 0), (30, 0), (290, 1)):
                            P = {"ahead": ahead, "lead_s": lead, "px": px, "size": size, "cancel_s": cancel, "chase": chase}
                            s = summarize(run(lives, P))
                            print(f"{ahead:5s}  {lead:5d}  {px:.2f}  {size:4d}  {cancel:6d}   {chase}   | {s['windows']:5d} {s['skipped']:4d}  {s['any_fill']:.2f}  "
                                  f"{s['both_filled']:.2f}  {s['filled_pre_open']:.2f}  {s['pairs']:6.0f}  {s['leftover_sh']:7.0f}  "
                                  f"{s['leftover_win_rate'] if s['leftover_win_rate'] is not None else '-':>5}  {s['pnl']:7.2f}  "
                                  f"{s['pnl_per_window']:7.3f}  {s['t_time'] if s['t_time'] is not None else '-':>6}  {s['rebate_est']:.2f}", flush=True)
