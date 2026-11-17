"""Search the buy-both-legs-on-dips rule for the best thresholds, then check them out of sample.

Rule per session (taker, 250 ms latency, 1 share per leg): buy the first side whose ask is <= X within the
first T1 seconds; then buy the other side the first time the pair costs <= 1 - m including both fees and
merge for $1. If the second leg has not come `cut` seconds after the first, sell the first leg at the bid
(cut=0: keep it and let the session settle). Parameters are chosen on one half of the sessions (by time)
and scored on the other half.

Usage: python3 -I dips_search.py
"""
import itertools
import math
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
import dips as D

STEP = D.STEP
XS = (0.05, 0.10, 0.15, 0.20, 0.25, 0.30, 0.35, 0.40, 0.45)
T1S = (30, 60, 120, 180, 240)
MS = (0.0, 0.02, 0.04, 0.06, 0.08, 0.10, 0.15, 0.20, 0.30)
CUTS = (0, 30, 60, 120)


def fee(p):
    return 0.07 * p * (1 - p)


def first_pair(mask):
    """First index j with mask[j] and mask[j + 1] (condition still true after the latency), else -1."""
    both = mask[:-1] & mask[1:]
    j = int(np.argmax(both)) if len(both) else 0
    return j if len(both) and both[j] else -1


def session_pnl(G, up, X, T1, m, cut):
    rs, A, Z, B = G
    with np.errstate(invalid="ignore"):
        lo = np.fmin(A[0], A[1])
        k = first_pair(lo[: int((T1 * 1000 - 5000) / STEP) + 1] <= X + 1e-9)
    if k < 0:
        return None
    ke = k + 1
    s1 = 0 if A[0, ke] <= X + 1e-9 else 1
    p1 = A[s1, ke]
    c1 = p1 + fee(p1)
    s2 = 1 - s1
    lim = 1 - m - c1
    end = len(rs) if not cut else min(len(rs), ke + 1 + int(cut * 1000 / STEP))
    with np.errstate(invalid="ignore"):
        c2 = A[s2, ke + 1:end] + fee(A[s2, ke + 1:end])
        j = first_pair(c2 <= lim + 1e-9)
    if j >= 0:
        return 1 - c1 - c2[j + 1], True
    if cut and end < len(rs):
        b = B[s1, end - 1]
        return ((b - fee(b)) if not np.isnan(b) else 0.0) - c1, False
    won = (s1 == 0) == up
    return (1.0 if won else 0.0) - c1, False


def score(cells, keys):
    pnl, by_t, comp = [], defaultdict(float), []
    for key in keys:
        r = cells.get(key)
        if r is None:
            continue
        pnl.append(r[0]); comp.append(r[1]); by_t[key[1]] += r[0]
    if len(pnl) < 20:
        return None
    pnl, tv = np.array(pnl), np.array(list(by_t.values()))
    t = tv.mean() / (tv.std(ddof=1) / math.sqrt(len(tv))) if len(tv) > 2 and tv.std() > 0 else float("nan")
    return {"n": len(pnl), "per": 100 * pnl.mean(), "complete": float(np.mean(comp)), "t": t}


def main():
    lives, grids = D.load()
    sessions = sorted(grids, key=lambda k: k[1])
    ws = sorted({w for _, w in sessions})
    cut_w = ws[len(ws) // 2]
    first = [k for k in sessions if k[1] < cut_w]
    second = [k for k in sessions if k[1] >= cut_w]
    ups = {(a, w): lives[a].F[w] >= lives[a].K[w] for a, w in sessions}
    configs = list(itertools.product(XS, T1S, MS, CUTS))
    res = {}
    for cfg in configs:
        res[cfg] = {key: session_pnl(grids[key], ups[key], *cfg) for key in sessions}
    def table(keys):
        return {cfg: score(res[cfg], keys) for cfg in configs}
    full, h1, h2 = table(sessions), table(first), table(second)
    fmt = lambda s: f"{s['per']:+6.2f} c/session (n {s['n']:3d}, pair done {s['complete']:4.0%}, t {s['t']:+5.2f})" if s else "      –"
    print(f"{len(sessions)} sessions, {len(configs)} rule combinations; halves split at {cut_w} ({len(first)} / {len(second)} sessions)")
    for name, a, b in (("first half", h1, h2), ("second half", h2, h1)):
        best = sorted((c for c in configs if a[c]), key=lambda c: -a[c]["per"])[:5]
        print(f"\nBest 5 chosen on the {name} -> scored on the other half")
        for c in best:
            print(f"  first leg <= {c[0]:.2f} by +{c[1]:3d}s, pair <= {1 - c[2]:.2f}, give up after {c[3] or 'never':>5}{'s' if c[3] else ' '}:  "
                  f"chosen half {fmt(a[c])}   other half {fmt(b[c])}")
    pos = [c for c in configs if full[c] and full[c]["per"] > 0]
    sig = [c for c in pos if full[c]["t"] > 2]
    print(f"\nAll sessions: {len(pos)} of {len(configs)} combinations positive, {len(sig)} with t > 2 "
          f"(about {0.025 * len(configs):.0f} expected by luck alone).")
    for c in sorted((c for c in configs if full[c]), key=lambda c: -full[c]["per"])[:5]:
        print(f"  first leg <= {c[0]:.2f} by +{c[1]:3d}s, pair <= {1 - c[2]:.2f}, give up after {c[3] or 'never'}: {fmt(full[c])}")
    both_pos = [c for c in configs if h1[c] and h2[c] and h1[c]["per"] > 0 and h2[c]["per"] > 0]
    print(f"Positive in BOTH halves: {len(both_pos)} combinations")
    for c in sorted(both_pos, key=lambda c: -min(h1[c]["per"], h2[c]["per"]))[:8]:
        print(f"  first leg <= {c[0]:.2f} by +{c[1]:3d}s, pair <= {1 - c[2]:.2f}, give up after {c[3] or 'never'}: "
              f"half 1 {fmt(h1[c])}  half 2 {fmt(h2[c])}")


if __name__ == "__main__":
    main()
