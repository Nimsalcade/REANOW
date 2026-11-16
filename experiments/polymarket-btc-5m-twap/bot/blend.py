"""Does the market price carry information beyond the TWAP model? Logistic blend, time-split OOS."""
import sys, os, math
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
import engine as E

def logit(p): p = min(max(p, 1e-4), 1 - 1e-4); return math.log(p / (1 - p))

def fit(X, y, l2=1e-3, iters=50):
    w = np.zeros(X.shape[1])
    for _ in range(iters):
        p = 1 / (1 + np.exp(-X @ w))
        g = X.T @ (p - y) + l2 * w
        H = X.T @ (X * (p * (1 - p))[:, None]) + l2 * np.eye(X.shape[1])
        w -= np.linalg.solve(H, g)
    return w

def ll(p, y): p = np.clip(p, 1e-4, 1 - 1e-4); return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))

d = E.Data()
ws = sorted([w for w in d.windows if w.trades], key=lambda w: w.w)
cut = int(len(ws) * 0.7)
print(f"windows {len(ws)} train {cut} test {len(ws)-cut}")
print("tau   test_ll_model  test_ll_market  test_ll_blend   weights[model, market, bias]")
for tau in (290, 270, 240, 210, 180, 150, 120, 90, 60, 45, 30, 20, 15, 10):
    rows = []
    for w in ws:
        t = w.w + 300 - tau
        mk = E.Tape(w).last_price(t, 0, max_age=30)
        if mk is None: continue
        p, _ = E.fair(d, w, t)
        rows.append((w.w, logit(p), logit(mk), float(w.up)))
    A = np.array(rows)
    tr, te = A[A[:, 0] < ws[cut].w], A[A[:, 0] >= ws[cut].w]
    X = lambda M: np.column_stack([M[:, 1], M[:, 2], np.ones(len(M))])
    wgt = fit(X(tr), tr[:, 3])
    pb = 1 / (1 + np.exp(-X(te) @ wgt))
    pm = 1 / (1 + np.exp(-te[:, 1])); pk = 1 / (1 + np.exp(-te[:, 2]))
    print(f"{tau:4d}   {ll(pm, te[:,3]):.4f}         {ll(pk, te[:,3]):.4f}          {ll(pb, te[:,3]):.4f}         {np.round(wgt, 3)}")
