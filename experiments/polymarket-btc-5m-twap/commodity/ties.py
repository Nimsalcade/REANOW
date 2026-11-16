"""Commodity Up/Down markets: how often does final == price to beat (a tie resolves Up), and when?
Usage: python3 -I ties.py DAYS"""
import collections, datetime as dt, gzip, json, sys, time, urllib.request
UA = {"User-Agent": "Mozilla/5.0", "Accept-Encoding": "gzip"}
def get(u):
    for i in range(5):
        try:
            with urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=60) as r:
                b = r.read(); return json.loads(gzip.decompress(b) if r.headers.get("Content-Encoding") == "gzip" else b)
        except Exception:
            time.sleep(2 * (i + 1))
    return []
days = float(sys.argv[1]) if len(sys.argv) > 1 else 3
now = int(time.time()); end = now - now % 900 - 900; start = end - int(days * 86400)
rows = []
for asset in ("xau", "xag", "wti", "natgas"):
    for dur, step in (("15m", 900), ("1h", 3600)):
        slugs = [f"{asset}-updown-{dur}-{t}" for t in range(start - start % step, end, step)]
        for i in range(0, len(slugs), 40):
            for e in get("https://gamma-api.polymarket.com/events?limit=50&" + "&".join("slug=" + s for s in slugs[i:i + 40])):
                m = e["markets"][0]; meta = e.get("eventMetadata") or {}
                try: prices = json.loads(m.get("outcomePrices") or "[]")
                except Exception: prices = []
                rows.append({"slug": e["slug"], "asset": asset, "dur": dur, "start": int(e["slug"].rsplit("-", 1)[1]),
                             "K": meta.get("priceToBeat"), "F": meta.get("finalPrice"), "prices": prices,
                             "volume": m.get("volumeNum") or 0, "cond": m["conditionId"], "closed": m.get("closed")})
json.dump(rows, open("commodity_events.json", "w"))
res = [r for r in rows if r["K"] is not None and r["F"] is not None]
print(f"windows with K and F: {len(res)} of {len(rows)} listed ({dt.datetime.utcfromtimestamp(start):%m-%d %H:%M} .. {dt.datetime.utcfromtimestamp(end):%m-%d %H:%M} UTC)")
by = collections.defaultdict(lambda: [0, 0, 0.0])
for r in res:
    k = (r["asset"], r["dur"]); by[k][0] += 1; by[k][1] += abs(r["F"] - r["K"]) < 1e-12; by[k][2] += r["volume"]
for k, (n, t, v) in sorted(by.items()):
    print(f"   {k[0]:6s} {k[1]:3s}: windows {n:4d}  ties {t:4d} ({100*t/n:5.1f}%)  volume ${v:,.0f}")
ties = [r for r in res if abs(r["F"] - r["K"]) < 1e-12]
hours = collections.Counter(dt.datetime.utcfromtimestamp(r["start"]).strftime("%a %H") for r in ties)
print("tie windows by weekday/hour UTC (top):", hours.most_common(15))
print("tie outcomes (Up price after resolution):", collections.Counter(json.dumps(r["prices"]) for r in ties).most_common(3))
