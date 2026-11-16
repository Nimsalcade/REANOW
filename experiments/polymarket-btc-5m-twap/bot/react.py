"""Event study: how fast does the Polymarket book reflect a model fair-value jump driven by spot?"""
import sys, os, bisect
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
import live as LV

LAGS = (0, 100, 200, 300, 500, 750, 1000, 1500, 2000, 3000, 5000, 8000)

def study(L, min_dfair=0.04):
    events = []
    for w in sorted(L.windows):
        if (w, 0) not in L.tob or w not in L.K: continue
        tobs = L.tob[(w, 0)]; tt = [x[0] for x in tobs]
        def mid(r):
            i = bisect.bisect_right(tt, r) - 1
            if i < 0: return None
            x = tobs[i]
            return (x[1] + x[3]) / 2 if x[1] is not None and x[3] is not None else None
        S = w * 1000; r = S + 10000; nxt = 0
        while r < S + 285000:
            if r >= nxt:
                f0 = L.fair(w, r - 1000); f1 = L.fair(w, r); m0 = mid(r)
                if f0 and f1 and m0 is not None and 0.1 < m0 < 0.9 and abs(f1[0] - f0[0]) >= min_dfair:
                    d = f1[0] - f0[0]
                    events.append([None if mid(r + dt) is None else (mid(r + dt) - m0) / d for dt in LAGS])
                    nxt = r + 5000
            r += 200
    return events

if __name__ == "__main__":
    rows = LV.load_rows()
    for as_ in (sys.argv[1:] or ["btc"]):
        L = LV.Live(asset=as_, rows=rows)
        ev = study(L)
        if not ev: print(as_, "no events"); continue
        a = np.array([[np.nan if v is None else v for v in e] for e in ev])
        print(f"{as_}: {len(ev)} fair-value jumps (>=4 pts in 1 s); median share of the jump reflected in Up mid:")
        print("   " + "  ".join(f"+{dt}ms:{np.nanmedian(a[:, j]):+.2f}" for j, dt in enumerate(LAGS)))
