"""Feature breakdown of direction-sniper fills (discovery only; new filters must be forward-tested).
Replicates taker_dir triggers and fills, records tau, mid, jump size, book staleness, asset."""
import sys, os, json, collections
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
import live as LV

def fills_with_features(L, as_, P):
    out = []
    look, jump, lat = P["look_ms"], P["jump"], P["lat_ms"]
    for w in sorted(L.F):
        if w not in L.windows or (w, 0) not in L.tob or w not in L.K: continue
        up = L.F[w] >= L.K[w]; S = w * 1000; last = -10**12; n = 0
        for r in range(S + 5000, S + 299000, 100):
            if r - last < 5000 or n >= 3: continue
            f1 = L.fair_fast(w, r); f0 = L.fair_fast(w, r - look)
            if not f0 or not f1 or abs(f1[0] - f0[0]) < jump: continue
            a = 0 if f1[0] > f0[0] else 1
            bk = L.book(w, a, r); bk0 = L.book(w, a, r - look)
            if not bk or bk[1] is None or bk[3] is None: continue
            mid = (bk[1] + bk[3]) / 2
            if not 0.10 <= mid <= 0.90: continue
            last = r; n += 1
            fb = L.book(w, a, r + lat)
            if not fb or fb[3] is None or fb[3] > mid + P["slack"]: continue
            price = fb[3]; q = min(P["shares"], fb[4] or 0)
            if q <= 0: continue
            mid0 = (bk0[1] + bk0[3]) / 2 if bk0 and bk0[1] is not None and bk0[3] is not None else mid
            won = (a == 0) == up
            out.append({"as": as_, "w": w, "tau": 300 - (r - S) / 1000, "mid": mid, "jump": abs(f1[0] - f0[0]),
                        "stale": mid - mid0 if a == 0 else (1 - mid) - (1 - mid0) if False else (mid - mid0),
                        "spread": bk[3] - bk[1], "price": price, "q": q,
                        "pnl": q * ((1.0 if won else 0.0) - price - 0.07 * price * (1 - price))})
    return out

def bucket(rows, key, edges):
    print(f"-- by {key}")
    for lo, hi in zip(edges[:-1], edges[1:]):
        sel = [r for r in rows if lo <= r[key] < hi]
        if len(sel) < 8: continue
        q = sum(r["q"] for r in sel); pnl = sum(r["pnl"] for r in sel)
        byt = collections.defaultdict(float)
        for r in sel: byt[r["w"]] += r["pnl"]
        v = np.array(list(byt.values())); t = v.mean() / (v.std(ddof=1) / np.sqrt(len(v))) if len(v) > 2 and v.std() > 0 else float("nan")
        print(f"   {key} [{lo}, {hi}): fills {len(sel):4d}  ¢/share {100*pnl/q:+6.2f}  t_time {t:+.2f}")

if __name__ == "__main__":
    P = {"jump": 0.06, "slack": 0.02, "look_ms": 1000, "lat_ms": 250, "shares": 100}
    rows_all = LV.load_rows(); rows = []
    for as_ in LV.ASSETS:
        rows += fills_with_features(LV.Live(asset=as_, rows=rows_all), as_, P)
    print("total fills", len(rows), "¢/share", round(100 * sum(r["pnl"] for r in rows) / sum(r["q"] for r in rows), 2))
    bucket(rows, "tau", [0, 30, 60, 120, 180, 240, 301])
    bucket(rows, "mid", [0.1, 0.3, 0.45, 0.55, 0.7, 0.91])
    bucket(rows, "jump", [0.06, 0.08, 0.10, 0.15, 1.0])
    # staleness: how much the favoured side's mid already moved during the look window, signed toward the jump
    for r in rows: r["moved"] = r["stale"] if True else 0
    bucket(rows, "moved", [-1, -0.005, 0.005, 0.02, 0.05, 1])
    bucket(rows, "spread", [0, 0.015, 0.025, 0.05, 1])
    by_as = collections.defaultdict(list)
    for r in rows: by_as[r["as"]].append(r)
    print("-- by asset:", {a: (len(v), round(100 * sum(x["pnl"] for x in v) / sum(x["q"] for x in v), 2)) for a, v in by_as.items()})
