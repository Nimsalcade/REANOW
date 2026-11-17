"""Cross-check the ladder bot from public data for a time range, all assets.

For every 15-minute window in [START, END): fetch the 15m market and its final 5m market from Gamma
(official priceToBeat / finalPrice), both taker tapes from data-api v2, and measure the pair
Up(lower strike) + Down(higher strike): asks reconstructed from prints (taker BUY o at p -> ask_o = p;
taker SELL 1-o at p -> ask_o = 1-p; ts - 2 s), cost incl. fee 0.07·p(1-p) per leg, whether F matches, and the
P&L of buying at the first instant the cost is <= 1 - edge.

Usage: python3 -I ladder_check.py START_ISO END_ISO [assets]
"""
import concurrent.futures as cf
import datetime as dt
import gzip
import json
import sys
import time
import urllib.parse
import urllib.request

import numpy as np

UA = {"User-Agent": "Mozilla/5.0", "Accept-Encoding": "gzip"}


def get(u):
    for i in range(6):
        try:
            with urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=60) as r:
                b = r.read()
                return json.loads(gzip.decompress(b) if r.headers.get("Content-Encoding") == "gzip" else b)
        except Exception:
            time.sleep(1.5 * (i + 1))
    raise RuntimeError(u)


def tape(cond):
    rows, cur = [], None
    while True:
        d = get(f"https://data-api.polymarket.com/v2/trades?conditionId={cond}&limit=500" + (f"&cursor={urllib.parse.quote(cur)}" if cur else ""))
        rows += [(x["timestamp"] - 2, x["outcome_index"], 1 if x["side"] == "BUY" else -1, x["price"], x["size"]) for x in d["data"]]
        if not d["pagination"].get("has_more"):
            break
        cur = d["pagination"]["next_cursor"]
    return sorted(rows)


def asks(tr, o):
    return [(t, p if oi == o else 1 - p, s) for t, oi, sd, p, s in tr if (oi == o and sd == 1) or (oi == 1 - o and sd == -1)]


def last(series, t, fresh=3):
    v = None
    for ts, p, s in series:
        if ts > t:
            break
        if t - ts <= fresh:
            v = (p, s)
    return v


fee = lambda p: 0.07 * p * (1 - p)


def main():
    t0 = int(dt.datetime.fromisoformat(sys.argv[1].replace("Z", "+00:00")).timestamp())
    t1 = int(dt.datetime.fromisoformat(sys.argv[2].replace("Z", "+00:00")).timestamp())
    assets = (sys.argv[3] if len(sys.argv) > 3 else "btc,eth,sol,xrp,doge,bnb,hype").split(",")
    slugs = []
    for a in assets:
        for S in range(t0 - t0 % 900, t1, 900):
            slugs += [f"{a}-updown-15m-{S}", f"{a}-updown-5m-{S + 600}"]
    ev = {}
    for i in range(0, len(slugs), 40):
        for e in get("https://gamma-api.polymarket.com/events?limit=50&" + "&".join("slug=" + s for s in slugs[i:i + 40])):
            ev[e["slug"]] = e
    pairs = []
    for a in assets:
        for S in range(t0 - t0 % 900, t1, 900):
            e15, e5 = ev.get(f"{a}-updown-15m-{S}"), ev.get(f"{a}-updown-5m-{S + 600}")
            if not e15 or not e5:
                continue
            m15, m5 = e15.get("eventMetadata") or {}, e5.get("eventMetadata") or {}
            if None in (m15.get("priceToBeat"), m15.get("finalPrice"), m5.get("priceToBeat"), m5.get("finalPrice")):
                continue
            pairs.append((a, S, e15, e5, m15, m5))
    conds = [c for p in pairs for c in (p[2]["markets"][0]["conditionId"], p[3]["markets"][0]["conditionId"])]
    with cf.ThreadPoolExecutor(8) as pool:
        tapes = dict(zip(conds, pool.map(tape, conds)))
    rows = []
    for a, S, e15, e5, m15, m5 in pairs:
        K15, K5, F15, F5 = m15["priceToBeat"], m5["priceToBeat"], m15["finalPrice"], m5["finalPrice"]
        t15, t5 = tapes[e15["markets"][0]["conditionId"]], tapes[e5["markets"][0]["conditionId"]]
        A, B = (asks(t15, 0), asks(t5, 1)) if K15 < K5 else (asks(t5, 0), asks(t15, 1))
        Ka, Kb = min(K15, K5), max(K15, K5)
        payoff = 2.0 if Ka <= F15 < Kb else 1.0
        costs = []
        for t in range(S + 602, S + 898):
            x, y = last(A, t), last(B, t)
            if x and y:
                costs.append((t - S - 600, x[0] + y[0] + fee(x[0]) + fee(y[0]), min(x[1], y[1])))
        rows.append({"asset": a, "S": S, "same_F": abs(F15 - F5) < 1e-9, "payoff": payoff, "costs": costs,
                     "prints": (len(t15), len(t5))})
    print(f"window range {sys.argv[1]} .. {sys.argv[2]}: {len(rows)} asset-windows with official results")
    print(f"   F identical in both markets: {sum(r['same_F'] for r in rows)}/{len(rows)}")
    print(f"   middle band (pair pays $2): {np.mean([r['payoff'] == 2 for r in rows]):.1%}")
    seen = [r for r in rows if r["costs"]]
    allc = np.array([c[1] for r in seen for c in r["costs"]])
    print(f"   windows with both legs printing in the last 5 m: {len(seen)}; pair cost incl. fees median {np.median(allc):.3f}, min {allc.min():.3f}")
    for edge in (0.0, 0.005, 0.02):
        hits = []
        for r in seen:
            c = next((c for c in r["costs"] if c[1] <= 1 - edge), None)
            if c:
                hits.append((r, c))
        if hits:
            pn = np.array([r["payoff"] - c[1] for r, c in hits])
            per_asset = {}
            for r, c in hits:
                per_asset[r["asset"]] = per_asset.get(r["asset"], 0) + 1
            print(f"   cost <= {1 - edge:.3f}: {len(hits)} windows {per_asset}; P&L per pair mean {100 * pn.mean():+.2f} c, min {100 * pn.min():+.2f} c; "
                  f"print size at trigger (min of legs) median {np.median([c[2] for r, c in hits]):.1f} shares; seconds below: {sum(1 for r in seen for c in r['costs'] if c[1] <= 1 - edge)}")
        else:
            print(f"   cost <= {1 - edge:.3f}: 0 windows")
    for r in seen:
        m = min(c[1] for c in r["costs"])
        if m < 1.0:
            print(f"      {r['asset']} {dt.datetime.fromtimestamp(r['S'], dt.timezone.utc):%H:%M} min cost {m:.4f} payoff ${r['payoff']:.0f}")


if __name__ == "__main__":
    main()
