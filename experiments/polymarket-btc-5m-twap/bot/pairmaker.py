"""Pair maker (gabagool structure) replayed on the recorder's live books.

Resting bids on both outcomes; a matched Up+Down pair is merged for $1, so P&L =
pairs + leftover payout - cost - taker fees. Quotes refresh every `rq_ms`, become active after `lat_ms`,
and fill only on real websocket prints that trade *through* the bid (taker SELL of that outcome below
it, or taker BUY of the mirror outcome above 1 - bid). Optional taker "chase" buys the light side at the
ask when that keeps the pair under the cap after the fee. Maker rebates are estimated separately
(rebate_rate x taker fee on our maker fills) and never included in the main P&L.

Usage: python3 -I pairmaker.py [grid|run key=value ...]
"""
import bisect
import itertools
import json
import math
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
import live as LV

TICK = 0.01


def fee(p):
    return 0.07 * p * (1 - p)


def floor_tick(p):
    return math.floor(p / TICK + 1e-9) * TICK


def run_window(L, w, P):
    S, E = w * 1000, (w + 300) * 1000
    tr = L.trades.get(w, [])
    tts = [x[0] for x in tr]
    q, c = [0.0, 0.0], [0.0, 0.0]
    taker_fees, rebate, maker_fills, taker_fills = 0.0, 0.0, 0, 0
    rq, lat, m = P.get("rq_ms", 1000), P.get("lat_ms", 250), P.get("margin", 0.01)
    clip, max_imb = P.get("clip", 10), P.get("max_imb", 30)
    for r in range(S + int(P.get("t_from", 5) * 1000), E - int(P.get("t_stop", 10) * 1000), rq):
        books = [L.book(w, a, r) for a in (0, 1)]
        if not all(b and b[1] is not None and b[3] is not None for b in books):
            continue
        quotes = {}
        for s in (0, 1):
            o = 1 - s
            bb, ba = books[s][1], books[s][3]
            if q[s] - q[o] >= max_imb:
                continue
            avg_o = c[o] / q[o] if q[o] > 0 else None
            # cap so a matched pair costs <= 1 - margin
            cap = 1 - m - (avg_o if (avg_o is not None and q[o] > q[s]) else books[o][1])
            # taker chase: buy the light side outright if it still pairs under the cap after the fee
            if P.get("chase", 0) and q[o] - q[s] >= P.get("chase_min", 10) and avg_o is not None:
                bt = L.book(w, s, r + lat)
                if bt and bt[3] is not None and bt[3] + fee(bt[3]) <= 1 - m - avg_o:
                    x = min(q[o] - q[s], bt[4] or 0, P.get("chase_clip", 20))
                    if x > 0:
                        q[s] += x; c[s] += x * bt[3]; taker_fees += x * fee(bt[3]); taker_fills += 1
                        continue
            mode = P.get("mode", "join")
            want = bb + TICK if mode == "improve" and bb + TICK < ba else bb - TICK if mode == "below" else bb
            px = floor_tick(min(want, cap))
            if px < P.get("pmin", 0.02) or px >= ba:
                continue
            quotes[s] = [round(px, 2), clip]
        if not quotes:
            continue
        j = bisect.bisect_right(tts, r + lat)
        while j < len(tr) and tr[j][0] <= r + rq + lat:
            ts, ta, price, size, side = tr[j]
            for s, qt in quotes.items():
                if qt[1] <= 0:
                    continue
                p = qt[0]
                through = (ta == s and side == "SELL" and price < p) or (ta == 1 - s and side == "BUY" and price > 1 - p)
                touch = P.get("touch", 0) and ((ta == s and side == "SELL" and abs(price - p) < 1e-9) or (ta == 1 - s and side == "BUY" and abs(price - (1 - p)) < 1e-9))
                if through or touch:
                    x = min(size * (P.get("touch_share", 0.25) if touch and not through else 1.0), qt[1])
                    if q[s] - q[1 - s] + x > max_imb:
                        x = max(0.0, max_imb - (q[s] - q[1 - s]))
                    if x <= 0:
                        continue
                    qt[1] -= x; q[s] += x; c[s] += x * p; maker_fills += 1
                    rebate += P.get("rebate_rate", 0.2) * x * fee(p)
            j += 1
    up = L.F[w] >= L.K[w]
    pairs = min(q)
    left_side = 0 if q[0] > q[1] else 1
    leftover = abs(q[0] - q[1])
    payout = pairs + (leftover if (left_side == 0) == up else 0.0)
    cost = c[0] + c[1]
    pair_cost = (c[0] / q[0] + c[1] / q[1]) if q[0] > 0 and q[1] > 0 else None
    return {"w": w, "pairs": pairs, "leftover": leftover, "cost": cost, "pnl": payout - cost - taker_fees,
            "locked": pairs - (pairs * pair_cost if pair_cost else 0.0), "pair_cost": pair_cost,
            "rebate_est": rebate, "maker_fills": maker_fills, "taker_fills": taker_fills, "q": q[:]}


def run(lives, P):
    out = []
    for as_, L in lives.items():
        if P.get("assets") and as_ not in P["assets"]:
            continue
        for w in sorted(L.F):
            if w < P.get("first_window", 0) or w not in L.windows or (w, 0) not in L.tob or w not in L.K:
                continue
            res = run_window(L, w, P)
            if res["pairs"] + res["leftover"] > 0:
                res["as"] = as_
                out.append(res)
    return out


def summarize(rows):
    if not rows:
        return {"windows": 0}
    pnl = np.array([r["pnl"] for r in rows])
    by_t = defaultdict(float)
    for r in rows:
        by_t[r["w"]] += r["pnl"]
    tv = np.array(list(by_t.values()))
    cost = sum(r["cost"] for r in rows)
    pcs = [r["pair_cost"] for r in rows if r["pair_cost"]]
    return {"windows": len(rows), "slots": len(tv), "pnl": round(float(pnl.sum()), 2),
            "pnl_per_window": round(float(pnl.mean()), 3), "win_share": round(float(np.mean(pnl > 0)), 3),
            "roi_on_cost_%": round(100 * float(pnl.sum()) / cost, 3) if cost else None,
            "median_pair_cost": round(float(np.median(pcs)), 4) if pcs else None,
            "locked": round(sum(r["locked"] for r in rows), 2),
            "leftover_pnl": round(float(pnl.sum()) - sum(r["locked"] for r in rows), 2),
            "rebate_est": round(sum(r["rebate_est"] for r in rows), 2),
            "pairs": round(sum(r["pairs"] for r in rows), 1),
            "t_window": round(float(pnl.mean() / (pnl.std(ddof=1) / math.sqrt(len(pnl)))), 2) if len(pnl) > 2 and pnl.std() > 0 else None,
            "t_time": round(float(tv.mean() / (tv.std(ddof=1) / math.sqrt(len(tv)))), 2) if len(tv) > 2 and tv.std() > 0 else None}


if __name__ == "__main__":
    rows = LV.load_rows()
    lives = {a: LV.Live(asset=a, rows=rows) for a in LV.ASSETS}
    if len(sys.argv) > 1 and sys.argv[1] == "run":
        P = {}
        for kv in sys.argv[2:]:
            k, v = kv.split("=", 1)
            P[k] = v if not v.replace(".", "").isdigit() else (float(v) if "." in v else int(v))
        res = run(lives, P)
        print(json.dumps(summarize(res)))
        per = defaultdict(list)
        for r in res:
            per[r["as"]].append(r)
        for a, v in per.items():
            print("  ", a, json.dumps(summarize(v)))
    else:
        grid = itertools.product(("join", "improve", "below"), (0.0, 0.01, 0.02), (20, 60), (250, 1000), (0, 1))
        results = []
        for mode, m, imb, rq, chase in grid:
            P = {"mode": mode, "margin": m, "max_imb": imb, "rq_ms": rq, "chase": chase, "clip": 10}
            s = summarize(run(lives, P))
            results.append((s.get("t_time") or -99, P, s))
            print(json.dumps(P), json.dumps(s), flush=True)
        results.sort(key=lambda x: -x[0])
        print("\nTOP 8 by t_time:")
        for t, P, s in results[:8]:
            print(json.dumps(P), json.dumps(s))
