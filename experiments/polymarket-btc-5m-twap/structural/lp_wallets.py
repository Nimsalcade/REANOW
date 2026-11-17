"""Who actually earns Polymarket liquidity rewards, and do they net a profit after their trading results?

1. Top rewarded markets (Gamma clobRewards) -> holders (data-api v2 /holders) and recent takers (/trades).
2. Candidate liquidity providers: wallets that hold, or recently traded, the most distinct rewarded markets
   (two-sided quoters merge their pairs, so holdings alone do not show them).
3. For each candidate: every REWARD payout (/v2/activity type REWARD) and MAKER_REBATE, and the P&L of every
   open and closed position (/v2/positions, total_pnl = realized + unrealized).
Net = rewards + maker rebates + position P&L. All public data; no orders.

Usage: python3 -I lp_wallets.py [markets=80] [wallets=60]
"""
import concurrent.futures as cf
import datetime as dt
import gzip
import json
import sys
import time
import urllib.request
from collections import defaultdict

UA = {"User-Agent": "Mozilla/5.0", "Accept-Encoding": "gzip"}
API = "https://data-api.polymarket.com/v2"


def get(u):
    for i in range(5):
        try:
            with urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=60) as r:
                b = r.read()
                return json.loads(gzip.decompress(b) if r.headers.get("Content-Encoding") == "gzip" else b)
        except Exception:
            time.sleep(1.5 * (i + 1))
    return None


def paged(url, cap=40):
    rows, cur = [], None
    for _ in range(cap):
        d = get(url + (f"&cursor={cur}" if cur else ""))
        if not d or "data" not in d:
            break
        rows += d["data"]
        if not d.get("pagination", {}).get("has_more"):
            return rows, True
        cur = d["pagination"]["next_cursor"]
    return rows, False


def rewarded_markets(n):
    out = []
    for off in range(0, 1000, 100):
        page = get(f"https://gamma-api.polymarket.com/events?active=true&closed=false&limit=100&offset={off}&order=volume24hr&ascending=false")
        if not page:
            break
        for e in page:
            for m in e.get("markets", []):
                rate = sum(float(r.get("rewardsDailyRate") or 0) for r in m.get("clobRewards") or [])
                if rate > 0:
                    out.append((rate, m["conditionId"], m.get("question", "")))
    return sorted(out, reverse=True)[:n]


def wallet_report(w):
    rew, rew_done = paged(f"{API}/activity?user={w}&type=REWARD&limit=500")
    reb, _ = paged(f"{API}/activity?user={w}&type=MAKER_REBATE&limit=500", cap=10)
    if not rew:
        return {"wallet": w, "rewards": 0.0}
    pos_open, ok_open = paged(f"{API}/positions?user={w}&limit=500", cap=20)
    pos_closed, ok_closed = paged(f"{API}/positions?user={w}&status=CLOSED&limit=500", cap=40)
    ts = sorted(x["timestamp"] for x in rew)
    days = max(1.0, (ts[-1] - ts[0]) / 86400 + 1)
    last30 = sum(float(x["usdc_size"] or 0) for x in rew if ts[-1] - x["timestamp"] < 30 * 86400)
    return {"wallet": w, "name": rew[0].get("name") or "", "rewards": sum(float(x["usdc_size"] or 0) for x in rew),
            "reward_days": len(rew), "first": ts[0], "last": ts[-1], "span_days": days, "rewards_30d": last30,
            "rebates": sum(float(x["usdc_size"] or 0) for x in reb),
            "pnl_open": sum(float(p.get("total_pnl") or 0) for p in pos_open),
            "pnl_closed": sum(float(p.get("realized_pnl") or 0) for p in pos_closed),
            "open_value": sum(float(p.get("current_value") or 0) for p in pos_open),
            "n_open": len(pos_open), "n_closed": len(pos_closed), "complete": rew_done and ok_open and ok_closed}


def main():
    nm = int(sys.argv[1]) if len(sys.argv) > 1 else 80
    nw = int(sys.argv[2]) if len(sys.argv) > 2 else 60
    mk = rewarded_markets(nm)
    print(f"{len(mk)} rewarded markets, ${sum(r for r, _, _ in mk):,.0f}/day in pools")
    seen = defaultdict(set)      # wallet -> rewarded markets where it holds a position or traded recently
    def footprint(x):
        h = get(f"{API}/holders?condition={x[1]}&limit=500") or {}
        t = get(f"{API}/trades?conditionId={x[1]}&limit=500") or {}
        ws = {y["proxy_wallet"] for tok in h.get("data", []) for y in tok.get("holders", [])}
        return ws | {y["proxy_wallet"] for y in t.get("data", [])}
    with cf.ThreadPoolExecutor(6) as pool:
        for (rate, cond, q), ws in zip(mk, pool.map(footprint, mk)):
            for w in ws:
                seen[w].add(cond)
    cands = sorted(seen, key=lambda w: -len(seen[w]))[:nw]
    print(f"{len(seen)} wallets hold or recently traded rewarded markets; checking the {len(cands)} present in the most "
          f"({len(seen[cands[0]])} … {len(seen[cands[-1]])} of {len(mk)} markets)")
    with cf.ThreadPoolExecutor(4) as pool:
        reps = [r for r in pool.map(wallet_report, cands) if r["rewards"] > 0]
    reps.sort(key=lambda r: -r["rewards"])
    day = lambda t: dt.datetime.fromtimestamp(t, dt.timezone.utc).strftime("%Y-%m-%d")
    print(f"\n{len(reps)} of {len(cands)} candidates received liquidity rewards")
    print("  name/wallet           rewards $   payout days  span        $/day(30d)  rebates $  closed P&L $  open P&L $   NET $      open value  markets o/c")
    tot = defaultdict(float)
    for r in reps:
        net = r["rewards"] + r["rebates"] + r["pnl_closed"] + r["pnl_open"]
        for k in ("rewards", "rebates", "pnl_closed", "pnl_open"):
            tot[k] += r[k]
        tot["net"] += net
        print(f"  {(r['name'] or r['wallet'])[:20]:20s} {r['rewards']:10,.0f}   {r['reward_days']:5d}   {day(r['first'])}..{day(r['last'])[5:]}  "
              f"{r['rewards_30d'] / 30:8.1f}   {r['rebates']:8,.0f}   {r['pnl_closed']:11,.0f}  {r['pnl_open']:10,.0f}  {net:10,.0f}   "
              f"{r['open_value']:10,.0f}  {r['n_open']}/{r['n_closed']}{'' if r['complete'] else ' (capped)'}")
    print(f"  TOTAL                {tot['rewards']:10,.0f}                                         {tot['rebates']:8,.0f}   {tot['pnl_closed']:11,.0f}  {tot['pnl_open']:10,.0f}  {tot['net']:10,.0f}")
    json.dump(reps, open("lp-wallets.json", "w"), indent=1)


if __name__ == "__main__":
    main()
