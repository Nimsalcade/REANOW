"""Patterns in official BTC Up/Down results (5 m and 15 m) and betting progressions run on them.

Results come from Gamma (outcomePrices of each btc-updown-{5m,15m}-{start} market), cached in data/outcomes_{dur}.json.
Tests: base rate; streaks (P(same as previous) and after k in a row, against a runs test); round of the hour,
hour of day (UTC) and day of week; and progressions per clock hour: bet $1 on a side, raise the stake after a
loss (double, or enough to recover the hour's losses plus $1), stop at the first win or after max_rounds.
Entry costs the opening ask plus the taker fee (0.07 p(1-p)); ASK is the measured median opening ask.

Usage: python3 -I patterns.py [days=60] [ask=0.51]
"""
import concurrent.futures as cf
import datetime as dt
import gzip
import json
import math
import os
import sys
import time
import urllib.request
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
UA = {"User-Agent": "Mozilla/5.0", "Accept-Encoding": "gzip"}
DUR = {"5m": 300, "15m": 900}


def get(u):
    for i in range(6):
        try:
            with urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=60) as r:
                b = r.read()
                return json.loads(gzip.decompress(b) if r.headers.get("Content-Encoding") == "gzip" else b)
        except Exception:
            time.sleep(2 * (i + 1))
    return None


def outcomes(dur, days):
    path = os.path.join(HERE, "data", f"outcomes_{dur}.json")
    cache = json.load(open(path)) if os.path.exists(path) else {}
    now = int(time.time())
    step = DUR[dur]
    last = now - now % step - step
    starts = [s for s in range(last - days * 86400, last + 1, step) if str(s) not in cache]
    batches = [starts[i:i + 40] for i in range(0, len(starts), 40)]

    def fetch(batch):
        res = {}
        evs = get("https://gamma-api.polymarket.com/events?limit=50&" + "&".join(f"slug=btc-updown-{dur}-{s}" for s in batch)) or []
        for e in evs:
            m = (e.get("markets") or [{}])[0]
            op = m.get("outcomePrices")
            if not op or not e.get("closed"):
                continue
            p = json.loads(op)
            if p[0] in ("1", "1.0") or p[1] in ("1", "1.0"):
                res[e["slug"].rsplit("-", 1)[1]] = 1 if p[0] in ("1", "1.0") else 0
        return res
    with cf.ThreadPoolExecutor(4) as pool:
        for r in pool.map(fetch, batches):
            cache.update(r)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    json.dump(cache, open(path, "w"))
    lo = last - days * 86400
    return sorted((int(s), u) for s, u in cache.items() if int(s) >= lo)


def z(p, n, p0=0.5):
    return (p - p0) / math.sqrt(p0 * (1 - p0) / n) if n else float("nan")


def analyse(dur, seq, ask):
    step = DUR[dur]
    n = len(seq)
    ups = [u for _, u in seq]
    cost = ask + 0.07 * ask * (1 - ask)
    print(f"\n==== BTC {dur}: {n} settled windows, {dt.datetime.fromtimestamp(seq[0][0], dt.timezone.utc):%Y-%m-%d} .. "
          f"{dt.datetime.fromtimestamp(seq[-1][0], dt.timezone.utc):%Y-%m-%d}; break-even win rate at ask {ask:.2f} + fee: {cost:.2%}")
    print(f"  Up {sum(ups) / n:.2%} (z {z(sum(ups) / n, n):+.2f})")
    # streaks: consecutive windows only
    same = [(seq[i][1] == seq[i - 1][1]) for i in range(1, n) if seq[i][0] - seq[i - 1][0] == step]
    print(f"  same result as the previous window: {sum(same) / len(same):.2%} of {len(same)} (z {z(sum(same) / len(same), len(same)):+.2f})")
    for k in (2, 3, 4, 5, 6):
        cont, tot = 0, 0
        for i in range(k, n):
            if seq[i][0] - seq[i - k][0] != k * step:
                continue
            prev = [seq[j][1] for j in range(i - k, i)]
            if len(set(prev)) == 1:
                tot += 1
                cont += seq[i][1] == prev[0]
        if tot:
            print(f"    after {k} in a row: continues {cont / tot:.2%} of {tot} (z {z(cont / tot, tot):+.2f})")
    # round of the hour, hour of day, weekday
    for name, key, levels in (("round of the hour", lambda s: (s % 3600) // step, range(3600 // step)),
                              ("hour of day UTC", lambda s: (s % 86400) // 3600, range(24)),
                              ("day of week", lambda s: dt.datetime.fromtimestamp(s, dt.timezone.utc).weekday(), range(7))):
        g = defaultdict(list)
        for s, u in seq:
            g[key(s)].append(u)
        zs = [z(sum(g[l]) / len(g[l]), len(g[l])) for l in levels if g[l]]
        chi = sum(x * x for x in zs)
        worst = max(levels, key=lambda l: abs(z(sum(g[l]) / len(g[l]), len(g[l]))) if g[l] else 0)
        print(f"  {name}: Up rate {min(sum(g[l]) / len(g[l]) for l in levels if g[l]):.1%}..{max(sum(g[l]) / len(g[l]) for l in levels if g[l]):.1%}; "
              f"chi-square {chi:.1f} on {len(zs)} groups (≈{len(zs)} expected by chance); most extreme: {worst} at "
              f"{sum(g[worst]) / len(g[worst]):.1%} (z {z(sum(g[worst]) / len(g[worst]), len(g[worst])):+.2f})")
    # progressions per clock hour
    hours = defaultdict(list)
    for s, u in seq:
        hours[s - s % 3600].append((s, u))
    res_at = dict(seq)
    print(f"  progressions per clock hour (entry {cost:.4f} per $1 payout; every bet's expected value is {(0.5 - cost) / cost:+.1%} at a 50% win rate):")
    for rule in ("always Up", "repeat last result", "opposite of last result"):
        for scheme in ("double", "recover+1"):
            for maxr in (4, 6):
                total, won_h, worst, staked, bets = 0.0, 0, 0.0, 0.0, 0
                for h, rows in sorted(hours.items()):
                    loss, stake, pnl, prev = 0.0, 1.0, 0.0, None
                    for r, (s, u) in enumerate(rows[:maxr]):
                        last = res_at.get(s - step)
                        side = 1 if rule == "always Up" else (last if rule == "repeat last result" else (1 - last if last is not None else None))
                        if side is None:
                            continue
                        shares = stake / cost                     # $stake buys stake/cost shares paying $1 each
                        bets += 1
                        staked += stake
                        if u == side:
                            pnl += shares - stake
                            break
                        pnl -= stake
                        loss += stake
                        stake = stake * 2 if scheme == "double" else (loss + 1.0) / (1 / cost - 1)
                    total += pnl
                    won_h += pnl > 0
                    worst = min(worst, pnl)
                print(f"    {rule:24s} {scheme:9s} max {maxr} rounds: hours won {won_h / len(hours):.1%}, total ${total:+,.2f} over {len(hours)} h "
                      f"(${total / len(hours):+.3f}/h), worst hour ${worst:,.2f}, staked ${staked:,.0f}")


def main():
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 60
    ask = float(sys.argv[2]) if len(sys.argv) > 2 else 0.51
    for dur in ("5m", "15m"):
        seq = outcomes(dur, days)
        if seq:
            analyse(dur, seq, ask)


if __name__ == "__main__":
    main()
