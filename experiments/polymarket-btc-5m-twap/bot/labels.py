"""Fetch official open/close for recorded windows that lack a label (e.g. after a recorder gap).

Writes rows {k:"label", as, w, open, close, t} to data/labels/labels.jsonl (read by live.load_rows).
"""
import datetime as dt, glob, gzip, json, os, time, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "data", "labels"); os.makedirs(OUT, exist_ok=True)
path = os.path.join(OUT, "labels.jsonl")
have, seen = set(), set()
for f in glob.glob(os.path.join(HERE, "data", "live", "*.jsonl*")) + [path]:
    if not os.path.exists(f): continue
    op = gzip.open if f.endswith(".gz") else open
    with op(f, "rt") as fh:
        for line in fh:
            try: r = json.loads(line)
            except ValueError: continue
            if r.get("k") == "label": have.add((r.get("as", "btc"), r["w"]))
            elif r.get("k") == "window": seen.add((r.get("as", "btc"), r["w"]))
now = time.time()
iso = lambda s: dt.datetime.fromtimestamp(s, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
out = open(path, "a"); n = 0
for as_, w in sorted(seen - have):
    if w + 330 > now: continue
    q = f"symbol={as_.upper()}&eventStartTime={iso(w)}&variant=fiveminute&endDate={iso(w+300)}&twapEnabled=true&twapLookbackSeconds=60"
    try:
        r = json.loads(urllib.request.urlopen(urllib.request.Request("https://polymarket.com/api/crypto/crypto-price?" + q, headers={"User-Agent": "Mozilla/5.0"}), timeout=20).read())
    except Exception as e:
        print("fail", as_, w, e); continue
    if r.get("completed") and r.get("closePrice") is not None:
        out.write(json.dumps({"k": "label", "as": as_, "w": w, "open": r["openPrice"], "close": r["closePrice"], "t": int(now * 1000)}) + "\n"); n += 1
print("labels added:", n)
