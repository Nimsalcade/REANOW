"""Which weather trades made money, given what the published METARs already showed at the time of the trade?

For every settled "Highest/Lowest temperature" event (all cities) of the last N days: replay the station's routine
METARs with the settlement rules (metar_snipe.py) and keep only events whose replay equals the official winner. For
every taker trade (data-api v2) that bought a bucket's Yes, classify the bucket against the extreme published
before the trade (receiptTime <= trade time): the bucket holding it ("current"), one that can still win beyond it
("beyond"), or a dead one. Realised P&L per share = payout - price - taker fee, by class and hours left in the
local day. A class with a reliably positive mean is an edge a taker can copy.

Usage: python3 -I weather_trades.py [days=5]
"""
import concurrent.futures as cf
import datetime as dt
import json
import math
import os
import re
import sys
from collections import defaultdict
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import metar_snipe as MS
import weather as W


def main():
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 5
    today = dt.datetime.now(dt.timezone.utc).date()
    cities = sorted(W.TZ)
    def slug(kind, city, day):
        c = {"Seoul": "seoul-incheon"}.get(city, city.lower().replace(" ", "-"))
        return f"{kind}-temperature-in-{c}-on-{day.strftime('%B').lower()}-{day.day}-{day.year}"
    want = [(k, c, today - dt.timedelta(days=d)) for d in range(1, days + 1) for c in cities for k in ("highest", "lowest")]
    with cf.ThreadPoolExecutor(8) as pool:
        evs = list(pool.map(lambda w: MS.get(f"https://gamma-api.polymarket.com/events?slug={slug(*w)}"), want))
    events = []
    for (kind, city, day), ev in zip(want, evs):
        if not ev:
            continue
        e = ev[0]
        st = re.search(r"\(([A-Z0-9]{4})\)", e["markets"][0].get("description", ""))
        ms = [m for m in e["markets"] if m.get("outcomePrices")]
        win = [m for m in ms if json.loads(m["outcomePrices"])[0] in ("1", "1.0")]
        if st and len(win) == 1:
            fahr = "°F" in (ms[0].get("groupItemTitle") or "")
            events.append({"kind": kind, "city": city, "day": day, "st": st.group(1), "markets": ms, "win": win[0], "fahr": fahr})
    stations = sorted({e["st"] for e in events})
    t_end = dt.datetime.now(dt.timezone.utc).timestamp() + 3600
    with cf.ThreadPoolExecutor(4) as pool:
        mets = dict(zip(stations, pool.map(lambda s: MS.metar_history(s, t_end - (days + 3) * 86400, t_end), stations)))
    good = []
    for e in events:
        tz = ZoneInfo(W.TZ[e["city"]])
        lo_t = dt.datetime(e["day"].year, e["day"].month, e["day"].day, tzinfo=tz).timestamp()
        steps, ext = [], None
        for o in sorted(mets[e["st"]], key=lambda o: o["obsTime"]):
            if not (lo_t <= o["obsTime"] < lo_t + 86400) or o.get("metarType") == "SPECI":
                continue
            q = MS.qualifying(o.get("rawOb", ""))
            if q is None:
                continue
            if ext is None or (q > ext if e["kind"] == "highest" else q < ext):
                ext = q
                pub = dt.datetime.fromisoformat(o["receiptTime"].replace("Z", "+00:00")).timestamp() if o.get("receiptTime") else o["obsTime"] + 120
                steps.append((pub, MS.to_f(q) if e["fahr"] else math.floor(q + 0.5)))
        if not steps:
            continue
        lo, hi = MS.bucket(e["win"].get("groupItemTitle") or "")
        if lo <= steps[-1][1] <= hi:
            e.update(steps=steps, start=lo_t, end=lo_t + 86400)
            good.append(e)
    print(f"{len(events)} settled temperature events ({days} days, {len(stations)} stations); replay = official winner in {len(good)}")
    mk = [(e, m) for e in good for m in e["markets"]]
    with cf.ThreadPoolExecutor(8) as pool:
        tr = list(pool.map(lambda em: MS.trades(em[1]["conditionId"]), mk))
    fee = lambda p: 0.05 * p * (1 - p)
    cells = defaultdict(list)
    for (e, m), rows in zip(mk, tr):
        lo, hi = MS.bucket(m.get("groupItemTitle") or "")
        won = m is e["win"]
        for x in rows:
            t = x["timestamp"] - 2
            if not (e["start"] <= t <= e["end"]):
                continue
            buy_yes = (x["outcome_index"] == 0 and x["side"] == "BUY") or (x["outcome_index"] == 1 and x["side"] == "SELL")
            if not buy_yes:
                continue
            p = x["price"] if x["outcome_index"] == 0 else 1 - x["price"]
            known = [v for pub, v in e["steps"] if pub <= t]
            if not known:
                continue
            v = known[-1]
            cls = "current" if lo <= v <= hi else ("dead" if ((hi < v) if e["kind"] == "highest" else (lo > v)) else "beyond")
            left = (e["end"] - t) / 3600
            hb = "<3h" if left < 3 else "3-6h" if left < 6 else "6-10h" if left < 10 else ">10h"
            cells[(e["kind"], cls, hb)].append(((1.0 if won else 0.0) - p - fee(p), p, x["size"], won))
    print("taker buys of a bucket's Yes, by what the published reports showed at that moment")
    print("  kind     class    hours left   trades   shares    avg price  win rate   P&L c/share (se)   P&L $")
    for key in sorted(cells):
        v = cells[key]
        sh = sum(s for _, _, s, _ in v)
        pnl = sum(r * s for r, _, s, _ in v)
        mean = pnl / sh
        sd = math.sqrt(sum(s * (r - mean) ** 2 for r, _, s, _ in v) / sh) if sh else 0
        se = sd / math.sqrt(len(v)) if len(v) > 1 else float("nan")
        print(f"  {key[0]:8s} {key[1]:8s} {key[2]:>6s}     {len(v):6d}  {sh:9.0f}     {sum(p * s for _, p, s, _ in v) / sh:.3f}    "
              f"{sum(s for _, _, s, w in v if w) / sh:6.1%}    {100 * mean:+6.1f} ({100 * se:4.1f})    {pnl:+9.0f}")


if __name__ == "__main__":
    main()
