"""Can taker volume make money through Polymarket's taker-rebate tiers?

Weighted volume (official worked example): a $50 crypto trade at 50c earns 50 x 0.5 x 2.3 = $57.50 wV and at 5c
50 x 0.95 x 2.3 = $109.25, so wV = notional x (1 - p) x weight = shares x p(1 - p) x weight.
Taker fee (live Gamma feeSchedule, exponent 1): shares x rate x p(1 - p).
Hence fee / wV = rate / weight at every price: weighted volume is the taker fee paid divided by ~3 %.

Prints (1) live fee rates per fee type with fee per $ of wV, (2) the best-case arithmetic of farming each tier,
(3) the month-1 tier path for a constant daily farm, (4) liquidity-reward pools, the program that pays for
resting quotes instead of refunding fees.

Usage: python3 -I rebates.py
"""
import collections
import gzip
import json
import time
import urllib.request

# name, 30-day wV threshold $, rebate share of taker fees, one-time bonus $ (Polymarket taker-tier table)
TIERS = [("Bronze", 2_000, 0.03, 10), ("Silver", 20_000, 0.08, 50), ("Gold", 200_000, 0.18, 250),
         ("Platinum", 1_000_000, 0.32, 1_500), ("Diamond", 4_000_000, 0.44, 7_500), ("Obsidian", 10_000_000, 0.50, 25_000)]
# category weights (third-party guides; crypto 2.3 confirmed by the official example). fee type -> weight
WEIGHT = {"crypto_fees_v2": 2.3, "economics_fees": 1.7, "culture_fees": 1.7, "weather_fees": 1.7, "general_fees": 1.7,
          "politics_fees": 1.3, "finance_prices_fees": 1.3, "mentions_fees": 1.3, "tech_fees": 1.3,
          "sports_fees_v2": 1.0, "sports_fees_v3": 1.0}
UA = {"User-Agent": "Mozilla/5.0", "Accept-Encoding": "gzip"}
CRYPTO_RATE = 0.07


def get(u):
    with urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=60) as r:
        b = r.read()
        return json.loads(gzip.decompress(b) if r.headers.get("Content-Encoding") == "gzip" else b)


def survey():
    """Fee schedules and liquidity-reward pools over the top-500 active events by 24 h volume."""
    rates, pools, n = collections.Counter(), [], 0
    for off in range(0, 500, 100):
        for e in get(f"https://gamma-api.polymarket.com/events?active=true&closed=false&limit=100&offset={off}"
                     "&order=volume24hr&ascending=false"):
            for m in e.get("markets", []):
                n += 1
                fs = m.get("feeSchedule") or {}
                rates[(m.get("feeType") or "fee-free", fs.get("rate"), fs.get("rebateRate"))] += 1
                for r in m.get("clobRewards") or []:
                    if r.get("rewardsDailyRate"):
                        pools.append((float(r["rewardsDailyRate"]), m.get("feeType") or "fee-free", m.get("rewardsMaxSpread"),
                                      m.get("rewardsMinSize"), (m.get("question") or "")[:60]))
    print(f"(1) fee schedules on {n} markets of the top-500 events by 24 h volume")
    print("    fee type              markets  taker rate  maker-rebate share  weight  fee per $1 of wV")
    for (ft, rate, rr), c in sorted(rates.items(), key=lambda x: -x[1]):
        w = WEIGHT.get(ft)
        per = f"{100 * rate / w:.2f} %" if rate and w else ("no wV" if not rate else "weight unknown")
        print(f"    {ft:22s} {c:6d}   {rate if rate is not None else '-':>8}   {rr if rr is not None else '-':>8}          "
              f"{w if w else '-':>5}   {per}")
    return pools


def farming(fee_per_wv, spread_per_wv):
    print(f"\n(2) farming a tier from zero, best case: target-tier rebate on every fee, bonuses of all tiers up to it")
    print(f"    (crypto: fee = {100 * fee_per_wv:.2f} % of wV; hedged Up+Down pair at 50c with a 1-tick spread adds "
          f"{100 * spread_per_wv:.2f} % of wV)")
    print("    tier       wV needed   fees paid   rebate   bonuses   refunded   net, no spread   net, 1-tick spread")
    bonus = 0
    for name, wv, r, b in TIERS:
        bonus += b
        fees = wv * fee_per_wv
        net = r * fees + bonus - fees
        print(f"    {name:9s} {wv:>11,.0f} {fees:>11,.0f} {r * fees:>8,.0f} {bonus:>9,.0f}   {(r * fees + bonus) / fees:>6.1%}"
              f"   {net:>14,.0f}   {net - wv * spread_per_wv:>18,.0f}")


def month_one(fee_per_wv, spread_per_wv, target=10_000_000, days=30):
    daily, wv, refunded, paid = target / days, 0.0, 0.0, 0.0
    rates = []
    for _ in range(days):  # tier recalculated at midnight UTC from the trailing 30 days
        r = max([t[2] for t in TIERS if wv >= t[1]], default=0.0)
        rates.append(r)
        paid += daily * fee_per_wv
        refunded += r * daily * fee_per_wv
        wv += daily
    bonus = sum(t[3] for t in TIERS if wv >= t[1])
    print(f"\n(3) constant ${daily:,.0f} wV per day to reach ${target:,.0f} in {days} days: average rebate "
          f"{sum(rates) / days:.1%}, fees ${paid:,.0f}, rebates ${refunded:,.0f}, bonuses ${bonus:,.0f} -> "
          f"net ${refunded + bonus - paid:,.0f} before spread, ${refunded + bonus - paid - target * spread_per_wv:,.0f} with it;"
          f" every later month at 50 %: ${-0.5 * paid - target * spread_per_wv:,.0f}")


def rewards(pools):
    now = int(time.time())
    slugs = [f"{a}-updown-5m-{now - now % 300}" for a in ("btc", "eth", "sol", "xrp")] + \
            [f"{a}-updown-15m-{now - now % 900}" for a in ("btc", "eth", "sol", "xrp")]
    ud = get("https://gamma-api.polymarket.com/events?limit=50&" + "&".join("slug=" + s for s in slugs))
    with_pool = sum(1 for e in ud for m in e["markets"] if any(r.get("rewardsDailyRate") for r in m.get("clobRewards") or []))
    pools.sort(key=lambda p: -p[0])
    by = collections.defaultdict(float)
    for p in pools:
        by[p[1]] += p[0]
    print(f"\n(4) liquidity-reward pools (paid for resting quotes near the midpoint, filled or not): {len(pools)} markets, "
          f"${sum(p[0] for p in pools):,.0f}/day; live crypto Up/Down markets with a pool: {with_pool}/{len(ud)}")
    print("    by fee type: " + ", ".join(f"{k} ${v:,.0f}" for k, v in sorted(by.items(), key=lambda x: -x[1])))
    for p in pools[:6]:
        print(f"    ${p[0]:>6,.0f}/day  max spread {p[2]}c  min size {p[3]}  {p[4]}")


def main():
    fee_per_wv = CRYPTO_RATE / WEIGHT["crypto_fees_v2"]
    spread_per_wv = 0.01 / (2 * 0.5 * 0.5 * WEIGHT["crypto_fees_v2"])
    try:
        pools = survey()
    except OSError as e:
        print(f"(1) survey unavailable ({e}); using crypto rate {CRYPTO_RATE}")
        pools = None
    farming(fee_per_wv, spread_per_wv)
    month_one(fee_per_wv, spread_per_wv)
    if pools is not None:
        rewards(pools)


if __name__ == "__main__":
    main()
