"""Score the pre-registered candidates in forward.json on windows that started after the freeze."""
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import live as LV

HERE = os.path.dirname(os.path.abspath(__file__))
cfg = json.load(open(os.path.join(HERE, "forward.json")))
rows = LV.load_rows()
lives = {}
for as_ in LV.ASSETS:
    try:
        L = LV.Live(asset=as_, rows=rows)
    except Exception:
        continue
    ws = [w for w in L.F if w >= cfg["first_window"] and w in L.windows and (w, 0) in L.tob and w in L.K]
    if ws:
        lives[as_] = (L, ws)
print("forward windows:", {a: len(ws) for a, (L, ws) in lives.items()}, "total", sum(len(ws) for L, ws in lives.values()))
for tag, name, P in cfg["candidates"]:
    pooled, per = [], {}
    for as_, (L, ws) in lives.items():
        keep = set(ws)
        fills = [f for f in LV.run(L, name, P, P.get("lat_ms", 250)) if f["w"] in keep]
        for f in fills: f["as"] = as_
        pooled += fills
        per[as_] = LV.summarize(fills, len(ws))
    t = LV.summarize(pooled, sum(len(ws) for L, ws in lives.values()))
    print(f"{tag} {name} {json.dumps(P)}\n    POOLED {json.dumps(t)}")
    for a, s in per.items():
        if s.get("trades"): print(f"    {a}: fills {s['trades']} ¢/share {round(100*s['pnl_per_share'],2)} PnL ${s['pnl_total']} t {s['t_stat']}")
