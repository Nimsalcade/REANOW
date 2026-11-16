"""Run a list of strategy configs over windows that have trades; print one JSON line each."""
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import engine as E

def main(runs, lat=1):
    d = E.Data(); ws = [w for w in d.windows if w.trades]
    print("windows with trades:", len(ws), flush=True)
    for name, P in runs:
        fills = E.run_maker(d, P, ws) if name == "maker" else E.run(d, name, P, ws, lat=P.get("lat", lat))
        print(name, json.dumps(P), json.dumps(E.summarize(fills, len(ws))), flush=True)

if __name__ == "__main__":
    main(json.loads(open(sys.argv[1]).read()))
