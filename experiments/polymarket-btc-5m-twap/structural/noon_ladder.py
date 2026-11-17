"""Cross-event strike ladder at the daily noon-ET Binance close.

Every day these markets settle on the same number C = Binance <ASSET>/USDT close at 12:00 ET (the last trade
of the 1-minute candle closing at 12:00, which is also the close of the 1-hour candle beginning at 11 AM ET):
  * "<Asset> above X on <date>"          Yes iff C > X               (one event, a ladder of strikes)
  * "<Asset> price on <date>"             Yes iff C in the bucket      (one negRisk event, buckets)
  * "<Asset> Up or Down - <date>, 11AM ET" Up iff C >= O (Binance 1 h open at 11:00 ET)
Relations that must hold, and the two-leg pairs that pay at least $1 when they are violated:
  A. above X_lo vs above X_hi (X_lo < X_hi): Yes(X_lo) at least as likely  -> Yes(X_lo) + No(X_hi)
  B. above X vs the buckets above X (exact replication)  -> Yes(above X) + No on each bucket above X, or the reverse
  C. hourly Up (strike O) vs above X: X >= O -> Up + No(above X);  X < O -> Yes(above X) + Down
Costs use CLOB best asks (No ask = 1 - Yes bid) and taker fees rate * p(1-p). Sizes are the smaller best-level size.

Usage: python3 -I noon_ladder.py [date=YYYY-MM-DD, default today ET] [assets=bitcoin,ethereum,solana,xrp]
"""
import concurrent.futures as cf
import datetime as dt
import gzip
import json
import re
import sys
import time
import urllib.request
from zoneinfo import ZoneInfo

UA = {"User-Agent": "Mozilla/5.0", "Accept-Encoding": "gzip"}
ET = ZoneInfo("America/New_York")
SYMBOL = {"bitcoin": "BTCUSDT", "ethereum": "ETHUSDT", "solana": "SOLUSDT", "xrp": "XRPUSDT"}


def get(u):
    for i in range(4):
        try:
            with urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=60) as r:
                b = r.read()
                return json.loads(gzip.decompress(b) if r.headers.get("Content-Encoding") == "gzip" else b)
        except Exception:
            time.sleep(1.5 * (i + 1))
    return None


def event(slug):
    ev = get(f"https://gamma-api.polymarket.com/events?slug={slug}")
    return ev[0] if ev else None


def num(s):
    m = re.search(r"([\d,]*\.?\d+)\s*([kK])?", s.replace("$", ""))
    return float(m.group(1).replace(",", "")) * (1000 if m.group(2) else 1) if m else None


def book(token):
    b = get(f"https://clob.polymarket.com/book?token_id={token}")
    if not b:
        return None
    bids = sorted(((float(x["price"]), float(x["size"])) for x in b.get("bids", [])), reverse=True)
    asks = sorted((float(x["price"]), float(x["size"])) for x in b.get("asks", []))
    return {"bid": bids[0] if bids else (0.0, 0.0), "ask": asks[0] if asks else (1.0, 0.0)}


def fee(m, p):
    fs = m.get("feeSchedule") or {}
    return (fs.get("rate") or 0.0) * p * (1 - p) if m.get("feesEnabled") else 0.0


def legs_cost(*legs):
    """legs: (market, book, 'yes'|'no'); buying Yes at the Yes ask, No at 1 - Yes bid."""
    cost, size = 0.0, float("inf")
    for m, b, side in legs:
        p, s = (b["ask"] if side == "yes" else (1 - b["bid"][0], b["bid"][1]))
        if side == "yes":
            p, s = b["ask"]
        cost += p + fee(m, p)
        size = min(size, s)
    return cost, size


def main():
    day = dt.date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 and "-" in sys.argv[1] else dt.datetime.now(ET).date()
    assets = (sys.argv[2] if len(sys.argv) > 2 else "bitcoin,ethereum,solana,xrp").split(",")
    md = f"{day.strftime('%B').lower()}-{day.day}-{day.year}"
    found = []
    for a in assets:
        above, price, hourly = event(f"{a}-above-on-{md}"), event(f"{a}-price-on-{md}"), event(f"{a}-up-or-down-{md}-11am-et")
        if not above:
            print(f"{a}: no 'above' ladder for {day}")
            continue
        ladder = []
        for m in above["markets"]:
            if m.get("acceptingOrders") and m.get("enableOrderBook"):
                x = num(m.get("groupItemTitle") or m.get("question", ""))
                if x:
                    ladder.append((x, m))
        buckets = []
        for m in (price or {}).get("markets", []):
            if not (m.get("acceptingOrders") and m.get("enableOrderBook")):
                continue
            t = (m.get("groupItemTitle") or "").replace("$", "").replace(",", "")
            lo_hi = re.findall(r"[\d.]+", t)
            if "<" in t or "below" in t.lower() or "under" in t.lower():
                buckets.append((float("-inf"), float(lo_hi[0]), m))
            elif ">" in t or "above" in t.lower() or "over" in t.lower():
                buckets.append((float(lo_hi[0]), float("inf"), m))
            elif len(lo_hi) == 2:
                buckets.append((float(lo_hi[0]), float(lo_hi[1]), m))
        O = None
        if hourly and hourly.get("markets") and hourly["markets"][0].get("acceptingOrders"):
            start = dt.datetime(day.year, day.month, day.day, 11, tzinfo=ET)
            k = get(f"https://data-api.binance.vision/api/v3/klines?symbol={SYMBOL[a]}&interval=1h&startTime={int(start.timestamp() * 1000)}&limit=1")
            if k and k[0][0] == int(start.timestamp() * 1000) and time.time() >= start.timestamp():
                O = float(k[0][1])
        toks = {id(m): json.loads(m["clobTokenIds"])[0] for _, m in ladder}
        toks.update({id(m): json.loads(m["clobTokenIds"])[0] for _, _, m in buckets})
        hm = hourly["markets"][0] if O is not None else None
        if hm:
            toks[id(hm)] = json.loads(hm["clobTokenIds"])[0]
        objs = {id(m): m for _, m in ladder} | {id(m): m for _, _, m in buckets} | ({id(hm): hm} if hm else {})
        with cf.ThreadPoolExecutor(8) as pool:
            books = dict(zip(toks, pool.map(book, toks.values())))
        B = lambda m: books.get(id(m))
        ladder.sort(key=lambda x: x[0])
        print(f"{a}: {len(ladder)} strikes {ladder[0][0]:,.0f}…{ladder[-1][0]:,.0f}, {len(buckets)} buckets, hourly 11AM-ET open "
              f"{'%.2f' % O if O else 'not started'}")
        # A. ladder monotonicity
        for i in range(len(ladder)):
            for j in range(i + 1, len(ladder)):
                (xl, ml), (xh, mh) = ladder[i], ladder[j]
                if B(ml) and B(mh):
                    c, s = legs_cost((ml, B(ml), "yes"), (mh, B(mh), "no"))
                    found.append(("A ladder", a, f"Yes >{xl:,.0f} + No >{xh:,.0f}", c, s))
        # B. above X vs buckets strictly above X (buckets must tile (X, inf))
        for x, m in ladder:
            up = sorted((b for b in buckets if b[0] >= x - 1e-9), key=lambda b: b[0])
            tiles = up and abs(up[0][0] - x) < 1e-6 and all(abs(up[k][1] - up[k + 1][0]) < 1e-6 for k in range(len(up) - 1)) and up[-1][1] == float("inf")
            if not tiles or not B(m) or not all(B(b[2]) for b in up):
                continue
            c1, s1 = legs_cost((m, B(m), "yes"), *[(b[2], B(b[2]), "no") for b in up])          # pays len(up)
            found.append(("B replicate", a, f"Yes >{x:,.0f} + No on {len(up)} buckets", c1 - (len(up) - 1), s1))
            c2, s2 = legs_cost((m, B(m), "no"), *[(b[2], B(b[2]), "yes") for b in up])          # pays 1
            found.append(("B replicate", a, f"No >{x:,.0f} + Yes on {len(up)} buckets", c2, s2))
        # C. hourly Up/Down vs the ladder (same close)
        if hm and B(hm):
            for x, m in ladder:
                if not B(m):
                    continue
                if x >= O:
                    c, s = legs_cost((hm, B(hm), "yes"), (m, B(m), "no"))
                    found.append(("C hourly", a, f"Up (open {O:,.2f}) + No >{x:,.0f}", c, s))
                else:
                    c, s = legs_cost((m, B(m), "yes"), (hm, B(hm), "no"))
                    found.append(("C hourly", a, f"Yes >{x:,.0f} + Down (open {O:,.2f})", c, s))
    found.sort(key=lambda f: f[3])
    print(f"\n{len(found)} guaranteed-payout pairs checked (each pays at least $1). Cheapest:")
    for kind, a, what, c, s in found[:15]:
        flag = "  <-- UNDER $1" if c < 1 else ""
        print(f"  {kind:11s} {a:8s} cost ${c:.4f} for >= $1, size {s:8.1f}   {what}{flag}")
    print(f"under $1 after fees: {sum(1 for f in found if f[3] < 1)}; locked total ${sum((1 - f[3]) * f[4] for f in found if f[3] < 1):.2f}")


if __name__ == "__main__":
    main()
