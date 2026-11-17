"""Strike-ladder pairs: the last 5-minute window of each 15-minute window settles on the same TWAP F.

Legs: Up on the lower strike Ka and Down on the higher strike Kb (Ka = min(K15, K5), Kb = max). The pair pays
$1 if F is outside [Ka, Kb) and $2 if Ka <= F < Kb. Asks for each leg are reconstructed from taker prints
(mirrored books): taker BUY of outcome o at p -> ask_o = p; taker SELL of 1-o at p -> ask_o = 1-p.
Timestamps are data-api seconds shifted by -2 s. Fee per taker share 0.07 * p * (1 - p).

Usage: python3 -I ladder.py ASSET FIFTEEN_JSON HIST_DIR
  FIFTEEN_JSON: output of ../gabagool/fetch_markets.py for the 15m markets
  HIST_DIR: the 5m backfill dir (events.jsonl + trades/<w>.json) from ../bot/backfill.py
"""
import collections
import json
import os
import sys

import numpy as np

SHIFT = 2


def fee(p):
    return 0.07 * p * (1 - p)


def asks_from_prints(prints, outcome):
    """[(t, ask)] for `outcome` reconstructed from taker prints [(t, oi, side, price, size), ...]."""
    out = []
    for t, oi, sd, p, s in prints:
        if oi == outcome and sd == 1:
            out.append((t - SHIFT, p))
        elif oi == 1 - outcome and sd == -1:
            out.append((t - SHIFT, 1 - p))
    out.sort()
    return out


def last_before(series, t, fresh):
    lo, hi, best = 0, len(series) - 1, None
    while lo <= hi:
        mid = (lo + hi) // 2
        if series[mid][0] <= t:
            best = mid; lo = mid + 1
        else:
            hi = mid - 1
    if best is None or t - series[best][0] > fresh:
        return None
    return series[best][1]


def main():
    asset, f15, hist = sys.argv[1], sys.argv[2], sys.argv[3]
    ev5 = {json.loads(l)["w"]: json.loads(l) for l in open(os.path.join(hist, "events.jsonl"))}
    mk15 = [m for m in json.load(open(f15)) if m["slug"].startswith(asset + "-")]
    rows = []
    for m in mk15:
        S = m["start"]
        e1, e3 = ev5.get(S), ev5.get(S + 600)
        p3 = os.path.join(hist, "trades", "%d.json" % (S + 600))
        if not e1 or not e3 or not os.path.exists(p3) or e1["open"] is None or e3["open"] is None or e3["close"] is None:
            continue
        K15, K5, F = e1["open"], e3["open"], e3["close"]
        if abs(K15 - K5) < 1e-9:
            continue
        lowUp_is_15 = K15 < K5           # Up on the lower strike, Down on the higher strike
        Ka, Kb = min(K15, K5), max(K15, K5)
        t15 = [(x[0], x[1], x[2], x[3], x[4]) for x in m["trades"]]
        t5 = [tuple(x) for x in json.load(open(p3))]
        if lowUp_is_15:
            legA, legB = asks_from_prints(t15, 0), asks_from_prints(t5, 1)   # Up15 + Down5
        else:
            legA, legB = asks_from_prints(t5, 0), asks_from_prints(t15, 1)   # Up5 + Down15
        payoff = 2.0 if Ka <= F < Kb else 1.0
        costs = []
        for t in range(S + 602, S + 898):
            a, b = last_before(legA, t, 3), last_before(legB, t, 3)
            if a is None or b is None:
                continue
            costs.append((t - S - 600, a + b + fee(a) + fee(b), a, b))
        if not costs:
            continue
        rows.append({"S": S, "gap": Kb - Ka, "gap_bp": 1e4 * (Kb - Ka) / Ka, "payoff": payoff, "costs": costs})
    print(f"{asset}: {len(rows)} 15m windows with both books printing in the last 5 minutes")
    allc = np.array([c[1] for r in rows for c in r["costs"]])
    print(f"   pair cost incl. fees over all observed seconds: median {np.median(allc):.4f}, p5 {np.percentile(allc,5):.4f}, min {allc.min():.4f}")
    print(f"   middle band (pays $2) in {np.mean([r['payoff']==2 for r in rows]):.1%} of windows; median strike gap {np.median([r['gap_bp'] for r in rows]):.2f} bp")
    sub1 = [r for r in rows if min(c[1] for c in r["costs"]) < 1.0]
    print(f"   windows where cost < $1 at some second (riskless): {len(sub1)} ({len(sub1)/len(rows):.1%}); seconds below $1: {np.mean(allc < 1.0):.2%}")
    # strategies: buy the pair once per window at the first second the cost is <= threshold
    for thr in (0.98, 0.99, 1.00, 1.02, 1.05, 1.10):
        pn = []
        for r in rows:
            hit = next((c for c in r["costs"] if c[1] <= thr), None)
            if hit:
                pn.append(r["payoff"] - hit[1])
        if pn:
            pn = np.array(pn)
            print(f"   buy first time cost <= {thr:.2f}: n {len(pn):3d}  mean {100*pn.mean():+6.2f} c/pair  worst {100*pn.min():+6.2f}  share>=0 {np.mean(pn>=0):.2f}  t {pn.mean()/(pn.std(ddof=1)/np.sqrt(len(pn))) if len(pn)>2 and pn.std()>0 else float('nan'):+.2f}")
    # fixed-time entry (no selection): buy the pair at window-minute 10.5, 12, 14
    for at in (30, 120, 240):
        pn = []
        for r in rows:
            c = next((c for c in r["costs"] if c[0] >= at), None)
            if c:
                pn.append(r["payoff"] - c[1])
        pn = np.array(pn)
        print(f"   fixed entry at +{at:3d}s of the last 5m: n {len(pn):3d}  mean {100*pn.mean():+6.2f} c/pair  t {pn.mean()/(pn.std(ddof=1)/np.sqrt(len(pn))):+.2f}")


if __name__ == "__main__":
    main()
