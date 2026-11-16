"""Price-bias study on history: for real taker BUY prints, realized win rate vs price paid,
by price bucket and seconds-to-close, net of the taker fee. Time-split: first 60% of windows
(by start time) = discovery, last 40% = check. Timestamps: data-api ts minus 2 s ~ real match time."""
import sys, os, json, glob, collections
import numpy as np

def load(root):
    ev = {json.loads(l)["w"]: json.loads(l) for l in open(os.path.join(root, "events.jsonl"))}
    out = []
    for f in glob.glob(os.path.join(root, "trades", "*.json")):
        w = int(os.path.basename(f)[:-5]); e = ev.get(w)
        if not e or e["open"] is None or e["close"] is None: continue
        up = e["close"] >= e["open"]
        for ts, oi, side, p, size in json.load(open(f)):
            if side != 1: continue
            tau = w + 300 - (ts - 2)
            if tau < 0 or tau > 300 or not (0.005 < p < 0.995): continue
            won = (oi == 0) == up
            out.append((w, tau, p, size, won))
    return out

def table(rows, label):
    PB = [0, .05, .1, .2, .3, .4, .5, .6, .7, .8, .9, .95, 1.0]
    TB = [(0, 10), (10, 30), (30, 60), (60, 120), (120, 200), (200, 300)]
    print(label)
    print("price\\tau " + " ".join(f"{a:>3d}-{b:<3d}         " for a, b in TB))
    res = {}
    for i in range(len(PB) - 1):
        cells = []
        for a, b in TB:
            sel = [r for r in rows if PB[i] <= r[2] < PB[i + 1] and a <= r[1] < b]
            if len(sel) < 30: cells.append(" " * 20); continue
            # per-trade net pnl per share, clustered by window for t
            byw = collections.defaultdict(list)
            for w, tau, p, s, won in sel: byw[w].append((1.0 if won else 0.0) - p - 0.07 * p * (1 - p))
            wm = np.array([np.mean(v) for v in byw.values()])
            t = wm.mean() / (wm.std(ddof=1) / np.sqrt(len(wm))) if len(wm) > 2 and wm.std() > 0 else 0
            res[(i, a)] = (wm.mean(), t, len(wm))
            cells.append(f"{100*wm.mean():+6.1f}c t{t:+5.1f} n{len(wm):4d}")
        print(f"{PB[i]:.2f}-{PB[i+1]:.2f} " + " ".join(cells))
    return res

if __name__ == "__main__":
    root = sys.argv[1]
    rows = load(root)
    ws = sorted({r[0] for r in rows}); cut = ws[int(len(ws) * 0.6)]
    print(f"{root}: {len(rows)} taker-buy prints over {len(ws)} windows; split at window {cut}")
    a = table([r for r in rows if r[0] < cut], "DISCOVERY (first 60%): net cents/share for buying at the print price")
    b = table([r for r in rows if r[0] >= cut], "CHECK (last 40%)")
