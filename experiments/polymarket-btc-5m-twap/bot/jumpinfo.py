"""At fair-value jump moments, does the jump predict the outcome beyond the market mid?
For each trigger (|fair(r) - fair(r-look)| >= jump), direction-adjusted: excess = outcome - mid,
also mid change over the next 5 s, by asset. Positive excess > fee means real information."""
import sys, os, bisect, collections
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
import live as LV

def study(L, jump=0.04, look=1000):
    rows = []
    for w in sorted(L.F):
        if w not in L.windows or (w, 0) not in L.tob or w not in L.K: continue
        up = L.F[w] >= L.K[w]
        S, nxt = w * 1000, 0
        for r in range(S + 5000, S + 295000, 100):
            if r < nxt: continue
            f1 = L.fair_fast(w, r); f0 = L.fair_fast(w, r - look)
            if not f1 or not f0 or abs(f1[0] - f0[0]) < jump: continue
            b = L.book(w, 0, r); b5 = L.book(w, 0, r + 5000)
            if not b or b[1] is None or b[3] is None: continue
            mid = (b[1] + b[3]) / 2
            if not 0.05 < mid < 0.95: continue
            d = 1 if f1[0] > f0[0] else -1
            y = 1.0 if up else 0.0
            mid5 = (b5[1] + b5[3]) / 2 if b5 and b5[1] is not None and b5[3] is not None else mid
            rows.append((w, d * (y - mid), d * (f1[0] - mid), d * (mid5 - mid)))
            nxt = r + 5000
    return rows

rows_all = LV.load_rows()
print("asset  triggers windows  mean(outcome-mid)  t     mean(fair-mid)  mean(mid move 5s)")
pool = []
for as_ in LV.ASSETS:
    L = LV.Live(asset=as_, rows=rows_all)
    rs = study(L)
    if len(rs) < 5: continue
    pool += [(as_,) + r for r in rs]
    byw = collections.defaultdict(list)
    for w, ex, fm, mv in rs: byw[w].append(ex)
    wm = np.array([np.mean(v) for v in byw.values()])
    t = wm.mean() / (wm.std(ddof=1) / np.sqrt(len(wm))) if len(wm) > 2 else float("nan")
    print(f"{as_:5s}  {len(rs):5d}   {len(byw):4d}     {np.mean([r[1] for r in rs]):+.3f}       {t:+.2f}    {np.mean([r[2] for r in rs]):+.3f}          {np.mean([r[3] for r in rs]):+.3f}")
ex = np.array([p[2] for p in pool])
print(f"POOLED {len(pool)} triggers: mean(outcome-mid) {ex.mean():+.3f} (se {ex.std()/np.sqrt(len(ex)):.3f}); mean(fair-mid) {np.mean([p[3] for p in pool]):+.3f}")
