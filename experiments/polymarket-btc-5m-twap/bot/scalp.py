"""Jump scalp: enter like F14 (taker at ask <= mid+slack after lat), then exit after H seconds either
(a) as taker at the bid (second fee) or (b) as maker: post an ask at max(entry+tick, ceil(mid_H)), filled
only on a trade-through print (taker BUY of the outcome above it, or taker SELL of the mirror below 1-ask)
before E-2 s; otherwise hold to resolution. Discovery script; candidates must be forward-tested."""
import sys, os, math, bisect, collections
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
import live as LV

def fee(p): return 0.07 * p * (1 - p)

def run(L, P):
    out = []
    tr_all = L.trades
    for w in sorted(L.F):
        if w not in L.windows or (w, 0) not in L.tob or w not in L.K: continue
        up = L.F[w] >= L.K[w]; S = w * 1000; E = S + 300000; last = -10**12; n = 0
        tr = tr_all.get(w, []); tts = [x[0] for x in tr]
        for r in range(S + 5000, S + 295000, 100):
            if r - last < 5000 or n >= P.get("max_trades", 3): continue
            f1 = L.fair_fast(w, r); f0 = L.fair_fast(w, r - 1000)
            if not f0 or not f1 or abs(f1[0] - f0[0]) < P["jump"]: continue
            a = 0 if f1[0] > f0[0] else 1
            bk = L.book(w, a, r)
            if not bk or bk[1] is None or bk[3] is None: continue
            mid = (bk[1] + bk[3]) / 2
            if not 0.10 <= mid <= 0.90: continue
            last = r; n += 1
            fb = L.book(w, a, r + P["lat"])
            if not fb or fb[3] is None or fb[3] > mid + P["slack"]: continue
            px = fb[3]; won = (a == 0) == up
            hold = (1.0 if won else 0.0) - px - fee(px)
            row = {"as": L.asset, "w": w, "hold": hold}
            for H in P["H"]:
                tH = r + P["lat"] + H * 1000
                if tH >= E - 2000:
                    row[f"tk{H}"] = hold; row[f"mk{H}"] = hold; continue
                bH = L.book(w, a, tH)
                bid = bH[1] if bH and bH[1] is not None else None
                row[f"tk{H}"] = (bid - px - fee(px) - fee(bid)) if bid is not None else hold
                midH = (bH[1] + bH[3]) / 2 if bH and bH[1] is not None and bH[3] is not None else px
                ask_exit = max(px + 0.01, math.ceil(midH * 100 - 1e-9) / 100)
                j = bisect.bisect_right(tts, tH + P["lat"]); filled = False
                while j < len(tr) and tr[j][0] <= E - 2000:
                    ts, ta, p, s, sd = tr[j]
                    if (ta == a and sd == "BUY" and p > ask_exit) or (ta == 1 - a and sd == "SELL" and p < 1 - ask_exit):
                        filled = True; break
                    j += 1
                row[f"mk{H}"] = (ask_exit - px - fee(px)) if filled else hold
            out.append(row)
    return out

if __name__ == "__main__":
    P = {"jump": 0.06, "slack": 0.02, "lat": 250, "H": (3, 5, 10, 20, 40)}
    rows_all = LV.load_rows(); rows = []
    for as_ in LV.ASSETS:
        rows += run(LV.Live(asset=as_, rows=rows_all), P)
    split = 1794846600
    for label, sel in (("ALL", rows), ("before 16:30", [r for r in rows if r["w"] < split]), ("16:30 onward", [r for r in rows if r["w"] >= split])):
        print(f"{label}: {len(sel)} entries")
        for key in ["hold"] + [f"tk{H}" for H in P["H"]] + [f"mk{H}" for H in P["H"]]:
            v = np.array([r[key] for r in sel])
            byt = collections.defaultdict(float)
            for r in sel: byt[r["w"]] += r[key]
            tv = np.array(list(byt.values())); tt = tv.mean() / (tv.std(ddof=1) / np.sqrt(len(tv))) if len(tv) > 2 and tv.std() > 0 else float("nan")
            print(f"   {key:6s} mean {100*v.mean():+6.2f}c/share  sd {100*v.std():5.1f}c  t {v.mean()/(v.std()/np.sqrt(len(v))):+5.2f}  t_time {tt:+5.2f}")
