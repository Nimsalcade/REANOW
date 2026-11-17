"""How gabagool22 builds each pair: which leg first, when, at what price, how much, and how the second leg follows.

`fetch` samples sessions from evidence/positions-closed.json (stratified by month, coin and market length) and
downloads every fill of the wallet in each from data-api v2 /activity (type TRADE, filtered by condition id)
into data/fills/ (gitignored). `analyze` reports per-session features and their distribution.

Usage: python3 -I legs.py fetch [per_stratum] | analyze
"""
import concurrent.futures as cf
import datetime as dt
import gzip
import json
import os
import random
import re
import sys
import time
import urllib.request
from collections import Counter, defaultdict
from zoneinfo import ZoneInfo

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
WALLET = "0x6031b6eed1c97e853c6e0f03ad3ce3529351f96d"
FILLS = os.path.join(HERE, "data", "fills")
UA = {"User-Agent": "Mozilla/5.0", "Accept-Encoding": "gzip"}
ET = ZoneInfo("America/New_York")
MONTHS = {m: i for i, m in enumerate(["january", "february", "march", "april", "may", "june", "july", "august",
                                      "september", "october", "november", "december"], 1)}


def get(u):
    for i in range(6):
        try:
            with urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=60) as r:
                b = r.read()
                return json.loads(gzip.decompress(b) if r.headers.get("Content-Encoding") == "gzip" else b)
        except Exception:
            time.sleep(2 * (i + 1))
    raise RuntimeError(u)


def markets():
    mk = {}
    for r in json.load(open(os.path.join(HERE, "evidence", "positions-closed.json"))):
        m = mk.setdefault(r["condition_id"], {"cond": r["condition_id"], "slug": r["slug"], "title": r["title"], "pnl": 0.0,
                                              "first": r.get("first_entry_at") or 0})
        m["pnl"] += r["realized_pnl"] or 0.0
    out = []
    for m in mk.values():
        s = m["slug"]
        hit = re.search(r"-updown-(5m|15m|4h)-(\d{9,})$", s)
        if hit:
            m["kind"], m["S"], m["D"] = hit.group(1), int(hit.group(2)), {"5m": 300, "15m": 900, "4h": 14400}[hit.group(1)]
            m["asset"] = s.split("-")[0]
        else:   # hourly: "Bitcoin Up or Down - February 20, 4AM ET"
            t = re.search(r"- (\w+) (\d+), (\d+)(AM|PM) ET", m["title"])
            if not t or "up-or-down" not in s or not m["first"]:
                continue
            year = dt.datetime.fromtimestamp(m["first"], dt.timezone.utc).year
            hour = int(t.group(3)) % 12 + (12 if t.group(4) == "PM" else 0)
            start = dt.datetime(year, MONTHS[t.group(1).lower()], int(t.group(2)), hour, tzinfo=ET)
            m["kind"], m["S"], m["D"], m["asset"] = "1h", int(start.timestamp()), 3600, {"bitcoin": "btc", "ethereum": "eth"}.get(s.split("-")[0], s.split("-")[0])
        m["month"] = dt.datetime.fromtimestamp(m["S"], dt.timezone.utc).strftime("%Y-%m")
        out.append(m)
    return out


def fetch_one(m):
    path = os.path.join(FILLS, m["cond"] + ".json")
    if os.path.exists(path):
        return
    rows, cur = [], None
    while True:
        d = get(f"https://data-api.polymarket.com/v2/activity?user={WALLET}&type=TRADE&limit=500&condition_id={m['cond']}"
                + (f"&cursor={cur}" if cur else ""))
        rows += [{k: x[k] for k in ("timestamp", "side", "outcome_index", "price", "size", "transaction_hash")} for x in d["data"]]
        if not d["pagination"].get("has_more"):
            break
        cur = d["pagination"]["next_cursor"]
    json.dump({"market": m, "fills": rows}, open(path, "w"))


def fetch(per):
    os.makedirs(FILLS, exist_ok=True)
    rng = random.Random(7)
    strata = defaultdict(list)
    for m in markets():
        if m["asset"] in ("btc", "eth") and m["kind"] in ("5m", "15m", "1h"):
            strata[(m["month"], m["asset"], m["kind"])].append(m)
    pick = []
    for k, v in sorted(strata.items()):
        pick += rng.sample(v, min(per, len(v)))
    print(f"fetching {len(pick)} sessions from {len(strata)} strata")
    with cf.ThreadPoolExecutor(4) as pool:
        for i, _ in enumerate(pool.map(fetch_one, pick)):
            if i % 50 == 0:
                print(f"  {i}/{len(pick)}", flush=True)


def features(m, fills):
    S, D = m["S"], m["D"]
    fills = sorted(fills, key=lambda f: (f["timestamp"], f["transaction_hash"]))
    if fills and m["kind"] == "1h" and abs(fills[0]["timestamp"] - S) > 180 * 86400:   # hourly titles carry no year
        start = dt.datetime.fromtimestamp(S, ET)
        S = int(start.replace(year=start.year + round((fills[0]["timestamp"] - S) / 31_557_600)).timestamp())
        m = dict(m, S=S, month=dt.datetime.fromtimestamp(S, dt.timezone.utc).strftime("%Y-%m"))
    buys = [f for f in fills if f["side"] == "BUY"]
    if not buys:
        return None
    q, c = [0.0, 0.0], [0.0, 0.0]
    first = buys[0]
    s1 = first["outcome_index"]
    other = next((f for f in buys if f["outcome_index"] != s1), None)
    max_imb, switches, prev, capped, capn = 0.0, 0, None, 0, 0
    for f in buys:
        o = f["outcome_index"]
        if q[1 - o] > 0:
            capn += 1
            capped += f["price"] + c[1 - o] / q[1 - o] < 1.0
        q[o] += f["size"]; c[o] += f["size"] * f["price"]
        max_imb = max(max_imb, abs(q[0] - q[1]))
        if prev is not None and o != prev:
            switches += 1
        prev = o
    # first leg block: shares bought on the first side before the other side's first fill
    t_other = other["timestamp"] if other else None
    block1 = sum(f["size"] for f in buys if f["outcome_index"] == s1 and (t_other is None or f["timestamp"] < t_other))
    tot = q[0] + q[1]
    pc = (c[0] / q[0] + c[1] / q[1]) if q[0] > 0 and q[1] > 0 else None
    return {"kind": m["kind"], "asset": m["asset"], "month": m["month"], "pnl": m["pnl"], "n": len(buys), "sells": len(fills) - len(buys),
            "t_first": first["timestamp"] - S, "first_side": "Up" if s1 == 0 else "Down", "p_first": first["price"], "sz_first": first["size"],
            "block1": block1, "t_other": (t_other - S) if other else None, "gap": (t_other - first["timestamp"]) if other else None,
            "p_other": other["price"] if other else None, "first_pair": (first["price"] + other["price"]) if other else None,
            "t_last": buys[-1]["timestamp"] - S, "D": D, "switches": switches, "max_imb_frac": max_imb / tot if tot else 0,
            "balance": min(q) / max(q) if max(q) else 0, "pair_cost": pc, "cap_rule": capped / capn if capn else None,
            "shares": tot, "median_fill": float(np.median([f["size"] for f in buys])),
            "p_all": [f["price"] for f in buys], "rel_t": [(f["timestamp"] - S) / D for f in buys]}


def analyze():
    rows = []
    for fn in os.listdir(FILLS):
        d = json.load(open(os.path.join(FILLS, fn)))
        r = features(d["market"], d["fills"])
        if r:
            rows.append(r)
    pct = lambda v, p: float(np.percentile(v, p))
    print(f"{len(rows)} sessions with fills")
    for kind in ("15m", "1h", "5m"):
        R = [r for r in rows if r["kind"] == kind]
        if not R:
            continue
        D = R[0]["D"]
        tf = [r["t_first"] for r in R]
        print(f"\n== {kind} sessions ({len(R)}; session length {D} s)")
        print(f"   first fill: {pct(tf, 10):+.0f} / {pct(tf, 50):+.0f} / {pct(tf, 90):+.0f} s after the open (p10/median/p90); "
              f"before the open: {np.mean([t < 0 for t in tf]):.0%}")
        pf = [r["p_first"] for r in R]
        print(f"   first-leg price p10/median/p90: {pct(pf, 10):.2f} / {pct(pf, 50):.2f} / {pct(pf, 90):.2f}; first side Up {np.mean([r['first_side'] == 'Up' for r in R]):.0%}; "
              f"first fill size median {np.median([r['sz_first'] for r in R]):.1f} sh")
        g = [r["gap"] for r in R if r["gap"] is not None]
        print(f"   other leg's first fill: {pct(g, 10):.0f} / {pct(g, 50):.0f} / {pct(g, 90):.0f} s after the first fill; shares bought on leg 1 before it: "
              f"median {np.median([r['block1'] for r in R]):.0f}")
        fp = [r["first_pair"] for r in R if r["first_pair"] is not None]
        print(f"   first fill of each leg combined: p10/median/p90 {pct(fp, 10):.3f} / {pct(fp, 50):.3f} / {pct(fp, 90):.3f}; under $1: {np.mean([x < 1 for x in fp]):.0%}")
        print(f"   fills per session median {np.median([r['n'] for r in R]):.0f}, side switches median {np.median([r['switches'] for r in R]):.0f}, "
              f"median fill {np.median([r['median_fill'] for r in R]):.1f} sh, shares per session median {np.median([r['shares'] for r in R]):.0f}")
        print(f"   last fill: median {np.median([D - r['t_last'] for r in R]):.0f} s before the close; sells: {sum(r['sells'] for r in R)}")
        cr = [r["cap_rule"] for r in R if r["cap_rule"] is not None]
        print(f"   fills where price + avg cost of the other side < $1: median {np.median(cr):.0%} of fills (p10 {pct(cr, 10):.0%})")
        pc = [r["pair_cost"] for r in R if r["pair_cost"]]
        print(f"   final pair cost median {np.median(pc):.4f}; balance median {np.median([r['balance'] for r in R]):.3f}; "
              f"max imbalance during the session median {np.median([r['max_imb_frac'] for r in R]):.0%} of shares")
        allp = np.concatenate([r["p_all"] for r in R]); allt = np.concatenate([r["rel_t"] for r in R])
        print("   price paid by part of the session: " + ", ".join(
            f"{int(100 * a)}-{int(100 * b)}%: median {np.median(allp[(allt >= a) & (allt < b)]):.2f} (n {int(((allt >= a) & (allt < b)).sum())})"
            for a, b in ((-1, 0), (0, .2), (.2, .4), (.4, .6), (.6, .8), (.8, 1.01)) if ((allt >= a) & (allt < b)).sum()))
        by_m = defaultdict(list)
        for r in R:
            by_m[r["month"]].append(r)
        for mo, rr in sorted(by_m.items()):
            fp_m = [r["first_pair"] for r in rr if r["first_pair"] is not None]
            print(f"     {mo}: n {len(rr):3d}  first fill +{np.median([r['t_first'] for r in rr]):.0f}s at {np.median([r['p_first'] for r in rr]):.2f}, "
                  f"other leg +{np.median([r['gap'] for r in rr if r['gap'] is not None]):.0f}s later, first pair {np.median(fp_m):.3f}, "
                  f"shares {np.median([r['shares'] for r in rr]):.0f}, pair cost {np.median([r['pair_cost'] for r in rr if r['pair_cost']]):.4f}, "
                  f"P&L median ${np.median([r['pnl'] for r in rr]):.2f}")


if __name__ == "__main__":
    if sys.argv[1] == "fetch":
        fetch(int(sys.argv[2]) if len(sys.argv) > 2 else 20)
    else:
        analyze()
