"""Run strategy configs over live recordings for every asset; print and log pooled results.

Usage: python3 -I evaluate.py CONFIG.json [assets=btc,eth,...]
CONFIG: [["taker_value", {...}], ["maker_value", {...}], ...]
"""
import sys, os, json, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import live as LV

def main():
    cfg = json.load(open(sys.argv[1]))
    assets = LV.ASSETS
    for a in sys.argv[2:]:
        if a.startswith("assets="): assets = a.split("=", 1)[1].split(",")
    rows = LV.load_rows()
    lives = {}
    for as_ in assets:
        try:
            L = LV.Live(asset=as_, rows=rows)
            n = len([w for w in L.labels if w in L.windows and (w, 0) in L.tob])
            if n: lives[as_] = (L, n)
        except Exception as e:
            print(as_, "skipped:", e)
    print("windows per asset:", {a: n for a, (L, n) in lives.items()}, "meas:", {a: round(L.meas, 6) if L.meas else None for a, (L, n) in lives.items()})
    out = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "results.jsonl"), "a")
    for name, P in cfg:
        pooled, per = [], {}
        for as_, (L, n) in lives.items():
            fills = LV.run(L, name, P, P.get("lat_ms", 250))
            for f in fills: f["as"] = as_
            pooled += fills
            per[as_] = LV.summarize(fills, n)
        total = LV.summarize(pooled, sum(n for L, n in lives.values()))
        print(name, json.dumps(P), "POOLED", json.dumps(total))
        for as_, sm in per.items():
            if sm.get("trades"): print("   ", as_, json.dumps(sm))
        out.write(json.dumps({"at": int(time.time()), "strategy": name, "params": P, "pooled": total, "per_asset": per}) + "\n")

if __name__ == "__main__":
    main()
