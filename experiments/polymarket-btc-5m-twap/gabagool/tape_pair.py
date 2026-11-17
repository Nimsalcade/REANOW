"""Tape-based pair-maker replay for any era (no historical books needed).

The Up touch is reconstructed from taker prints (mirrored books): taker BUY Up at p -> Up ask p;
taker SELL Up at p -> Up bid p; taker BUY Down at p -> Up bid 1-p; taker SELL Down at p -> Up ask 1-p.
Every second we rest bids on both outcomes at the reconstructed best bids (join), capped so a matched pair
costs <= 1 - margin, with an inventory limit, price bounds and an early stop. A later print fills a bid
at the touch (share `touch_share` of its size, queue proxy) or through it (full size).
P&L = merged pairs ($1 each) + leftover payout - cost - fees (fee_rate x p(1-p) per taker share; 0 in 2025).

Usage: python3 -I tape_pair.py MARKETS.json [key=value ...]
MARKETS.json: [{"slug", "start", "duration", "up_won", "trades": [[ts, outcome_index, side, price, size], ...]}]
"""
import json
import math
import sys
from collections import defaultdict

import numpy as np


def simulate(mkt, P):
    S, D = mkt["start"], mkt["duration"]
    shift = P.get("ts_shift", 2)          # data-api timestamps lag the match by ~2 s
    tr = sorted(((x[0] - shift, x[1], x[2], x[3], x[4]) for x in mkt["trades"]), key=lambda x: x[0])
    bid = ask = None
    q, c = [0.0, 0.0], [0.0, 0.0]
    fills = []
    margin, max_imb, clip = P.get("margin", 0.0), P.get("max_imb", 30), P.get("clip", 10)
    stop = S + D - P.get("t_stop", 60)
    j = 0
    for sec in range(S + P.get("t_from", 5), stop):
        # 1) prints during (sec-1, sec] update the touch (information known at sec)
        quotes = {}
        if bid is not None and ask is not None and bid < ask:
            for s in (0, 1):
                o = 1 - s
                my_bid = bid if s == 0 else 1 - ask            # best bid of outcome s
                other_bid = (1 - ask) if s == 0 else bid
                if q[s] - q[o] >= max_imb:
                    continue
                avg_o = c[o] / q[o] if q[o] > 0 else None
                cap = 1 - margin - (avg_o if (avg_o is not None and q[o] > q[s]) else other_bid)
                my_ask = ask if s == 0 else 1 - bid
                if P.get("lean", 0) and q[o] - q[s] >= P.get("lean_min", 1):
                    want = my_ask - 0.01                     # light side: step up to the top of book (<= cap)
                else:
                    want = my_bid - P.get("heavy_back", 0) * 0.01 if q[s] > q[o] else my_bid
                px = math.floor(min(want, cap) * 100 + 1e-9) / 100
                if P.get("pmin", 0.2) <= px <= 1 - P.get("pmin", 0.2):
                    quotes[s] = [px, clip]
        # taker chase: top up the light side at the reconstructed ask if the pair still clears the cap
        if P.get("chase", 0) and bid is not None and ask is not None and bid < ask:
            for s in (0, 1):
                o = 1 - s
                gap = q[o] - q[s]
                if gap >= P.get("chase_min", 5) and q[o] > 0:
                    a_s = ask if s == 0 else 1 - bid
                    f = P.get("fee_rate", 0.0) * a_s * (1 - a_s)
                    if a_s + f <= 1 - margin - c[o] / q[o]:
                        x = min(gap, P.get("chase_clip", 20))
                        q[s] += x; c[s] += x * (a_s + f)
                        fills.append((sec, s, a_s, x))
        while j < len(tr) and tr[j][0] < sec + 1:
            t, oi, sd, p, size = tr[j]
            # 2) fills against quotes resting during [sec, sec+1)
            if t >= sec:
                for s, qt in quotes.items():
                    if qt[1] <= 0:
                        continue
                    b = qt[0]
                    hit_px = p if oi == s else 1 - p       # price expressed in outcome-s terms
                    seller_into_s = (oi == s and sd == -1) or (oi == 1 - s and sd == 1)
                    if not seller_into_s:
                        continue
                    if hit_px < b - 1e-9:
                        x = min(size, qt[1])
                    elif abs(hit_px - b) < 1e-9:
                        x = min(size * P.get("touch_share", 0.5), qt[1])
                    else:
                        continue
                    x = min(x, max(0.0, max_imb - (q[s] - q[1 - s])))
                    if x <= 0:
                        continue
                    qt[1] -= x; q[s] += x; c[s] += x * b
                    fills.append((t, s, b, x))
            # touch update from this print
            up_px = p if oi == 0 else 1 - p
            if (oi == 0 and sd == 1) or (oi == 1 and sd == -1):
                ask = up_px
            else:
                bid = up_px
            j += 1
    pairs = min(q)
    leftover = abs(q[0] - q[1])
    left_side = 0 if q[0] > q[1] else 1
    payout = pairs + (leftover if (left_side == 0) == mkt["up_won"] else 0.0)
    cost = c[0] + c[1]
    pc = (c[0] / q[0] + c[1] / q[1]) if q[0] > 0 and q[1] > 0 else None
    return {"slug": mkt["slug"], "pnl": payout - cost, "cost": cost, "pairs": pairs, "leftover": leftover,
            "pair_cost": pc, "q": q, "fills": fills}


def summarize(res):
    res = [r for r in res if r["cost"] > 0]
    pnl = np.array([r["pnl"] for r in res])
    pcs = [r["pair_cost"] for r in res if r["pair_cost"]]
    return {"markets": len(res), "pnl": round(float(pnl.sum()), 2), "pnl_per_market": round(float(pnl.mean()), 3),
            "win_share": round(float(np.mean(pnl > 0)), 3), "median_pair_cost": round(float(np.median(pcs)), 4) if pcs else None,
            "median_balance": round(float(np.median([min(r["q"]) / max(r["q"]) for r in res if max(r["q"]) > 0])), 3),
            "roi_%": round(100 * float(pnl.sum()) / sum(r["cost"] for r in res), 2),
            "t": round(float(pnl.mean() / (pnl.std(ddof=1) / math.sqrt(len(pnl)))), 2) if len(pnl) > 2 else None}


if __name__ == "__main__":
    mkts = json.load(open(sys.argv[1]))
    P = {}
    for kv in sys.argv[2:]:
        k, v = kv.split("=", 1)
        P[k] = float(v) if "." in v else int(v)
    print(json.dumps(summarize([simulate(m, P) for m in mkts])))
