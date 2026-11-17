"""Weather markets settled by airport METAR reports: which buckets are already decided, and are they priced so?

Rules (from each market's description): the qualifying value is the highest (or lowest) temperature in the
hourly T-group remark (tenths of a degree C) of any routine METAR for the named station whose time stamp, in the
station's local time, falls on the observation date; without a T-group the body temperature (whole degrees C)
counts; SPECI reports do not count. Buckets are whole degrees C, or whole degrees F after F = C * 9/5 + 32;
both round to the nearest degree with halves rounded up. Source: aviationweather.gov, as used for resolution.

Decided now: for "Highest", every bucket below the one holding today's observed maximum can no longer win; for
"Lowest", every bucket above today's observed minimum. Selling Yes on those buckets (buying No at 1 - bid) is
riskless; the bucket holding the current extreme is the favourite, with the hours left as the risk.

Usage: python3 -I weather.py [YYYY-MM-DD observation date, default today UTC]
"""
import datetime as dt
import gzip
import json
import math
import re
import sys
import time
import urllib.request
from zoneinfo import ZoneInfo

UA = {"User-Agent": "Mozilla/5.0", "Accept-Encoding": "gzip"}
TZ = {"London": "Europe/London", "Paris": "Europe/Paris", "Seoul": "Asia/Seoul", "Ankara": "Europe/Istanbul", "Lucknow": "Asia/Kolkata",
      "Munich": "Europe/Berlin", "Tel Aviv": "Asia/Jerusalem", "Tokyo": "Asia/Tokyo", "Hong Kong": "Asia/Hong_Kong", "Shanghai": "Asia/Shanghai",
      "Singapore": "Asia/Singapore", "Milan": "Europe/Rome", "Madrid": "Europe/Madrid", "Warsaw": "Europe/Warsaw", "Taipei": "Asia/Taipei",
      "Chongqing": "Asia/Shanghai", "Beijing": "Asia/Shanghai", "Wuhan": "Asia/Shanghai", "Chengdu": "Asia/Shanghai", "Shenzhen": "Asia/Shanghai",
      "Moscow": "Europe/Moscow", "Istanbul": "Europe/Istanbul", "Busan": "Asia/Seoul", "Amsterdam": "Europe/Amsterdam", "Helsinki": "Europe/Helsinki",
      "Kuala Lumpur": "Asia/Kuala_Lumpur", "Jeddah": "Asia/Riyadh", "Cape Town": "Africa/Johannesburg", "Guangzhou": "Asia/Shanghai",
      "Qingdao": "Asia/Shanghai", "Karachi": "Asia/Karachi", "Manila": "Asia/Manila", "NYC": "America/New_York", "Miami": "America/New_York",
      "Austin": "America/Chicago", "Houston": "America/Chicago", "Dallas": "America/Chicago", "Atlanta": "America/New_York",
      "Chicago": "America/Chicago", "Seattle": "America/Los_Angeles", "Denver": "America/Denver", "Los Angeles": "America/Los_Angeles",
      "San Francisco": "America/Los_Angeles", "Sao Paulo": "America/Sao_Paulo", "Buenos Aires": "America/Argentina/Buenos_Aires",
      "Toronto": "America/Toronto", "Mexico City": "America/Mexico_City", "Panama City": "America/Panama"}


def get(u):
    for i in range(4):
        try:
            with urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=60) as r:
                b = r.read()
                return json.loads(gzip.decompress(b) if r.headers.get("Content-Encoding") == "gzip" else b)
        except Exception:
            time.sleep(1.5 * (i + 1))
    return None


def half_up(x):
    return math.floor(x + 0.5)


def qualifying(raw):
    """Temperature (C) a routine report contributes, or None (SPECI, or no temperature)."""
    if raw.startswith("SPECI"):
        return None
    t = re.search(r"\sT([01])(\d{3})[01]\d{3}\b", raw)
    if t:
        return (-1 if t.group(1) == "1" else 1) * int(t.group(2)) / 10
    b = re.search(r"\s(M?\d{2})/(M?\d{2})?\s", raw)
    return (-int(b.group(1)[1:]) if b.group(1).startswith("M") else int(b.group(1))) if b else None


def bucket(title):
    """(lo, hi) in whole degrees from a groupItemTitle like '58-59°F', '8°C or below', '22°C or higher', '14°C'."""
    r = re.search(r"(-?\d+)\s*[-–]\s*(-?\d+)", title)          # a range such as 58-59°F or -3--2°C
    if r:
        return int(r.group(1)), int(r.group(2))
    v = int(re.search(r"-?\d+", title).group(0))
    if "below" in title:
        return -10**6, v
    if "higher" in title or "above" in title:
        return v, 10**6
    return v, v


def book(token):
    b = get(f"https://clob.polymarket.com/book?token_id={token}") or {}
    bids = sorted(((float(x["price"]), float(x["size"])) for x in b.get("bids", [])), reverse=True)
    asks = sorted((float(x["price"]), float(x["size"])) for x in b.get("asks", []))
    return (bids[0] if bids else (0.0, 0.0)), (asks[0] if asks else (1.0, 0.0))


def main():
    day = dt.date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else dt.datetime.now(dt.timezone.utc).date()
    tag = f"on {day.strftime('%B')} {day.day}?"
    evs = []
    for off in range(0, 600, 100):
        page = get(f"https://gamma-api.polymarket.com/events?active=true&closed=false&limit=100&offset={off}&tag_slug=weather")
        if not page:
            break
        evs += [e for e in page if e["title"].endswith(tag) and re.match(r"(Highest|Lowest) temperature in ", e["title"])]
    stations = {}
    for e in evs:
        city = re.match(r"(?:Highest|Lowest) temperature in (.+?) on ", e["title"]).group(1).split(" (")[0]
        st = re.search(r"\(([A-Z0-9]{4})\)", e["markets"][0].get("description", ""))
        if st and city in TZ:
            stations[e["id"]] = (st.group(1), ZoneInfo(TZ[city]), city)
    ids = sorted({s for s, _, _ in stations.values()})
    obs = []
    for i in range(0, len(ids), 20):
        obs += get(f"https://aviationweather.gov/api/data/metar?ids={','.join(ids[i:i + 20])}&hours=40&format=json") or []
    by_st = {}
    for o in obs:
        by_st.setdefault(o["icaoId"], []).append(o)
    now = dt.datetime.now(dt.timezone.utc)
    riskless, favourites = [], []
    for e in evs:
        if e["id"] not in stations:
            continue
        st, tz, city = stations[e["id"]]
        lo_t = dt.datetime(day.year, day.month, day.day, tzinfo=tz)
        hi_t = lo_t + dt.timedelta(days=1)
        vals = []
        for o in by_st.get(st, []):
            t = dt.datetime.fromtimestamp(o["obsTime"], dt.timezone.utc)
            q = None if o.get("metarType") == "SPECI" else qualifying(o.get("rawOb", ""))
            if lo_t <= t < hi_t and q is not None:
                vals.append(q)
        if not vals:
            continue
        highest = e["title"].startswith("Highest")
        x = max(vals) if highest else min(vals)
        fahr = "°F" in (e["markets"][0].get("groupItemTitle") or "")
        v = half_up(x * 9 / 5 + 32) if fahr else half_up(x)
        hours_left = max(0.0, (hi_t - now).total_seconds() / 3600)
        for m in e["markets"]:
            if not m.get("acceptingOrders"):
                continue
            lo, hi = bucket(m.get("groupItemTitle") or "")
            dead = hi < v if highest else lo > v
            holds = lo <= v <= hi
            if not (dead or holds):
                continue
            (bb, bbs), (ba, bas) = book(json.loads(m["clobTokenIds"])[0])
            rec = (e["title"][:50], m.get("groupItemTitle"), st, round(x, 1), v, round(hours_left, 1), bb, bbs, ba, bas, len(vals))
            if dead and bb > 0:
                riskless.append(rec)
            if holds:
                favourites.append(rec)
    print(f"{len(evs)} temperature events for {day}; {len(by_st)} stations with reports")
    fee = lambda p: 0.05 * p * (1 - p)
    tot = sum((r[6] - fee(1 - r[6])) * r[7] for r in riskless)
    print(f"\nDECIDED buckets still bid (sell Yes = buy No at 1 - bid; riskless): {len(riskless)}, ${tot:.2f} available at the best bids")
    for r in sorted(riskless, key=lambda r: -r[6] * r[7])[:20]:
        print(f"  {r[0]:50s} bucket {r[1]:>14s} dead (observed {r[3]}°C -> {r[4]}), bid {r[6]:.3f} x {r[7]:.0f}  ({r[5]} h left, {r[10]} reports)")
    print("\nBucket holding today's observed extreme (the favourite):")
    for r in sorted(favourites, key=lambda r: r[5]):
        print(f"  {r[0]:50s} {r[1]:>14s}  observed {r[3]:>5}°C -> {r[4]:>3}  {r[5]:4.1f} h left  bid {r[6]:.2f} x {r[7]:<6.0f} ask {r[8]:.2f} x {r[9]:.0f}")


if __name__ == "__main__":
    main()
