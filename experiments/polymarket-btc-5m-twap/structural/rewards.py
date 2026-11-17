"""Estimate what a two-sided quote would earn from Polymarket liquidity-reward pools, from live books.

Scoring (Polymarket liquidity rewards): an order of size n at distance s cents from the midpoint, within
the market's max spread v, scores ((v - s) / v)^2 * n; orders below the market's min size do not count.
A maker's score is the smaller of its two sides (a one-sided maker gets a third when the midpoint is in
0.10-0.90). The daily pool is split in proportion to score. Competing score is taken from the public book
as the larger side's total (each level treated as one qualifying order), which is the most competition the
book allows. Our quote: N shares per side one tick inside the best bid/ask, or at it when the spread is one
tick. Fill risk is read from the last 24 h of trades: shares that traded at or through our price per side.

Usage: python3 -I rewards.py [N=1000] [top=30]
"""
import concurrent.futures as cf
import gzip
import json
import sys
import time
import urllib.request

UA = {"User-Agent": "Mozilla/5.0", "Accept-Encoding": "gzip"}


def get(u):
    for i in range(4):
        try:
            with urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=60) as r:
                b = r.read()
                return json.loads(gzip.decompress(b) if r.headers.get("Content-Encoding") == "gzip" else b)
        except Exception:
            time.sleep(1.5 * (i + 1))
    return None


def pools():
    out = []
    for off in range(0, 1000, 100):
        page = get(f"https://gamma-api.polymarket.com/events?active=true&closed=false&limit=100&offset={off}&order=volume24hr&ascending=false")
        if not page:
            break
        for e in page:
            for m in e.get("markets", []):
                rate = sum(float(r.get("rewardsDailyRate") or 0) for r in m.get("clobRewards") or [])
                if rate > 0 and m.get("acceptingOrders") and m.get("enableOrderBook"):
                    out.append((rate, m))
    return sorted(out, key=lambda x: -x[0])


def evaluate(item, N):
    rate, m = item
    v, mins = float(m.get("rewardsMaxSpread") or 0), float(m.get("rewardsMinSize") or 0)
    tick = float(m.get("orderPriceMinTickSize") or 0.01)
    yes = json.loads(m["clobTokenIds"])[0]
    b = get(f"https://clob.polymarket.com/book?token_id={yes}")
    if not b or not b.get("bids") or not b.get("asks"):
        return None
    bids = sorted(((float(x["price"]), float(x["size"])) for x in b["bids"]), reverse=True)
    asks = sorted((float(x["price"]), float(x["size"])) for x in b["asks"])
    qb = [x for x in bids if x[1] >= mins] or bids
    qa = [x for x in asks if x[1] >= mins] or asks
    mid = (qb[0][0] + qa[0][0]) / 2
    score = lambda p, n: ((v - abs(p - mid) * 100) / v) ** 2 * n if abs(p - mid) * 100 < v and n >= mins else 0.0
    Qb = sum(score(p, n) for p, n in bids)
    Qa = sum(score(p, n) for p, n in asks)
    spread = asks[0][0] - bids[0][0]
    pb = bids[0][0] + tick if spread > tick + 1e-9 else bids[0][0]
    pa = asks[0][0] - tick if spread > tick + 1e-9 else asks[0][0]
    ours = min(score(pb, N), score(pa, N))
    if ours <= 0:
        return None
    share = ours / (ours + max(Qb, Qa))
    # 24 h of trades: shares that would have reached our bid (taker sells at <= pb) and our ask (taker buys at >= pa)
    cond, now, hit_b, hit_a, prices = m["conditionId"], time.time(), 0.0, 0.0, []
    cur = None
    for _ in range(20):
        d = get(f"https://data-api.polymarket.com/v2/trades?conditionId={cond}&limit=500" + (f"&cursor={cur}" if cur else ""))
        if not d:
            break
        rows = [x for x in d["data"] if now - x["timestamp"] < 86400]
        for x in rows:
            p = x["price"] if x["outcome_index"] == 0 else 1 - x["price"]
            sell_yes = (x["outcome_index"] == 0 and x["side"] == "SELL") or (x["outcome_index"] == 1 and x["side"] == "BUY")
            prices.append((x["timestamp"], p))
            if sell_yes and p <= pb + 1e-9:
                hit_b += x["size"]
            if not sell_yes and p >= pa - 1e-9:
                hit_a += x["size"]
        if len(rows) < len(d["data"]) or not d["pagination"].get("has_more"):
            break
        cur = d["pagination"]["next_cursor"]
    prices.sort()
    move = abs(prices[-1][1] - prices[0][1]) if len(prices) > 1 else 0.0
    return {"q": m.get("question", "")[:62], "pool": rate, "v": v, "min": mins, "mid": mid, "spread": spread, "share": share,
            "usd_day": rate * share, "capital": N * (pb + 1 - pa), "hit_b": hit_b, "hit_a": hit_a, "move24": move}


def main():
    N = int(sys.argv[1]) if len(sys.argv) > 1 else 1000
    top = int(sys.argv[2]) if len(sys.argv) > 2 else 30
    P = pools()
    print(f"{len(P)} rewarded markets in the top-1000 events by 24 h volume, ${sum(r for r, _ in P):,.0f}/day in pools")
    with cf.ThreadPoolExecutor(6) as pool:
        res = [r for r in pool.map(lambda it: evaluate(it, N), P[:top]) if r]
    res.sort(key=lambda r: -r["usd_day"] / r["capital"])
    print(f"Two-sided quote of {N} shares per side; competition = larger side's qualifying book score")
    print("  $/day  per $1k   pool $/d  share   mid   spread  max-spr  24h shares through our bid / ask   24h price move  market")
    for r in res:
        print(f"  {r['usd_day']:6.2f}  {1000 * r['usd_day'] / r['capital']:6.2f}   {r['pool']:7.0f}  {r['share']:5.1%}  {r['mid']:.3f}  {r['spread']:.3f}  "
              f"{r['v']:4.1f}c    {r['hit_b']:9.0f} / {r['hit_a']:<9.0f}            {r['move24']:.3f}       {r['q']}")


if __name__ == "__main__":
    main()
