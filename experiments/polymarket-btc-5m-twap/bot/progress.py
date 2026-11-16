"""Sweep strategy grids over all live recordings; write a ranked progress report.

Writes data/progress.md (latest ranking) and appends every config's result to data/results.jsonl.
Usage: python3 -I progress.py [min_trades]
"""
import itertools
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import live as LV

HERE = os.path.dirname(os.path.abspath(__file__))


def grid():
    g = []
    for jump, edge, look, lat in itertools.product((0.03, 0.04, 0.06, 0.08), (0.01, 0.02, 0.04, 0.06), (500, 1000, 2000), (100, 250, 500)):
        g.append(("taker_snipe", {"jump": jump, "edge": edge, "look_ms": look, "lat_ms": lat}))
    for (t0, t1), edge, lat in itertools.product(((0, 300), (0, 120), (120, 240), (240, 300), (270, 300), (285, 300)), (0.01, 0.03, 0.06), (100, 250, 500)):
        g.append(("taker_value", {"t_from": t0, "t_to": t1, "edge": edge, "lat_ms": lat, "pmax": 0.995}))
    for margin, rq, join in itertools.product((0.01, 0.02, 0.03, 0.05), (100, 250, 1000), (0, 1)):
        g.append(("maker_value", {"margin": margin, "requote_ms": rq, "join_only": join, "lat_ms": 250}))
    return g


def main():
    min_trades = int(sys.argv[1]) if len(sys.argv) > 1 else 20
    rows = LV.load_rows()
    lives = {}
    for as_ in LV.ASSETS:
        try:
            L = LV.Live(asset=as_, rows=rows)
        except Exception as e:  # asset without enough data yet
            continue
        n = len([w for w in L.F if w in L.windows and (w, 0) in L.tob and w in L.K])
        if n:
            lives[as_] = (L, n)
    nwin = {a: n for a, (L, n) in lives.items()}
    results = []
    out = open(os.path.join(HERE, "data", "results.jsonl"), "a")
    stamp = int(time.time())
    for name, P in grid():
        pooled, per = [], {}
        for as_, (L, n) in lives.items():
            fills = LV.run(L, name, P, P.get("lat_ms", 250))
            for f in fills:
                f["as"] = as_
            pooled += fills
            per[as_] = LV.summarize(fills, n)
        tot = LV.summarize(pooled, sum(nwin.values()))
        results.append((name, P, tot, per))
        out.write(json.dumps({"at": stamp, "strategy": name, "params": P, "pooled": tot,
                              "per_asset": {a: s for a, s in per.items() if s.get("trades")}}) + "\n")
    ok = [r for r in results if r[2].get("trades", 0) >= min_trades and r[2].get("t_stat") is not None]
    ok.sort(key=lambda r: -r[2]["t_stat"])
    lines = [f"# Live paper progress — {time.strftime('%Y-%m-%d %H:%M:%SZ', time.gmtime(stamp))}", "",
             f"Windows with books and outcomes: {nwin} (total {sum(nwin.values())}).",
             f"Configs: {len(results)}; shown: top 25 by window-clustered t-stat with ≥{min_trades} fills.", "",
             "| # | strategy | params | fills | windows | win | avg px | shares | PnL $ | ¢/share | t |",
             "|---|---|---|---|---|---|---|---|---|---|---|"]
    for i, (name, P, t, per) in enumerate(ok[:25], 1):
        lines.append(f"| {i} | {name} | `{json.dumps(P)}` | {t['trades']} | {t['windows_traded']} | {t['win_rate']} | "
                     f"{t['avg_price']} | {t['shares']} | {t['pnl_total']} | {round(100 * t['pnl_per_share'], 2)} | {t['t_stat']} |")
    best = {}
    for name, P, t, per in results:
        if t.get("trades", 0) >= min_trades and t.get("t_stat") is not None and (name not in best or t["t_stat"] > best[name][2]["t_stat"]):
            best[name] = (name, P, t, per)
    lines += ["", "## Best config per strategy family, by asset", ""]
    for name, (n_, P, t, per) in best.items():
        lines.append(f"- **{name}** `{json.dumps(P)}` pooled ¢/share {round(100 * t['pnl_per_share'], 2)}, t {t['t_stat']}, fills {t['trades']}")
        for a, s in per.items():
            if s.get("trades"):
                lines.append(f"  - {a}: fills {s['trades']}, ¢/share {round(100 * s['pnl_per_share'], 2)}, PnL ${s['pnl_total']}, t {s['t_stat']}")
    open(os.path.join(HERE, "data", "progress.md"), "w").write("\n".join(lines) + "\n")
    print("\n".join(lines[:35]))


if __name__ == "__main__":
    main()
