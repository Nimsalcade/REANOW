"""Do settled US "Highest temperature" markets show the bias implied by their rules?

The qualifying value is the max of the hourly routine-METAR temperature remarks, which is never above the day's
true (continuous) high that forecasts predict. If traders price from forecasts, the market's expected bucket sits
above the bucket that wins. For each settled event: bucket prices at a fixed local time on the observation day
(CLOB prices-history), normalised to sum to 1; the market's expected value (bucket midpoints, open-ended buckets at
their edge -/+ 1 F); the winning bucket from the resolution; and the P&L of a rule that, at that time, buys the
bucket just below the market's favourite and sells (buys No on) the bucket just above it.

Usage: python3 -I weather_bias.py [days=30] [local_hour=10]
"""
import concurrent.futures as cf
import datetime as dt
import gzip
import json
import math
import re
import sys
import time
import urllib.request
from collections import defaultdict
from zoneinfo import ZoneInfo

UA = {"User-Agent": "Mozilla/5.0", "Accept-Encoding": "gzip"}
US = {"NYC": "America/New_York", "Miami": "America/New_York", "Atlanta": "America/New_York", "Austin": "America/Chicago",
      "Houston": "America/Chicago", "Dallas": "America/Chicago", "Chicago": "America/Chicago", "Denver": "America/Denver",
      "Seattle": "America/Los_Angeles", "Los Angeles": "America/Los_Angeles", "San Francisco": "America/Los_Angeles"}
MONTHS = {m: i for i, m in enumerate(["january", "february", "march", "april", "may", "june", "july", "august",
                                      "september", "october", "november", "december"], 1)}


def get(u):
    for i in range(4):
        try:
            with urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=60) as r:
                b = r.read()
                return json.loads(gzip.decompress(b) if r.headers.get("Content-Encoding") == "gzip" else b)
        except Exception:
            time.sleep(1.5 * (i + 1))
    return None


def bucket(title):
    r = re.search(r"(-?\d+)\s*[-–]\s*(-?\d+)", title)
    if r:
        return int(r.group(1)), int(r.group(2))
    v = int(re.search(r"-?\d+", title).group(0))
    return (v - 1, v - 1) if "below" in title else (v + 1, v + 1) if ("higher" in title or "above" in title) else (v, v)


def price_at(token, ts):
    h = get(f"https://clob.polymarket.com/prices-history?market={token}&startTs={ts - 1800}&endTs={ts + 1800}&fidelity=1")
    pts = (h or {}).get("history") or []
    before = [p for p in pts if p["t"] <= ts]
    return float((before[-1] if before else pts[0])["p"]) if pts else None


def main():
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 30
    hour = int(sys.argv[2]) if len(sys.argv) > 2 else 10
    today = dt.datetime.now(dt.timezone.utc).date()
    want = []
    for d in range(1, days + 1):
        day = today - dt.timedelta(days=d)
        for city in US:
            slug = f"highest-temperature-in-{city.lower().replace(' ', '-')}-on-{day.strftime('%B').lower()}-{day.day}-{day.year}"
            want.append((city, day, slug))
    with cf.ThreadPoolExecutor(8) as pool:
        evs = list(pool.map(lambda w: get(f"https://gamma-api.polymarket.com/events?slug={w[2]}"), want))
    jobs = []
    for (city, day, slug), ev in zip(want, evs):
        if not ev:
            continue
        e = ev[0]
        ms = [m for m in e["markets"] if m.get("outcomePrices")]
        win = [m for m in ms if json.loads(m["outcomePrices"])[0] in ("1", "1.0")]
        if len(win) != 1:
            continue
        ts = int(dt.datetime(day.year, day.month, day.day, hour, tzinfo=ZoneInfo(US[city])).timestamp())
        jobs.append((city, day, ts, [(m.get("groupItemTitle"), json.loads(m["clobTokenIds"])[0], m is win[0]) for m in ms]))
    print(f"{len(jobs)} settled US highest-temperature events in the last {days} days; prices at {hour:02d}:00 local")
    flat = [(j, k) for j in range(len(jobs)) for k in range(len(jobs[j][3]))]
    with cf.ThreadPoolExecutor(10) as pool:
        px = list(pool.map(lambda jk: price_at(jobs[jk[0]][3][jk[1]][1], jobs[jk[0]][2]), flat))
    prices = defaultdict(dict)
    for (j, k), p in zip(flat, px):
        if p is not None:
            prices[j][k] = p
    diffs, rule, rows_by_off = [], [], defaultdict(lambda: [0.0, 0.0, 0])
    fee = lambda p: 0.05 * p * (1 - p)
    for j, (city, day, ts, legs) in enumerate(jobs):
        P = prices[j]
        if len(P) < len(legs) - 1:
            continue
        mids = [sum(bucket(t)) / 2 for t, _, _ in legs]
        tot = sum(P.get(k, 0.0) for k in range(len(legs)))
        if tot <= 0.5:
            continue
        exp = sum(P.get(k, 0.0) / tot * mids[k] for k in range(len(legs)))
        w = next(k for k, l in enumerate(legs) if l[2])
        diffs.append(mids[w] - exp)
        order = sorted(range(len(legs)), key=lambda k: mids[k])
        fav = max(order, key=lambda k: P.get(k, 0.0))
        i = order.index(fav)
        for off in (-2, -1, 0, 1, 2):
            if 0 <= i + off < len(order):
                k = order[i + off]
                rows_by_off[off][0] += P.get(k, 0.0) / tot
                rows_by_off[off][1] += 1.0 if k == w else 0.0
                rows_by_off[off][2] += 1
        if 0 < i < len(order) - 1:
            below, above = order[i - 1], order[i + 1]
            pb, pa = P.get(below, 0.0), P.get(above, 0.0)
            r = ((1.0 if below == w else 0.0) - pb - fee(pb)) + ((0.0 if above == w else 1.0) - (1 - pa) - fee(1 - pa))
            rule.append(r)
    if not diffs:
        print("no events with prices"); return
    m, s = sum(diffs) / len(diffs), (sum((x - sum(diffs) / len(diffs)) ** 2 for x in diffs) / (len(diffs) - 1)) ** 0.5
    print(f"winning bucket minus market-expected high: mean {m:+.2f} F (se {s / math.sqrt(len(diffs)):.2f}, n {len(diffs)})")
    print("calibration by bucket position relative to the market favourite (priced probability vs how often it won):")
    for off in sorted(rows_by_off):
        p, wn, n = rows_by_off[off]
        print(f"   {off:+d}: priced {p / n:5.1%}   won {wn / n:5.1%}   (n {n})")
    if rule:
        mr = sum(rule) / len(rule)
        sr = (sum((x - mr) ** 2 for x in rule) / (len(rule) - 1)) ** 0.5
        print(f"rule 'buy the bucket below the favourite + buy No on the bucket above', at mid prices incl. fees: "
              f"{100 * mr:+.1f} c per event (se {100 * sr / math.sqrt(len(rule)):.1f}, n {len(rule)})")


if __name__ == "__main__":
    main()
