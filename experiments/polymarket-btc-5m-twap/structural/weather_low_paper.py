"""Paper bot for the dawn-low edge in Polymarket "Lowest temperature in <city>" markets. No orders are sent.

Edge (bot/JOURNAL.md #28): between 04:00 and 08:00 station-local time, the bucket holding the overnight low
observed so far (settlement rules of metar_snipe.py: routine METAR T-group or body temperature, rounded half up,
C or F) won more often than takers paid for it: +5.4 c/share after fees over 378 city-days (t 3.35), and
+3.2 c on days not used to find it.

Every 5 minutes: for each city whose local time is 04:00-08:00, find today's lowest-temperature event, replay the
published reports, and paper-buy the low's bucket once per city-day at the CLOB best ask (up to SIZE shares at that
level, ask <= MAX_ASK). Entries settle from Gamma's official outcome after the event closes.

Run:  python3 weather_low_paper.py [--size 50] [--max-ask 0.97] [--out weather-paper]
Output: entries.jsonl, settled.jsonl, ledger.md in the output folder. Ctrl-C to stop.
"""
import datetime as dt
import json
import math
import os
import re
import sys
import time
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import metar_snipe as MS
import weather as W


def arg(k, d):
    return sys.argv[sys.argv.index(k) + 1] if k in sys.argv else d


SIZE, MAX_ASK = float(arg("--size", "50")), float(arg("--max-ask", "0.97"))
OUT = os.path.abspath(arg("--out", "weather-paper"))
os.makedirs(OUT, exist_ok=True)
log = lambda *a: print(dt.datetime.now(dt.timezone.utc).strftime("%H:%M:%S"), *a, flush=True)


def read(name):
    try:
        return [json.loads(l) for l in open(os.path.join(OUT, name)) if l.strip()]
    except FileNotFoundError:
        return []


def append(name, rec):
    with open(os.path.join(OUT, name), "a") as fh:
        fh.write(json.dumps(rec) + "\n")


def slug(city, day):
    return f"lowest-temperature-in-{city.lower().replace(' ', '-')}-on-{day.strftime('%B').lower()}-{day.day}-{day.year}"


def observed_low(station, tz, day, fahr):
    lo_t = dt.datetime(day.year, day.month, day.day, tzinfo=tz).timestamp()
    obs = MS.get(f"https://aviationweather.gov/api/data/metar?ids={station}&hours=30&format=json") or []
    vals = [MS.qualifying(o.get("rawOb", "")) for o in obs
            if lo_t <= o["obsTime"] < lo_t + 86400 and o.get("metarType") != "SPECI"]
    vals = [v for v in vals if v is not None]
    if not vals:
        return None, None, 0
    x = min(vals)
    return x, (MS.to_f(x) if fahr else math.floor(x + 0.5)), len(vals)


def enter():
    done = {(r["city"], r["day"]) for r in read("entries.jsonl")}
    now = dt.datetime.now(dt.timezone.utc)
    for city, tzname in sorted(W.TZ.items()):
        tz = ZoneInfo(tzname)
        local = now.astimezone(tz)
        day = local.date()
        if not (4 <= local.hour < 8) or (city, day.isoformat()) in done:
            continue
        ev = MS.get(f"https://gamma-api.polymarket.com/events?slug={slug(city, day)}")
        if not ev:
            continue
        e = ev[0]
        st = re.search(r"\(([A-Z0-9]{4})\)", e["markets"][0].get("description", ""))
        ms = [m for m in e["markets"] if m.get("acceptingOrders") and m.get("enableOrderBook")]
        if not st or not ms:
            continue
        fahr = "°F" in (ms[0].get("groupItemTitle") or "")
        x, v, n = observed_low(st.group(1), tz, day, fahr)
        if v is None:
            continue
        m = next((m for m in ms if MS.bucket(m.get("groupItemTitle") or "")[0] <= v <= MS.bucket(m.get("groupItemTitle") or "")[1]), None)
        if not m:
            continue
        (bb, bbs), (ba, bas) = W.book(json.loads(m["clobTokenIds"])[0])
        rec = {"city": city, "day": day.isoformat(), "local": local.strftime("%H:%M"), "station": st.group(1), "low": x, "bucket_value": v,
               "bucket": m.get("groupItemTitle"), "slug": e["slug"], "market_id": m["id"], "bid": bb, "ask": ba, "ask_size": bas, "reports": n,
               "t": now.isoformat()}
        if ba > MAX_ASK or bas <= 0:
            rec.update(skip=True, shares=0)
            log(f"{city:13s} {local:%H:%M} low {x} -> {m.get('groupItemTitle')}: ask {ba:.3f} x {bas:.0f} — skip")
        else:
            fs = m.get("feeSchedule") or {}
            fee = (fs.get("rate") or 0.05) * ba * (1 - ba)
            rec.update(shares=min(SIZE, bas), cost=min(SIZE, bas) * (ba + fee))
            log(f"{city:13s} {local:%H:%M} low {x} -> {m.get('groupItemTitle')}: PAPER BUY {rec['shares']:.0f} @ {ba:.3f} (bid {bb:.3f})")
        append("entries.jsonl", rec)


def settle():
    settled = {(r["city"], r["day"]) for r in read("settled.jsonl")}
    for r in read("entries.jsonl"):
        if r.get("skip") or (r["city"], r["day"]) in settled:
            continue
        ev = MS.get(f"https://gamma-api.polymarket.com/events?slug={r['slug']}")
        if not ev:
            continue
        m = next((m for m in ev[0]["markets"] if m["id"] == r["market_id"]), None)
        if not m or not m.get("outcomePrices") or not ev[0].get("closed"):
            continue
        won = json.loads(m["outcomePrices"])[0] in ("1", "1.0")
        pnl = r["shares"] * (1.0 if won else 0.0) - r["cost"]
        append("settled.jsonl", {**r, "won": won, "pnl": pnl})
        log(f"SETTLED {r['city']} {r['day']} {r['bucket']}: {'WON' if won else 'lost'} {pnl:+.2f}")


def ledger():
    S = read("settled.jsonl")
    E = [r for r in read("entries.jsonl") if not r.get("skip")]
    sh, cost, pnl = sum(r["shares"] for r in S), sum(r["cost"] for r in S), sum(r["pnl"] for r in S)
    lines = [f"# Dawn-low weather paper ledger — {dt.datetime.now(dt.timezone.utc):%Y-%m-%d %H:%M}Z", "",
             f"size {SIZE:.0f} shares, max ask {MAX_ASK}", "",
             "| entries | settled | won | shares | cost $ | P&L $ | P&L per share c | avg ask |", "|---|---|---|---|---|---|---|---|",
             f"| {len(E)} | {len(S)} | {sum(1 for r in S if r['won'])} | {sh:.0f} | {cost:.2f} | {pnl:+.2f} | "
             f"{(100 * pnl / sh) if sh else 0:+.2f} | {(sum(r['ask'] * r['shares'] for r in S) / sh) if sh else 0:.3f} |"]
    with open(os.path.join(OUT, "ledger.md"), "w") as fh:
        fh.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    log(f"dawn-low paper bot: size {SIZE:.0f}, max ask {MAX_ASK}, output {OUT}")
    while True:
        try:
            enter()
            settle()
            ledger()
        except Exception as exc:     # keep running through transient API errors
            log("error", exc)
        time.sleep(300)
