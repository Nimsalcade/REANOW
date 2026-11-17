"""Replay gabagool22's measured settings (../gabagool/README.md, legs.py) on recorded live books.

Two-sided resting bids of 10 shares from his median start time after the open until his median stop time
before the close, inventory capped at max_imb shares, with or without a pair-cost cap. Uses pairmaker.py's
price-time queue model. 5 m: start +14 s, stop 81 s before close; 15 m: start +21 s, stop 103 s.

Usage: python3 -I clone.py 5m|15m
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import live as LV
import pairmaker as PM

HIS = {"5m": {"t_from": 14, "t_stop": 81, "imbs": (20, 40)}, "15m": {"t_from": 21, "t_stop": 103, "imbs": (40, 120)}}


def main(dur):
    rows = LV.load_rows(dur)
    lives = {a: LV.Live(asset=a, rows=rows, dur=LV.DURS[dur]) for a in LV.ASSETS}
    h = HIS[dur]
    base = {"fill": "queue", "clip": 10, "t_from": h["t_from"], "t_stop": h["t_stop"], "rq_ms": 1000, "lat_ms": 250}
    print(f"gabagool settings on recorded {dur} books: start +{h['t_from']} s, stop {h['t_stop']} s before the close, 10-share bids, queue model")
    for mode in ("join", "improve"):
        for margin in (-1.0, 0.0, 0.02):
            for imb in h["imbs"]:
                res = PM.run(lives, dict(base, mode=mode, margin=margin, max_imb=imb))
                s = PM.summarize(res)
                if not s.get("windows"):
                    print("  no windows with fills"); return
                cap = "no cap" if margin < 0 else f"pair <= {1 - margin:.2f}"
                print(f"  {mode:7s} {cap:12s} max imbalance {imb:3d}: sessions {s['windows']:3d}  pairs/session {s['pairs'] / s['windows']:6.1f}  "
                      f"P&L ${s['pnl']:8.2f} (${s['pnl_per_window']:+.3f}/session)  pair cost {s['median_pair_cost']}  "
                      f"locked ${s['locked']:.2f}  leftovers ${s['leftover_pnl']:.2f}  rebate est ${s['rebate_est']:.2f}  t {s['t_time']}", flush=True)
                if mode == "join" and margin < 0 and imb == h["imbs"][-1]:
                    for a in LV.ASSETS:
                        ra = [r for r in res if r["as"] == a]
                        if ra:
                            sa = PM.summarize(ra)
                            print(f"      {a:5s} sessions {sa['windows']:3d}  P&L ${sa['pnl']:8.2f}  pair cost {sa['median_pair_cost']}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "15m")
