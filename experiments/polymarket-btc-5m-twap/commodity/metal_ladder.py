"""Gold/silver ladder: last 15m window of each hour and the 1h window settle on the same Chainlink spot F.
Up(lower strike) + Down(higher strike) pays $1, or $2 if F lands between the strikes."""
import collections, concurrent.futures as cf, gzip, json, sys, time, urllib.parse, urllib.request
import numpy as np
UA = {"User-Agent": "Mozilla/5.0", "Accept-Encoding": "gzip"}
def get(u):
    for i in range(5):
        try:
            with urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=60) as r:
                b = r.read(); return json.loads(gzip.decompress(b) if r.headers.get("Content-Encoding") == "gzip" else b)
        except Exception:
            time.sleep(2 * (i + 1))
    return {"data": [], "pagination": {}}
def tape(cond):
    rows, cur = [], None
    while True:
        d = get(f"https://data-api.polymarket.com/v2/trades?conditionId={cond}&limit=500" + (f"&cursor={urllib.parse.quote(cur)}" if cur else ""))
        rows += [(x["timestamp"] - 2, x["outcome_index"], 1 if x["side"] == "BUY" else -1, x["price"], x["size"]) for x in d["data"]]
        if not d.get("pagination", {}).get("has_more"): break
        cur = d["pagination"]["next_cursor"]
    return sorted(rows)
def asks(tr, o):
    return [(t, p if oi == o else 1 - p) for t, oi, sd, p, s in tr if (oi == o and sd == 1) or (oi == 1 - o and sd == -1)]
def last(series, t, fresh=5):
    v = None
    for ts, p in series:
        if ts > t: break
        if t - ts <= fresh: v = p
    return v
fee = lambda p: 0.07 * p * (1 - p)
ev = json.load(open("commodity_events.json"))
idx = {(r["asset"], r["dur"], r["start"]): r for r in ev if r["K"] is not None and r["F"] is not None}
pairs = []
for (a, d, s), h in idx.items():
    if d != "1h": continue
    q = idx.get((a, "15m", s + 2700))
    if q and abs(q["F"] - h["F"]) < 1e-9:
        pairs.append((h, q))
print("hour/15m pairs with identical F:", len(pairs), "of", sum(1 for k in idx if k[1] == "1h"), "hours")
with cf.ThreadPoolExecutor(8) as pool:
    tapes = dict(zip([c for h, q in pairs for c in (h["cond"], q["cond"])], pool.map(tape, [c for h, q in pairs for c in (h["cond"], q["cond"])])))
res = []
for h, q in pairs:
    K1, K2, F, S = h["K"], q["K"], h["F"], q["start"]
    if abs(K1 - K2) < 1e-12: continue
    t1, t2 = tapes[h["cond"]], tapes[q["cond"]]
    A, B = (asks(t1, 0), asks(t2, 1)) if K1 < K2 else (asks(t2, 0), asks(t1, 1))
    Ka, Kb = min(K1, K2), max(K1, K2); payoff = 2.0 if Ka <= F < Kb else 1.0
    costs = []
    for t in range(S + 5, S + 895, 2):
        x, y = last(A, t), last(B, t)
        if x is not None and y is not None:
            costs.append((t - S, x + y + fee(x) + fee(y)))
    if costs: res.append((h["asset"], payoff, costs, 1e4 * (Kb - Ka) / Ka))
print("pairs with both legs printing:", len(res), "| middle band share:", round(np.mean([r[1] == 2 for r in res]), 3), "| median strike gap bp:", round(float(np.median([r[3] for r in res])), 1))
allc = np.array([c for r in res for _, c in r[2]])
if len(allc):
    print(f"pair cost incl. fees: median {np.median(allc):.3f}  p5 {np.percentile(allc,5):.3f}  min {allc.min():.3f}; share of observed instants < $1: {np.mean(allc<1):.2%}")
for thr in (0.97, 1.0, 1.05, 1.1):
    pn = [r[1] - next(c for _, c in r[2] if c <= thr) for r in res if any(c <= thr for _, c in r[2])]
    if pn: pn = np.array(pn); print(f"   buy first time cost <= {thr}: n {len(pn)}  mean {100*pn.mean():+.2f} c/pair  share>=0 {np.mean(pn>=0):.2f}")
