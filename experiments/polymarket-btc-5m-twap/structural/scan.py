"""Scan every active Polymarket event for baskets whose payout is guaranteed by the market structure.

1. Mutually exclusive outcomes (negRisk events, at most one market resolves Yes): buying No on every
   tradable outcome pays at least n - 1. No ask = 1 - Yes bid (mirrored books), so the basket costs
   n - sum(Yes bids) and locks sum(Yes bids) - 1 before fees. Leaving outcomes out keeps the guarantee.
2. Nested markets in one event ("by <date>" deadlines, "above/below <price>" ladders): P(Yes) must be
   monotone. If the market that must be at least as likely (A) is offered below the bid of the one that is
   implied by it (B), Yes(A) + No(B) pays at least $1 and costs ask_A + 1 - bid_B < 1.

Prices come from Gamma (bestBid/bestAsk); candidates are re-checked on the CLOB book with sizes and taker
fees (rate x p(1-p) per share, from each market's feeSchedule).

Usage: python3 -I scan.py
"""
import concurrent.futures as cf
import datetime as dt
import gzip
import json
import re
import time
import urllib.request

UA = {"User-Agent": "Mozilla/5.0", "Accept-Encoding": "gzip"}
MONTHS = {m: i for i, m in enumerate(["january", "february", "march", "april", "may", "june", "july", "august",
                                      "september", "october", "november", "december"], 1)}


def get(u):
    for i in range(5):
        try:
            with urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=60) as r:
                b = r.read()
                return json.loads(gzip.decompress(b) if r.headers.get("Content-Encoding") == "gzip" else b)
        except Exception:
            time.sleep(1.5 * (i + 1))
    return None


def events():
    out, off = [], 0
    while True:
        page = get(f"https://gamma-api.polymarket.com/events?active=true&closed=false&limit=100&offset={off}")
        if not page:
            break
        out += page
        off += 100
    return out


def tradable(m):
    return m.get("enableOrderBook") and m.get("acceptingOrders") and not m.get("closed") and m.get("active")


def fee(m, p):
    fs = m.get("feeSchedule") or {}
    return (fs.get("rate") or 0.0) * (p * (1 - p)) ** (fs.get("exponent") or 1) if m.get("feesEnabled") else 0.0


def yes_token(m):
    return json.loads(m["clobTokenIds"])[0]


def book(token):
    b = get(f"https://clob.polymarket.com/book?token_id={token}")
    if not b:
        return None, None
    bids = sorted(((float(x["price"]), float(x["size"])) for x in b.get("bids", [])), reverse=True)
    asks = sorted((float(x["price"]), float(x["size"])) for x in b.get("asks", []))
    return (bids[0] if bids else None), (asks[0] if asks else None)


def order_key(m, title):
    """(key, sign, group): within a group P(Yes) must rise with key * sign; None if not a nested ladder item."""
    t = (m.get("groupItemTitle") or "").strip()
    tl, q = t.lower(), (m.get("question") or "").lower() + " " + title.lower()
    if re.match(r"(by\s+)?(january|february|march|april|may|june|july|august|september|october|november|december)\s+\d{1,2}(,?\s*\d{4})?$", tl) \
            and re.search(r"\bby\b|\bbefore\b", q) and m.get("endDate"):
        end = dt.datetime.fromisoformat(m["endDate"].replace("Z", "+00:00")).timestamp()
        return end, +1, "deadline"                       # later deadline: at least as likely
    n = re.fullmatch(r"\s*(↑|↓|>|<|≥|≤|above|over|below|under)?\s*\$?([\d,]*\.?\d+)\s*([kmbt%])?\s*", tl)
    if not n:
        return None
    v = float(n.group(2).replace(",", "")) * {"k": 1e3, "m": 1e6, "b": 1e9, "t": 1e12}.get(n.group(3) or "", 1)
    word = n.group(1) or ""
    if not word and re.search(r"how low|as low as|\bdrop\b|\bfall\b|\bdip\b|\bbelow\b|\bunder\b", q):
        return v, +1, "down"                             # "how low will X go": a higher level is reached first
    if word in ("↑", ">", "≥", "above", "over") or (not word and re.search(r"\babove\b|\bover\b|\breach\b|\bhit\b|\bexceed|how high", q)):
        return v, -1, "up"                               # higher threshold: less likely
    if word in ("↓", "<", "≤", "below", "under") or (not word and re.search(r"\bbelow\b|\bunder\b|\bdip\b", q)):
        return v, +1, "down"
    return None


def main():
    evs = events()
    print(f"{len(evs)} active events")
    basket, nested = [], []
    for e in evs:
        ms = [m for m in e.get("markets", []) if tradable(m)]
        if len(ms) < 2:
            continue
        if e.get("negRisk"):
            bids = [float(m.get("bestBid") or 0) for m in ms]
            if sum(bids) > 1.0:
                basket.append((sum(bids) - 1, e, ms))
        else:
            groups = {}
            for m in ms:
                k = order_key(m, e.get("title", ""))
                if k:
                    groups.setdefault(k[2], []).append((k[0] * k[1], m))
            for g in groups.values():
                g.sort(key=lambda km: km[0])                   # ascending in P(Yes)
                for i in range(len(g)):
                    for j in range(i + 1, len(g)):
                        if g[j][0] == g[i][0]:
                            continue
                        lo, hi = g[i][1], g[j][1]              # hi must be at least as likely as lo
                        a_hi, b_lo = float(hi.get("bestAsk") or 1), float(lo.get("bestBid") or 0)
                        if a_hi < b_lo:
                            nested.append((b_lo - a_hi, e, hi, lo))
    print(f"Gamma candidates: {len(basket)} exclusive-outcome baskets with sum(Yes bids) > $1, {len(nested)} nested pairs out of order")

    def check_basket(c):
        _, e, ms = c
        legs = []
        for m in ms:
            bb, _ = book(yes_token(m))
            if not bb:
                return None
            legs.append((m, bb))
        s = sum(bb[0] for _, bb in legs)
        fees = sum(fee(m, 1 - bb[0]) for m, bb in legs)
        size = min(bb[1] for _, bb in legs)
        return {"kind": "all-No basket", "event": e["title"], "n": len(legs), "edge": s - 1 - fees, "gross": s - 1, "size": size}

    def check_nested(c):
        _, e, hi, lo = c
        _, ah = book(yes_token(hi))
        bl, _ = book(yes_token(lo))
        if not ah or not bl:
            return None
        cost = ah[0] + (1 - bl[0]) + fee(hi, ah[0]) + fee(lo, 1 - bl[0])
        return {"kind": "nested pair", "event": e["title"], "buy_yes": hi.get("groupItemTitle"), "buy_no": lo.get("groupItemTitle"),
                "edge": 1 - cost, "gross": bl[0] - ah[0], "size": min(ah[1], bl[1])}

    with cf.ThreadPoolExecutor(6) as pool:
        checked = [r for r in pool.map(check_basket, basket) if r] + [r for r in pool.map(check_nested, nested) if r]
    live = sorted((r for r in checked if r["edge"] > 0), key=lambda r: -r["edge"] * r["size"])
    print(f"Confirmed on the CLOB book after fees: {len(live)} of {len(checked)} re-checked")
    for r in live[:25]:
        extra = f" ({r['n']} outcomes)" if r["kind"] == "all-No basket" else f" — Yes '{r['buy_yes']}' + No '{r['buy_no']}'"
        print(f"  {r['kind']:13s} edge {100 * r["edge"]:5.2f} c per set (gross {100 * r['gross']:5.2f} c), size {r['size']:8.1f}, "
              f"${r['edge'] * r['size']:7.2f} locked  | {r['event'][:70]}{extra}")
    json.dump(checked, open("scan-latest.json", "w"), indent=1, default=str)


if __name__ == "__main__":
    main()
