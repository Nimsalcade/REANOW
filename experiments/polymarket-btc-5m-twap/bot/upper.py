"""Upper-bound taker edge: buy at last trade + half tick when model - price - fee > thr, fixed tau."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
import engine as E
d = E.Data(); ws = [w for w in d.windows if w.trades]
print("tau  thr   n    win   avg_px  pnl/share   t")
for tau in (270, 240, 180, 120, 90, 60, 45, 30, 20, 15, 10, 7, 5):
    for thr in (0.0, 0.03, 0.06, 0.10):
        pn = []
        for w in ws:
            t = w.w + 300 - tau
            tape = E.Tape(w)
            p, _ = E.fair(d, w, t)
            for oi, fp in ((0, p), (1, 1 - p)):
                mk = tape.last_price(t, oi, max_age=10)
                if mk is None: continue
                px = min(0.999, mk + 0.005)
                if fp - px - E.fee(px) > thr and 0.03 < px < 0.99:
                    won = (oi == 0) == w.up
                    pn.append((1.0 if won else 0.0) - px - E.fee(px)); break
        if len(pn) > 5:
            a = np.array(pn)
            print(f"{tau:4d} {thr:.2f} {len(a):4d}  {np.mean(a>0):.2f}  {np.mean([0]):.0f}      {a.mean():+.4f}   {a.mean()/(a.std(ddof=1)/np.sqrt(len(a))):+.2f}")
