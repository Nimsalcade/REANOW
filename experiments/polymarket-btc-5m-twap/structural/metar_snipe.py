"""METAR sniping: how long do bids on a weather bucket survive after a published report has made it impossible?

For each settled US "Highest/Lowest temperature" event of the last N days: replay the station's routine METARs on
the local date with the settlement rules (T-group tenths of a degree C, else body whole degrees; SPECI excluded;
F = C * 9/5 + 32 rounded half up). Each report that moves the running extreme into a new bucket kills the buckets
it passes, at the moment the report was published (aviationweather.gov receiptTime). Checks: the replayed final
bucket must equal the official winner. Then, from data-api v2 trades of each killed bucket, every trade after the
kill that sold its Yes (taker SELL Yes, or taker BUY No) captured a riskless profit of the Yes price per share
(minus the taker fee); its delay after publication shows how fast the market cleans up.

Usage: python3 -I metar_snipe.py [days=14]
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


def get(u):
    for i in range(5):
        try:
            with urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=60) as r:
                b = r.read()
                return json.loads(gzip.decompress(b) if r.headers.get("Content-Encoding") == "gzip" else b)
        except Exception:
            time.sleep(1.5 * (i + 1))
    return None


def qualifying(raw):
    if raw.startswith("SPECI"):
        return None
    t = re.search(r"\sT([01])(\d{3})[01]\d{3}\b", raw)
    if t:
        return (-1 if t.group(1) == "1" else 1) * int(t.group(2)) / 10
    b = re.search(r"\s(M?\d{2})/(M?\d{2})?\s", raw)
    return (-int(b.group(1)[1:]) if b.group(1).startswith("M") else int(b.group(1))) if b else None


def to_f(c):
    return math.floor(c * 9 / 5 + 32 + 0.5)


def bucket(title):
    r = re.search(r"(-?\d+)\s*[-–]\s*(-?\d+)", title)
    if r:
        return int(r.group(1)), int(r.group(2))
    v = int(re.search(r"-?\d+", title).group(0))
    return (-10**6, v) if "below" in title else (v, 10**6) if ("higher" in title or "above" in title) else (v, v)


def metar_history(station, start_ts, end_ts):
    """All reports for a station between two times. The API returns at most 400 rows per call, so fetch in
    6-day windows (half-hourly stations send 288 per window) ending at each window's end, and de-duplicate."""
    seen, out, t = set(), [], end_ts
    while t > start_ts:
        stamp = dt.datetime.fromtimestamp(t, dt.timezone.utc).strftime("%Y%m%d_%H%M")
        for o in get(f"https://aviationweather.gov/api/data/metar?ids={station}&hours=144&date={stamp}&format=json") or []:
            key = (o["obsTime"], o.get("rawOb"))
            if key not in seen:
                seen.add(key)
                out.append(o)
        t -= 144 * 3600
    return out


def trades(cond):
    rows, cur = [], None
    for _ in range(30):
        d = get(f"https://data-api.polymarket.com/v2/trades?conditionId={cond}&limit=500" + (f"&cursor={cur}" if cur else ""))
        if not d or "data" not in d:
            break
        rows += d["data"]
        if not d["pagination"].get("has_more"):
            break
        cur = d["pagination"]["next_cursor"]
    return rows


def main():
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 14
    today = dt.datetime.now(dt.timezone.utc).date()
    want = [(kind, city, today - dt.timedelta(days=d)) for d in range(1, days + 1) for city in US for kind in ("highest", "lowest")]
    slug = lambda k, c, day: f"{k}-temperature-in-{c.lower().replace(' ', '-')}-on-{day.strftime('%B').lower()}-{day.day}-{day.year}"
    with cf.ThreadPoolExecutor(8) as pool:
        evs = list(pool.map(lambda w: get(f"https://gamma-api.polymarket.com/events?slug={slug(*w)}"), want))
    events = []
    for (kind, city, day), ev in zip(want, evs):
        if not ev:
            continue
        e = ev[0]
        st = re.search(r"\(([A-Z0-9]{4})\)", e["markets"][0].get("description", ""))
        ms = [m for m in e["markets"] if m.get("outcomePrices")]
        win = [m for m in ms if json.loads(m["outcomePrices"])[0] in ("1", "1.0")]
        if st and len(win) == 1:
            events.append({"kind": kind, "city": city, "day": day, "st": st.group(1), "markets": ms, "win": win[0]})
    stations = sorted({e["st"] for e in events})
    t_end = time.time() + 3600
    with cf.ThreadPoolExecutor(4) as pool:
        mets = dict(zip(stations, pool.map(lambda s: metar_history(s, t_end - (days + 3) * 86400, t_end), stations)))
    kills, match, checked = [], 0, 0
    for e in events:
        tz = ZoneInfo(US[e["city"]])
        lo_t = dt.datetime(e["day"].year, e["day"].month, e["day"].day, tzinfo=tz).timestamp()
        obs = sorted((o["obsTime"], o) for o in mets[e["st"]] if lo_t <= o["obsTime"] < lo_t + 86400 and o.get("metarType") != "SPECI")
        highest = e["kind"] == "highest"
        ext, ext_f = None, None
        bks = [(bucket(m.get("groupItemTitle") or ""), m) for m in e["markets"]]
        dead = set()
        for _, o in obs:
            q = qualifying(o.get("rawOb", ""))
            if q is None:
                continue
            if ext is None or (q > ext if highest else q < ext):
                ext, ext_f = q, to_f(q)
                pub = dt.datetime.fromisoformat(o["receiptTime"].replace("Z", "+00:00")).timestamp() if o.get("receiptTime") else o["obsTime"] + 120
                for (lo, hi), m in bks:
                    if m["id"] not in dead and ((hi < ext_f) if highest else (lo > ext_f)):
                        dead.add(m["id"])
                        kills.append({"event": e, "m": m, "t": pub, "obs": o["obsTime"], "ext_f": ext_f})
        if ext_f is not None:
            checked += 1
            (lo, hi) = bucket(e["win"].get("groupItemTitle") or "")
            ok = lo <= ext_f <= hi
            match += ok
            if not ok:                      # replay disagrees with the official result: drop this event's kills
                kills = [k for k in kills if k["event"] is not e]
                print(f"   mismatch: {e['kind']} {e['city']} {e['day']} replayed {ext_f}F, official {e['win'].get('groupItemTitle')}")
            e["end"] = lo_t + 86400
    print(f"{len(events)} settled US temperature events, {len(stations)} stations; replayed winner = official winner in {match}/{checked}")
    print(f"{len(kills)} bucket kills by published reports")
    with cf.ThreadPoolExecutor(8) as pool:
        tr = list(pool.map(lambda k: trades(k["m"]["conditionId"]), kills))
    fee = lambda p: 0.05 * p * (1 - p)
    caps, bought_dead = [], []
    for k, rows in zip(kills, tr):
        for x in rows:
            if x["timestamp"] < k["t"] - 2 or x["timestamp"] > k["event"]["end"]:   # stamps lag ~2 s; same local day only
                continue
            p_yes = x["price"] if x["outcome_index"] == 0 else 1 - x["price"]
            sell_yes = (x["outcome_index"] == 0 and x["side"] == "SELL") or (x["outcome_index"] == 1 and x["side"] == "BUY")
            rec = (x["timestamp"] - k["t"], p_yes, x["size"], k)
            (caps if sell_yes else bought_dead).append(rec)
    if not caps:
        print("no trades sold into dead buckets after the kill"); return
    prof = [(p - fee(p)) * s for d, p, s, _ in caps]
    delays = sorted(d for d, _, _, _ in caps)
    print(f"\nSales into DEAD buckets after the report was published: {len(caps)} trades, {sum(s for _, _, s, _ in caps):,.0f} shares, "
          f"riskless profit captured ${sum(prof):,.2f}")
    for lim in (30, 60, 300, 1800, 10**9):
        sel = [(p - fee(p)) * s for d, p, s, _ in caps if d <= lim]
        print(f"   within {('%d s' % lim) if lim < 10**9 else 'any time'}: ${sum(sel):,.2f} in {len(sel)} trades")
    print(f"   delay after publication: median {delays[len(delays) // 2]:.0f} s, p90 {delays[int(len(delays) * .9)]:.0f} s")
    big = sorted(caps, key=lambda c: -(c[1] * c[2]))[:12]
    for d, p, s, k in big:
        e = k["event"]
        print(f"   {e['kind']:7s} {e['city']:13s} {e['day']}  bucket {k['m'].get('groupItemTitle'):>14s} dead at {dt.datetime.fromtimestamp(k['t'], ZoneInfo(US[e['city']])):%H:%M} local "
              f"(extreme {k['ext_f']}F): sold {s:7.1f} sh at {p:.3f}, {d:+.0f} s after")
    print(f"Buys of DEAD buckets after publication (losers): {len(bought_dead)} trades, ${sum(p * s for _, p, s, _ in bought_dead):,.2f} paid")


if __name__ == "__main__":
    main()
