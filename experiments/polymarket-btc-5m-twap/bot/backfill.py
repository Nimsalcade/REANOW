"""Backfill historical BTC 5-minute windows for replay backtests.

Writes under data/hist/:
  events.jsonl            one row per window: w, cond, tokens, open, close, cfg, fee
  trades/<w>.json         [[ts, outcome_index, side(+1 buy/-1 sell), price, size], ...]
  twap5s/<w>.json         [[ts_ms, value], ...] official TWAP60 series at 5 s
  binance1s/<hour>.json   [[ts_ms, open, high, low, close, volume], ...]

Usage: python3 -I backfill.py DAYS
"""

import concurrent.futures as cf
import datetime as dt
import gzip
import json
import os
import sys
import time
import urllib.parse
import urllib.request

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "hist")
UA = {"User-Agent": "Mozilla/5.0", "Accept-Encoding": "gzip"}


def get(url, tries=6):
    delay = 1.0
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=30) as r:
                body = r.read()
                if r.headers.get("Content-Encoding") == "gzip":
                    body = gzip.decompress(body)
                return json.loads(body)
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and attempt < tries - 1:
                time.sleep(delay); delay *= 2; continue
            raise
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            if attempt < tries - 1:
                time.sleep(delay); delay *= 2; continue
            raise


def iso(sec):
    return dt.datetime.fromtimestamp(sec, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def fetch_events(starts):
    rows = []
    for i in range(0, len(starts), 40):
        chunk = starts[i:i + 40]
        q = "&".join("slug=btc-updown-5m-%d" % s for s in chunk)
        for e in get("https://gamma-api.polymarket.com/events?limit=50&" + q):
            m = e["markets"][0]
            meta = e.get("eventMetadata") or {}
            rows.append({
                "w": int(e["slug"].rsplit("-", 1)[1]),
                "cond": m["conditionId"],
                "tokens": json.loads(m["clobTokenIds"]),
                "outcomes": json.loads(m["outcomes"]),
                "open": meta.get("priceToBeat"),
                "close": meta.get("finalPrice"),
                "cfg": m.get("cryptoMarketConfig"),
                "fee": m.get("feeSchedule"),
                "volume": m.get("volumeNum"),
            })
    return sorted(rows, key=lambda r: r["w"])


def fetch_trades(ev):
    path = os.path.join(ROOT, "trades", "%d.json" % ev["w"])
    if os.path.exists(path):
        return ev["w"], "cached"
    out, cursor = [], None
    while True:
        url = "https://data-api.polymarket.com/v2/trades?conditionId=%s&limit=500" % ev["cond"]
        if cursor:
            url += "&cursor=" + urllib.parse.quote(cursor)
        d = get(url)
        for x in d["data"]:
            out.append([x["timestamp"], x["outcome_index"], 1 if x["side"] == "BUY" else -1, x["price"], x["size"]])
        pg = d.get("pagination") or {}
        if not pg.get("has_more") or not pg.get("next_cursor"):
            break
        cursor = pg["next_cursor"]
    out.sort()
    with open(path, "w") as f:
        json.dump(out, f)
    return ev["w"], len(out)


def fetch_twap(ev):
    path = os.path.join(ROOT, "twap5s", "%d.json" % ev["w"])
    if os.path.exists(path):
        return ev["w"], "cached"
    w = ev["w"]
    q = ("symbol=BTC&eventStartTime=%s&variant=fiveminute&endDate=%s&twapEnabled=true&twapLookbackSeconds=60"
         % (iso(w), iso(w + 300)))
    d = get("https://polymarket.com/api/crypto/price-history?" + q)
    with open(path, "w") as f:
        json.dump([[x["timestamp"], x["value"]] for x in d], f)
    return w, len(d)


def fetch_binance(hour_start):
    path = os.path.join(ROOT, "binance1s", "%d.json" % hour_start)
    if os.path.exists(path):
        return hour_start, "cached"
    rows, t = [], hour_start * 1000
    end = (hour_start + 3600) * 1000
    while t < end:
        d = get("https://data-api.binance.vision/api/v3/klines?symbol=BTCUSDT&interval=1s&startTime=%d&limit=1000" % t)
        if not d:
            break
        for k in d:
            if k[0] >= end:
                break
            rows.append([k[0], float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[5])])
        t = d[-1][0] + 1000
    with open(path, "w") as f:
        json.dump(rows, f)
    return hour_start, len(rows)


def main():
    days = float(sys.argv[1]) if len(sys.argv) > 1 else 3
    for sub in ("trades", "twap5s", "binance1s"):
        os.makedirs(os.path.join(ROOT, sub), exist_ok=True)
    now = int(time.time())
    last_closed = now - now % 300 - 300
    first = last_closed - int(days * 86400)
    starts = list(range(first, last_closed + 1, 300))
    events = fetch_events(starts)
    events = [e for e in events if e["close"] is not None and (e["cfg"] or {}).get("twapEnabled")]
    with open(os.path.join(ROOT, "events.jsonl"), "w") as f:
        for e in events:
            f.write(json.dumps(e) + "\n")
    print("events", len(events), iso(events[0]["w"]), "->", iso(events[-1]["w"]), flush=True)

    hours = list(range(first - first % 3600 - 3600, last_closed + 3600, 3600))
    with cf.ThreadPoolExecutor(8) as pool:
        jobs = [pool.submit(fetch_binance, h) for h in hours]
        jobs += [pool.submit(fetch_twap, e) for e in events]
        jobs += [pool.submit(fetch_trades, e) for e in events]
        done = 0
        for j in cf.as_completed(jobs):
            try:
                j.result()
            except Exception as exc:  # keep going; report failures
                print("job failed:", exc, flush=True)
            done += 1
            if done % 200 == 0:
                print("done", done, "/", len(jobs), flush=True)
    print("backfill complete", flush=True)


if __name__ == "__main__":
    main()
