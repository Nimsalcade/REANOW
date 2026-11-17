"""Decode the gabagool fills: inventory per outcome, average costs, pair cost, P&L under each outcome,
and how the position evolves fill by fill. Usage: python3 -I decode.py FILE.json [FILE.json ...]"""
import json, sys, collections

def analyse(path):
    fills = json.load(open(path))
    asset = fills[0]["side"].split("_")[0]
    q = collections.defaultdict(float); c = collections.defaultdict(float)
    print(f"\n=== {asset}: {len(fills)} fills, {fills[0]['timestamp']} -> {fills[-1]['timestamp']}")
    print("  n  time      side   px    qty   | Up qty  Down qty |  avgUp  avgDn  pair  | hedged  imbalance  locked$  worst$")
    for f in fills:
        s = "UP" if f["side"].endswith("UP") else "DOWN"
        q[s] += f["shares"]; c[s] += f["cost"]
        au = c["UP"] / q["UP"] if q["UP"] else float("nan"); ad = c["DOWN"] / q["DOWN"] if q["DOWN"] else float("nan")
        hedged = min(q["UP"], q["DOWN"]); total = c["UP"] + c["DOWN"]
        pnl_up, pnl_dn = q["UP"] - total, q["DOWN"] - total
        if f["n"] % 6 == 0 or f is fills[-1] or f["n"] <= 3:
            print(f"{f['n']:3d}  {f['timestamp'][11:]}  {s:4s} {f['price']:.2f} {f['shares']:6.2f} | {q['UP']:6.1f}  {q['DOWN']:6.1f}   | {au:.3f}  {ad:.3f}  {au+ad:.3f} | {hedged:6.1f}  {q['UP']-q['DOWN']:+7.1f}  {min(pnl_up,pnl_dn):+7.2f}  ")
    total = c["UP"] + c["DOWN"]
    au, ad = c["UP"] / q["UP"], c["DOWN"] / q["DOWN"]
    print(f"  FINAL: Up {q['UP']:.2f} sh @ {au:.4f} (${c['UP']:.2f}); Down {q['DOWN']:.2f} sh @ {ad:.4f} (${c['DOWN']:.2f}); total cost ${total:.2f}")
    print(f"  pair cost avgUp+avgDown = {au+ad:.4f}  -> locked edge per matched pair {1-au-ad:+.4f}")
    print(f"  P&L if Up wins: {q['UP']-total:+.2f}   if Down wins: {q['DOWN']-total:+.2f}   (min {min(q['UP'],q['DOWN'])-total:+.2f})")
    pairs = min(q["UP"], q["DOWN"]); extra_side = "UP" if q["UP"] > q["DOWN"] else "DOWN"
    extra = abs(q["UP"] - q["DOWN"])
    print(f"  merge {pairs:.2f} pairs -> ${pairs:.2f} back immediately; leftover {extra:.2f} {extra_side} shares (cost basis ~${extra*(au if extra_side=='UP' else ad):.2f})")
    print(f"  ROI on capital (worst case): {100*(min(q['UP'],q['DOWN'])-total)/total:+.2f}%   (best case {100*(max(q['UP'],q['DOWN'])-total)/total:+.2f}%)")
    # time-of-buy pattern: does he buy the side that just got cheap?
    prev = None; cheap_buys = 0; n = 0
    for f in fills:
        s = "UP" if f["side"].endswith("UP") else "DOWN"
        if prev and s != prev[0]: n += 1; cheap_buys += f["price"] < 0.5
        prev = (s, f["price"])
    print(f"  side switches: {n}; fills below 0.50: {sum(1 for f in fills if f['price']<0.5)} / {len(fills)}; median fill size {sorted(f['shares'] for f in fills)[len(fills)//2]}")

for p in sys.argv[1:]:
    analyse(p)
