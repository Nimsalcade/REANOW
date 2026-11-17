"""Informed maker: after a fair-value jump, post a passive bid on the favoured outcome instead of taking.

Trigger: |fair(r) - fair(r - look)| >= jump (Binance-led TWAP model), at most `max_trades` per window,
`gap_ms` apart. Order: bid at the best bid (join) or one tick better if the spread allows (improve), for
`hold_s` seconds, active after `lat_ms`. Fills use a price-time queue model: the order sits behind the size
resting at its price (1 s depth snapshot; a new better level starts first); sells printing at that price
eat the queue ahead first; prints through the price fill fully. No fee (makers pay none). Held to
resolution. Rebate estimate (0.2 x taker fee) reported separately.

Usage: python3 -I informed_maker.py
"""
import bisect, collections, itertools, json, math, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
import live as LV

TICK = 0.01
fee = lambda p: 0.07 * p * (1 - p)


def size_at(L, w, a, px, r):
    d = L.depth_at(w, a, r)
    if not d:
        return 0.0
    for lp, ls in d[1]:
        if abs(lp - px) < 1e-9:
            return ls
    return 0.0


def run_asset(L, as_, P):
    out = []
    for w in sorted(L.F):
        if w < P.get("first_window", 0) or w not in L.windows or (w, 0) not in L.tob or w not in L.K:
            continue
        up = L.F[w] >= L.K[w]
        tr = L.trades.get(w, []); tts = [x[0] for x in tr]
        S = w * 1000; last = -10 ** 12; n = 0
        for r in range(S + 5000, S + int(P.get("t_to", 290)) * 1000, 100):
            if r - last < P["gap_ms"] or n >= P["max_trades"]:
                continue
            f1 = L.fair_fast(w, r); f0 = L.fair_fast(w, r - P["look_ms"])
            if not f0 or not f1 or abs(f1[0] - f0[0]) < P["jump"]:
                continue
            a = 0 if f1[0] > f0[0] else 1
            bk = L.book(w, a, r)
            if not bk or bk[1] is None or bk[3] is None:
                continue
            bb, ba = bk[1], bk[3]
            if not (P.get("pmin", 0.10) <= (bb + ba) / 2 <= P.get("pmax", 0.90)):
                continue
            last = r; n += 1
            px = round(bb + TICK, 2) if P["mode"] == "improve" and bb + TICK < ba - 1e-9 else bb
            t0 = r + P["lat_ms"]
            ahead = size_at(L, w, a, px, t0) if abs(px - bb) < 1e-9 else 0.0
            left, got = P["shares"], 0.0
            j = bisect.bisect_right(tts, t0)
            while j < len(tr) and tr[j][0] <= t0 + P["hold_s"] * 1000 and left > 0:
                ts, ta, price, size, side = tr[j]
                hit = price if ta == a else 1 - price
                seller = (ta == a and side == "SELL") or (ta == 1 - a and side == "BUY")
                if seller:
                    if hit < px - 1e-9:
                        x = min(size, left)
                    elif abs(hit - px) < 1e-9:
                        eat = min(ahead, size); ahead -= eat; x = min(size - eat, left)
                    else:
                        x = 0
                    if x > 0:
                        got += x; left -= x
                j += 1
            if got > 0:
                won = (a == 0) == up
                out.append({"as": as_, "w": w, "q": got, "px": px, "pnl": got * ((1.0 if won else 0.0) - px),
                            "rebate": 0.2 * got * fee(px), "won": won})
            out.append({"as": as_, "w": w, "trigger": 1, "filled": got > 0})
    return out


def summarize(rows):
    fills = [r for r in rows if "pnl" in r]
    trig = [r for r in rows if "trigger" in r]
    if not fills:
        return {"triggers": len(trig), "fills": 0}
    q = sum(f["q"] for f in fills); pnl = sum(f["pnl"] for f in fills)
    by_t = collections.defaultdict(float)
    for f in fills:
        by_t[f["w"]] += f["pnl"]
    tv = np.array(list(by_t.values()))
    return {"triggers": len(trig), "fill_rate": round(len(fills) / len(trig), 3), "fills": len(fills), "shares": round(q),
            "win": round(float(np.mean([f["won"] for f in fills])), 3), "avg_px": round(sum(f["px"] * f["q"] for f in fills) / q, 3),
            "c_per_share": round(100 * pnl / q, 2), "pnl": round(pnl, 2), "rebate_est": round(sum(f["rebate"] for f in fills), 2),
            "slots": len(tv), "t_time": round(float(tv.mean() / (tv.std(ddof=1) / math.sqrt(len(tv)))), 2) if len(tv) > 2 and tv.std() > 0 else None}


if __name__ == "__main__":
    rows = LV.load_rows()
    lives = {a: LV.Live(asset=a, rows=rows) for a in LV.ASSETS}
    split = 1794846600   # 16:30Z: the direction-sniper forward split, for an in/out comparison
    for mode, jump, hold, lat in itertools.product(("join", "improve"), (0.04, 0.06), (5, 15, 30), (100, 250)):
        P = {"mode": mode, "jump": jump, "hold_s": hold, "lat_ms": lat, "look_ms": 1000, "gap_ms": 5000, "max_trades": 3, "shares": 100}
        allr = []
        for as_, L in lives.items():
            allr += run_asset(L, as_, P)
        early = [r for r in allr if r["w"] < split]; late = [r for r in allr if r["w"] >= split]
        print(json.dumps({k: P[k] for k in ("mode", "jump", "hold_s", "lat_ms")}), "| ALL", json.dumps(summarize(allr)),
              "| <16:30", summarize(early).get("c_per_share"), "| >=16:30", summarize(late).get("c_per_share"), summarize(late).get("t_time"), flush=True)
