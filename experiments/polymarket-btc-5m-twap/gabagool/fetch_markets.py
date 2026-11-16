"""Fetch resolved crypto Up/Down markets (Gamma) and their full taker tapes (data-api v2) into one JSON.
Usage: python3 -I fetch_markets.py OUT.json ASSETS DURATION START_ISO END_ISO   e.g. btc,eth 15m 2025-10-29T00:00:00Z 2025-10-31T00:00:00Z"""
import concurrent.futures as cf, datetime as dt, gzip, json, sys, time, urllib.parse, urllib.request
UA = {"User-Agent": "Mozilla/5.0", "Accept-Encoding": "gzip"}
def get(u, tries=6):
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=60) as r:
                b = r.read()
                return json.loads(gzip.decompress(b) if r.headers.get("Content-Encoding") == "gzip" else b)
        except Exception:
            time.sleep(1.5 * (i + 1))
    raise RuntimeError(u)
out, assets, dur = sys.argv[1], sys.argv[2].split(","), sys.argv[3]
step = {"5m": 300, "15m": 900}[dur]
t0 = int(dt.datetime.fromisoformat(sys.argv[4].replace("Z", "+00:00")).timestamp())
t1 = int(dt.datetime.fromisoformat(sys.argv[5].replace("Z", "+00:00")).timestamp())
slugs = [f"{a}-updown-{dur}-{t}" for a in assets for t in range(t0, t1, step)]
events = []
for i in range(0, len(slugs), 40):
    events += get("https://gamma-api.polymarket.com/events?limit=50&" + "&".join("slug=" + s for s in slugs[i:i + 40]))
def tape(e):
    m = e["markets"][0]
    try:
        prices = json.loads(m["outcomePrices"])
    except Exception:
        return None
    if prices not in (["1", "0"], ["0", "1"]):
        return None
    rows, cur = [], None
    while True:
        d = get(f"https://data-api.polymarket.com/v2/trades?conditionId={m['conditionId']}&limit=500" + (f"&cursor={urllib.parse.quote(cur)}" if cur else ""))
        rows += [[x["timestamp"], x["outcome_index"], 1 if x["side"] == "BUY" else -1, x["price"], x["size"], x["proxy_wallet"]] for x in d["data"]]
        if not d["pagination"].get("has_more"):
            break
        cur = d["pagination"]["next_cursor"]
    return {"slug": e["slug"], "start": int(e["slug"].rsplit("-", 1)[1]), "duration": step, "up_won": prices == ["1", "0"],
            "fees": m.get("feesEnabled"), "trades": rows}
with cf.ThreadPoolExecutor(8) as pool:
    mk = [x for x in pool.map(tape, events) if x]
json.dump(mk, open(out, "w"))
print(out, "markets", len(mk), "trades", sum(len(m["trades"]) for m in mk), "fees", {str(m["fees"]) for m in mk})
