"""flightopt command line."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sqlite3
import sys
import tempfile
import time
import zipfile
from pathlib import Path

from . import db as dbm
from .airports import AirportIndex, build_airport_file, import_airports


def _engine(args):
    from .planner import Engine
    return Engine(args.db, args.models)


def cmd_init(args):
    con = dbm.connect(args.db)
    path = args.airports
    if args.ourairports and args.airportsdata:
        path = Path(args.db).parent / 'us_airports.csv'
        build_airport_file(args.ourairports, args.airportsdata, str(path))
    n = import_airports(con, path)
    print(json.dumps(dict(airports=n, db=str(args.db))))


def cmd_ingest(args):
    from . import ingest
    con = dbm.connect(args.db)
    if args.kind == 'ontime':
        for f in args.files:
            print(json.dumps(ingest.import_ontime(con, f, allow_partial=args.allow_partial)), flush=True)
    elif args.kind == 't100':
        for f in args.files:
            print(json.dumps(ingest.import_t100(con, f)))
    elif args.kind == 'fares':
        cmap = json.loads(args.column_map) if args.column_map else None
        for f in args.files:
            print(json.dumps(ingest.import_fares(con, f, args.survey, cmap, args.member)))
    elif args.kind == 'schedule':
        for f in args.files:
            print(json.dumps(ingest.import_schedule(con, f, args.provider, AirportIndex(con))))
    elif args.kind == 'collector':
        _ingest_collector(con, Path(args.files[0]), args)


def _ingest_collector(con, data_dir: Path, args):
    """Import what the bundled collector (collector/flight_data.py fetch) downloaded."""
    from . import ingest
    current = json.loads((data_dir / 'current.json').read_text())
    path = lambda job: data_dir / current[job]['path']
    if 'oa_airports' in current and 'airport_timezones' in current:
        out = Path(args.db).parent / 'us_airports.csv'
        n = build_airport_file(str(path('oa_airports')), str(path('airport_timezones')), str(out))
        import_airports(con, out)
        print(json.dumps(dict(airports=n, built_from='collector downloads')))
    for job in sorted(current):
        if job.startswith('bts_ontime_'):
            print(json.dumps(ingest.import_ontime(con, path(job))), flush=True)
        elif job.startswith('db1c_market_'):
            print(json.dumps(ingest.import_fares(con, path(job), 'db1c')), flush=True)
    skipped = [j for j in current if not (j.startswith(('bts_ontime_', 'db1c_market_')) or j in ('oa_airports', 'airport_timezones'))]
    print(json.dumps(dict(not_imported=skipped, reason='no importer for these files yet (reference/weather/other DB1C tables)')))


def cmd_build(args):
    from .delay_model import DelayModel, load_history
    con = dbm.connect(args.db)
    t0 = time.perf_counter()
    h = load_history(con, args.since)
    periods = sorted({r[0] for r in con.execute('SELECT DISTINCT period FROM flights')})
    synthetic = bool(con.execute('SELECT MAX(synthetic) FROM sources').fetchone()[0])
    meta = dict(periods=periods, synthetic=synthetic, built_at_utc=dbm.now_utc(),
                sources=[r[0] for r in con.execute("SELECT sha256 FROM sources WHERE kind LIKE 'ontime%'")])
    tuning = None
    if len(periods) >= 2 and not args.no_tune:
        last = (dt.date.fromisoformat(periods[-1] + '-01') - dt.date(1970, 1, 1)).days
        m0 = DelayModel.fit(h.subset(h.date < last))
        tuning = m0.tune(h.subset(h.date >= last))
    m = DelayModel.fit(h, meta)
    if tuning:
        m.alphas = {t: list(v) for t, v in m0.alphas.items()}
        m.season = dict(m0.season)
        m.meta['tuning'] = tuning
    m.save(args.models or Path(args.db).parent / 'models')
    print(json.dumps(dict(rows=len(h), periods=periods, tuned=bool(tuning), seconds=round(time.perf_counter() - t0, 1),
                          alphas=m.alphas, season_floor=m.season)))


def cmd_evaluate(args):
    from .evaluate import backtest
    con = dbm.connect(args.db)
    rep = backtest(con, args.truth, sample=args.sample)
    if args.json:
        print(json.dumps(rep, indent=2))
        return
    print(f"Backtest: train {rep['train_periods'][0]}..{rep['train_periods'][-1]}, tune {rep['valid_period']}, "
          f"test {rep['test_period']} ({rep['test_rows']:,} test rows)")
    for t, res in rep['targets'].items():
        print(f"\n{res['event']} (n={res['n']:,})")
        for name in ('global_rate', 'carrier_only', 'naive_backoff_n20', 'hierarchical_model'):
            if name in res:
                ll = res[name].get('logloss')
                print(f"  {name:<20} brier {res[name]['brier']:.5f}" + (f"  logloss {ll:.4f}" if ll else ''))
        cal = res.get('calibration', [])
        if cal:
            print('  calibration (pred -> observed): ' +
                  ', '.join(f"{c['mean_predicted']:.2f}->{c['observed']:.2f}" for c in cal))
    for t, o in (rep.get('oracle') or {}).items():
        print(f"\nOracle check [{t}]: brier model {o['brier_model']:.5f} | global {o['brier_global']:.5f} | "
              f"structural oracle {o['brier_structural_oracle']:.5f} | day oracle {o['brier_day_oracle']:.5f}\n"
              f"  share of achievable booking-time gain: {o['share_of_structural_gain']}; "
              f"MAE vs structural probability {o['mae_vs_structural_probability']}")


def _print_search(res):
    if res.get('synthetic'):
        print('!! SYNTHETIC DEMO DATA: fictional carriers and performance !!')
    basis = res.get('timetable_basis') or {}
    if basis.get('kind') == 'pattern':
        print(f"Timetable: recurring pattern from {', '.join(basis.get('reference_dates', []))} (not a verified schedule)")
    for i in res.get('insights', []):
        print('* ' + i)
    for r in res['results']:
        fare = f"~${r['fare']['p50']:.0f} (IQR ${r['fare']['p25']:.0f}-{r['fare']['p75']:.0f})" if r.get('fare') else 'fare n/a'
        print(f"\n#{r['rank']}{' [pareto]' if r['pareto_optimal'] else ''}  {r['origin']}->{r['destination']}  "
              f"{r['depart_local'][11:16]} -> {r['arrive_local'][11:16]}  {r['stops']} stop(s)  sched {r['scheduled_minutes']}m  "
              f"expected +{r['expected_delay_min']:.0f}m  P90 +{r['p90_delay_min']:.0f}m  on-time {r['p_on_time']:.0%}  "
              f"misconnect {r['p_misconnect']:.1%}  {fare}")
        for l in r['legs']:
            print(f"     {l['flight']:<8} {l['origin']} {l['depart_local'][11:16]} -> {l['destination']} {l['arrive_local'][11:16]}"
                  f"  cxl {l['p_cancel']:.1%}  late15 {l['p_arrive_15plus_late']:.0%}  [{l['basis']}]")
        for c in r['connections']:
            print(f"     connect {c['airport']} {c['minutes']}m ({c['type']}) miss {c['p_miss']:.1%}")
        for n in r['notes']:
            print(f"     - {n}")
    print('\nLimitations: ' + ' | '.join(res.get('limitations', [])))


def cmd_search(args):
    from .planner import SearchRequest
    eng = _engine(args)
    req = SearchRequest(origin=args.origin, destination=args.destination, date=args.date, depart_after=args.after,
                        depart_before=args.before, arrive_by=args.by, max_stops=args.stops, profile=args.profile,
                        value_of_time=args.vot, min_connection=args.min_connection, nearby_miles=args.nearby,
                        allow_separate_tickets=args.separate_tickets, carriers=args.carrier or [],
                        avoid_carriers=args.avoid or [], results=args.results, samples=args.samples)
    t0 = time.perf_counter()
    res = eng.search(req)
    res['elapsed_seconds'] = round(time.perf_counter() - t0, 2)
    if args.json:
        print(json.dumps(res, indent=2, default=str))
    else:
        _print_search(res)
        print(f"\n({res['considered']} itineraries evaluated in {res['elapsed_seconds']}s)")


def cmd_route(args):
    print(json.dumps(_engine(args).route_report(args.origin, args.destination, args.date), indent=2, default=str))


def cmd_hubs(args):
    print(json.dumps(_engine(args).hubs(args.date, args.limit), indent=2))


def cmd_opportunities(args):
    print(json.dumps(_engine(args).opportunities(args.date, args.limit), indent=2))


def cmd_status(args):
    print(json.dumps(_engine(args).status(), indent=2, default=str))


def cmd_serve(args):
    from .server import serve
    serve(_engine(args), args.host, args.port)


def cmd_demo(args):
    from . import ingest, synthetic
    from .delay_model import DelayModel  # noqa: F401  (import check)
    base = Path(args.db).parent
    con = dbm.connect(args.db)
    import_airports(con)
    t0 = time.perf_counter()
    gen = synthetic.generate(AirportIndex(con), base / 'synthetic', seed=args.seed)
    print(json.dumps(dict(generated=gen['flights_generated'], patterns=gen['scheduled_patterns'],
                          seconds=round(time.perf_counter() - t0, 1))), flush=True)
    for f in gen['files']:
        print(json.dumps(ingest.import_ontime(con, base / 'synthetic' / f, synthetic=True)), flush=True)
    print(json.dumps(ingest.import_t100(con, base / 'synthetic' / 'SYNTHETIC_T100_Segment.csv', synthetic=True)))
    print(json.dumps(ingest.import_fares(con, base / 'synthetic' / 'SYNTHETIC_DB1C_Market.csv', 'synthetic',
                                         synthetic=True)))
    with con:
        dbm.set_meta(con, 'affiliations', synthetic.AFFILIATIONS)
        dbm.set_meta(con, 'carrier_names', {k: v[0] for k, v in synthetic.CARRIERS.items()})
    args.since, args.no_tune = None, False
    cmd_build(args)
    print('\nDemo ready. Try:\n  flightopt --db', args.db, 'search BOS LAX --date 2026-09-15\n  flightopt --db', args.db,
          'serve\n  flightopt --db', args.db, 'evaluate --truth', base / 'synthetic' / 'SYNTHETIC_truth.csv.gz')


def main(argv=None):
    p = argparse.ArgumentParser(prog='flightopt', description='US flight route optimizer: risk-aware itinerary '
                                'search, route reliability and network analysis from public BTS/FAA data.')
    p.add_argument('--db', default='data/flightopt.sqlite')
    p.add_argument('--models', default=None, help='model directory (default: <db dir>/models)')
    sub = p.add_subparsers(dest='cmd', required=True)

    c = sub.add_parser('init', help='create the database and load airports')
    c.add_argument('--airports', help='prepared airport CSV (default: bundled file)')
    c.add_argument('--ourairports', help='OurAirports airports.csv to rebuild the airport file')
    c.add_argument('--airportsdata', help='airportsdata airports.csv (time zones)')
    c.set_defaults(func=cmd_init)

    c = sub.add_parser('ingest', help='import source files')
    c.add_argument('kind', choices=['ontime', 't100', 'fares', 'schedule', 'collector'])
    c.add_argument('files', nargs='+')
    c.add_argument('--allow-partial', action='store_true', help='accept filtered on-time files (use a separate --db)')
    c.add_argument('--survey', default='db1c', choices=['db1c', 'db1b'])
    c.add_argument('--column-map', help='JSON mapping of fare fields to column names')
    c.add_argument('--member', help='CSV member name inside a multi-file ZIP')
    c.add_argument('--provider', default='manual', help='schedule provider name')
    c.set_defaults(func=cmd_ingest)

    c = sub.add_parser('build', help='fit and tune the leg-performance model')
    c.add_argument('--since', help='only use history on/after YYYY-MM-DD')
    c.add_argument('--no-tune', action='store_true')
    c.set_defaults(func=cmd_build)

    c = sub.add_parser('evaluate', help='time-split backtest against baselines')
    c.add_argument('--truth', help='synthetic truth file for an oracle comparison')
    c.add_argument('--sample', type=int, default=80_000)
    c.add_argument('--json', action='store_true')
    c.set_defaults(func=cmd_evaluate)

    c = sub.add_parser('search', help='find and rank itineraries')
    c.add_argument('origin', help='airport (ORD) or metro (CHI, NYC, WAS, BAYAREA, LAAREA, ...)')
    c.add_argument('destination')
    c.add_argument('--date', required=True)
    c.add_argument('--after', default='00:00', help='earliest local departure HH:MM')
    c.add_argument('--before', default='23:59', help='latest local departure HH:MM')
    c.add_argument('--by', help='arrive by HH:MM local (append +1 for next day)')
    c.add_argument('--stops', type=int, help='maximum stops (default: 1, widened to 2 if few options)')
    c.add_argument('--profile', default='balanced', choices=['balanced', 'fastest', 'reliable', 'cheapest'])
    c.add_argument('--vot', type=float, default=50.0, help='value of time, $/hour')
    c.add_argument('--min-connection', type=int, help='override connection buffer, minutes')
    c.add_argument('--nearby', type=float, default=0, help='also consider airports within N miles')
    c.add_argument('--separate-tickets', action='store_true', help='allow unprotected self-connections')
    c.add_argument('--carrier', nargs='+', help='only these carriers')
    c.add_argument('--avoid', nargs='+', help='exclude these carriers')
    c.add_argument('--results', type=int, default=8)
    c.add_argument('--samples', type=int, default=2000)
    c.add_argument('--json', action='store_true')
    c.set_defaults(func=cmd_search)

    c = sub.add_parser('route', help='historical route report')
    c.add_argument('origin')
    c.add_argument('destination')
    c.add_argument('--date', help='also list the timetable for this date')
    c.set_defaults(func=cmd_route)

    c = sub.add_parser('hubs', help='airport connectivity ranking for a date')
    c.add_argument('--date', required=True)
    c.add_argument('--limit', type=int, default=20)
    c.set_defaults(func=cmd_hubs)

    c = sub.add_parser('opportunities', help='unserved nonstop markets ranked by passenger-hours saved')
    c.add_argument('--date', required=True)
    c.add_argument('--limit', type=int, default=25)
    c.set_defaults(func=cmd_opportunities)

    c = sub.add_parser('status', help='dataset, model and limitations')
    c.set_defaults(func=cmd_status)

    c = sub.add_parser('serve', help='run the local web app')
    c.add_argument('--host', default='127.0.0.1')
    c.add_argument('--port', type=int, default=8765)
    c.set_defaults(func=cmd_serve)

    c = sub.add_parser('demo', help='generate labeled synthetic data, import it and fit the model')
    c.add_argument('--seed', type=int, default=2026)
    c.set_defaults(func=cmd_demo)

    args = p.parse_args(argv)
    try:
        args.func(args)
    except (ValueError, OSError, sqlite3.Error, zipfile.BadZipFile, KeyError) as e:
        print(f'error: {e}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
