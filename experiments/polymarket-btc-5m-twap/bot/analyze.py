"""Model calibration vs outcomes, and model vs market (trade-implied) log loss by time to close."""
import math, sys, json, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
import engine as E

def ll(p, y):
    p = min(max(p, 1e-4), 1 - 1e-4)
    return -(math.log(p) if y else math.log(1 - p))

def model_calibration(d, taus=(300, 270, 240, 180, 120, 90, 60, 45, 30, 20, 10, 5, 3, 1), sig=1.0, meas=1.2):
    print(f"sig_mult={sig} meas={meas}")
    print("tau  n    logloss_model  brier_model  logloss_mkt  brier_mkt  n_mkt  (market = last trade price)")
    for tau in taus:
        lm, bm, lk, bk, nk = [], [], [], [], 0
        for w in d.windows:
            t = w.w + 300 - tau
            p, _ = E.fair(d, w, t, sig, meas)
            lm.append(ll(p, w.up)); bm.append((p - w.up) ** 2)
            if w.trades:
                mk = E.Tape(w).last_price(t, 0, max_age=15)
                if mk is not None:
                    nk += 1; lk.append(ll(mk, w.up)); bk.append((mk - w.up) ** 2)
        print(f"{tau:4d} {len(lm):4d}  {np.mean(lm):.4f}        {np.mean(bm):.4f}      "
              + (f"{np.mean(lk):.4f}       {np.mean(bk):.4f}    {nk}" if nk else "  -"))

if __name__ == "__main__":
    d = E.Data()
    for sig in [float(x) for x in (sys.argv[1:] or ["1.0"])]:
        model_calibration(d, sig=sig)
