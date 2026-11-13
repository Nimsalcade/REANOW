"""Offline tests. Flight rows are synthetic; airports come from the bundled real reference file."""
from __future__ import annotations

import csv
import datetime as dt
import io
import json
import random
import tempfile
import threading
import unittest
import urllib.request
import zipfile
from pathlib import Path

import numpy as np

from flightopt import db as dbm
from flightopt import ingest
from flightopt.airports import AirportIndex, import_airports
from flightopt.delay_model import DEP_EDGES, ENR_EDGES, DelayModel, LegForecast, load_history
from flightopt.fare_model import FareModel
from flightopt.risk import RiskParams, evaluate
from flightopt.router import ConnectionPolicy, Itinerary, Router
from flightopt.schedule import Leg, Timetable, build_timetable, from_minutes, local_minutes_to_utc

HEADER = ['Year', 'Month', 'FlightDate', 'Reporting_Airline', 'Flight_Number_Reporting_Airline', 'Origin', 'Dest',
          'CRSDepTime', 'DepDelay', 'ArrDelay', 'Cancelled', 'Diverted', 'CRSElapsedTime', 'Distance']


def ontime_zip(path: Path, rows: list[list], header=HEADER):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(header)
    w.writerows(rows)
    with zipfile.ZipFile(path, 'w') as z:
        z.writestr('data.csv', buf.getvalue())
    return path


def fresh_db(tmp: Path):
    con = dbm.connect(tmp / 'test.sqlite')
    import_airports(con)
    return con


class IngestTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.con = fresh_db(self.tmp)

    def rows(self, day='2026-06-02', n=3, carrier='N1', dep='0700'):
        return [[2026, 6, day, carrier, str(100 + i), 'ORD', 'LGA', dep, '5.00', '-2.00', '0.00', '0.00', '123.00',
                 '733.00'] for i in range(n)]

    def test_partial_file_refused_without_flag(self):
        f = ontime_zip(self.tmp / 'a.zip', self.rows())
        with self.assertRaisesRegex(ValueError, 'filtered'):
            ingest.import_ontime(self.con, f)
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM flights').fetchone()[0], 0)

    def test_month_replacement_and_2400(self):
        ingest.import_ontime(self.con, ontime_zip(self.tmp / 'a.zip', self.rows(n=3)), allow_partial=True)
        rows = self.rows(n=2, dep='2400')
        ingest.import_ontime(self.con, ontime_zip(self.tmp / 'b.zip', rows), allow_partial=True)
        got = self.con.execute('SELECT COUNT(*), MAX(dep_local) FROM flights').fetchone()
        self.assertEqual(tuple(got), (2, 1440))  # revised month replaced, not appended

    def test_invalid_flag_fails_closed(self):
        bad = self.rows(n=1)
        bad[0][10] = '2.00'
        with self.assertRaisesRegex(ValueError, 'cancelled'):
            ingest.import_ontime(self.con, ontime_zip(self.tmp / 'c.zip', bad), allow_partial=True)
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM flights').fetchone()[0], 0)

    def test_marketing_file_keeps_brand_and_operator(self):
        header = ['FlightDate', 'IATA_Code_Marketing_Airline', 'Flight_Number_Marketing_Airline', 'Operating_Airline ',
                  'Flight_Number_Operating_Airline', 'Origin', 'Dest', 'CRSDepTime', 'DepDelay', 'ArrDelay',
                  'Cancelled', 'Diverted', 'CRSElapsedTime', 'Distance']
        rows = [['2026-06-02', 'UA', '5501', 'OO', '4401', 'ORD', 'MSN', '0905', '0', '0', '0', '0', '55', '109']]
        res = ingest.import_ontime(self.con, ontime_zip(self.tmp / 'm.zip', rows, header), allow_partial=True)
        self.assertEqual(res['kind'], 'ontime_marketing')
        r = self.con.execute('SELECT mkt, op, fn, op_fn FROM flights').fetchone()
        self.assertEqual(tuple(r), ('UA', 'OO', '5501', '4401'))

    def test_fares_aggregate_and_filter(self):
        p = self.tmp / 'm.csv'
        with p.open('w', newline='') as f:
            w = csv.writer(f)
            w.writerow(['Year', 'Month', 'Origin', 'Dest', 'TkCarrier', 'Passengers', 'MktFare', 'MktCoupons',
                        'NonStopMiles', 'BulkFare'])
            for fare in (100, 200, 300, 400):
                w.writerow([2026, 7, 'ORD', 'LGA', 'N1', 5, fare, 1, 733, 0])
            w.writerow([2026, 7, 'ORD', 'LGA', 'N1', 5, 0, 1, 733, 0])       # award fare: dropped
            w.writerow([2026, 7, 'ORD', 'LGA', 'N1', 5, 250, 1, 733, 1])     # bulk: dropped
        res = ingest.import_fares(self.con, p, 'db1c')
        self.assertEqual(res['records_kept'], 4)
        self.assertEqual(res['dropped'], {'fare_out_of_range': 1, 'bulk': 1})
        fm = FareModel(self.con)
        e = fm.estimate('ORD', 'LGA', 'N1', True, 733)
        self.assertTrue(200 <= e['p50'] <= 300, e)
        rev = fm.estimate('LGA', 'ORD', 'N1', True, 733)
        self.assertIn('reverse', rev['basis'])


class TimetableTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.con = fresh_db(self.tmp)
        self.ap = AirportIndex(self.con)

    def test_local_to_utc_across_dst(self):
        # 08:00 Chicago is 13:00 UTC in summer, 14:00 UTC in winter
        s = local_minutes_to_utc(dt.date(2026, 7, 1), 480, 'America/Chicago')
        w = local_minutes_to_utc(dt.date(2026, 12, 1), 480, 'America/Chicago')
        self.assertEqual(from_minutes(s).hour, 13)
        self.assertEqual(from_minutes(w).hour, 14)

    def test_pattern_frequency_and_overnight(self):
        rows = []
        for k, day in enumerate(['2026-06-02', '2026-06-09', '2026-06-16', '2026-06-23']):
            rows.append([2026, 6, day, 'N1', '1', 'LAX', 'JFK', '2330', '0', '0', '0', '0', '320', '2475'])  # red-eye
            if k >= 3:
                rows.append([2026, 6, day, 'N1', '2', 'LAX', 'JFK', '0800', '0', '0', '0', '0', '320', '2475'])
        ingest.import_ontime(self.con, ontime_zip(self.tmp / 'p.zip', rows), allow_partial=True)
        tt = build_timetable(self.con, self.ap, dt.date(2026, 6, 30), days=1)
        fns = {l.fn: l for l in tt.legs}
        self.assertIn('1', fns)
        self.assertNotIn('2', fns)  # 1 of 4 reference Tuesdays < 50% frequency
        arr_local = from_minutes(fns['1'].arr, 'America/New_York')
        self.assertEqual(arr_local.date(), dt.date(2026, 7, 1))  # overnight arrival lands next local day


def mk(i, carrier, fn, o, d, dep, arr, dist):
    return Leg(i, carrier, carrier, fn, fn, o, d, dep, arr, 0, 'd', dist, 1.0, False)


def random_timetable(seed: int, n_legs=260):
    rng = random.Random(seed)
    aps = ['ORD', 'DEN', 'ATL', 'DFW', 'LAX', 'BOS', 'SEA', 'MSP', 'CLT', 'PHX']
    legs = []
    base = 29_600_000
    for i in range(n_legs):
        o, d = rng.sample(aps, 2)
        dep = base + rng.randint(0, 20 * 60)
        legs.append(Leg(i, rng.choice(['N1', 'N2', 'R1']), 'X', str(rng.randint(1, 40)), '1', o, d, dep,
                        dep + rng.randint(50, 300), 600, '2026-06-01', 500.0, 1.0, False))
    return Timetable(legs, {})


class RouterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tmp = Path(tempfile.mkdtemp())
        cls.ap = AirportIndex(fresh_db(tmp))

    def test_pruning_never_drops_an_itinerary(self):
        for seed in range(6):
            tt = random_timetable(seed)
            pol = ConnectionPolicy(affiliations={'R1': ['N1']}, max_circuity=9, circuity_slack_miles=10_000)
            r = Router(tt, self.ap, pol)
            win = {'BOS': (tt.legs[0].dep - 10_000, tt.legs[-1].dep + 10_000)}
            deadline = max(l.arr for l in tt.legs) - 200
            a = {it.key() for it in r.candidates({'BOS': 0}, {'LAX': 0}, win, deadline, 3)}
            b = {it.key() for it in r.candidates({'BOS': 0}, {'LAX': 0}, win, deadline, 3, use_bounds=False)}
            self.assertEqual(a, b, f'seed {seed}')
            self.assertTrue(a, 'expected at least one itinerary')

    def test_connection_rules(self):
        t = 29_600_000
        legs = [mk(0, 'N1', '1', 'BOS', 'ORD', t, t + 150, 867),
                mk(1, 'N1', '2', 'ORD', 'LAX', t + 180, t + 420, 1745),   # 30 min: too tight
                mk(2, 'N1', '3', 'ORD', 'LAX', t + 200, t + 440, 1745),   # 50 min: ok
                mk(3, 'N2', '4', 'ORD', 'LAX', t + 260, t + 500, 1745),   # other carrier
                mk(4, 'N1', '1', 'ORD', 'LAX', t + 175, t + 415, 1745)]   # through flight
        r = Router(Timetable(legs, {}), self.ap, ConnectionPolicy())
        found = {it.key(): it.connection_types for it in r.candidates({'BOS': 0}, {'LAX': 0}, {'BOS': (t, t)}, t + 900, 2)}
        self.assertEqual(found, {(0, 2): ['online'], (0, 4): ['through']})
        r2 = Router(Timetable(legs, {}), self.ap, ConnectionPolicy(allow_separate_tickets=True, separate_ticket_min=100))
        found2 = {it.key() for it in r2.candidates({'BOS': 0}, {'LAX': 0}, {'BOS': (t, t)}, t + 900, 2)}
        self.assertIn((0, 3), found2)

    def test_recovery_profile_matches_brute_force(self):
        for seed in range(4):
            tt = random_timetable(100 + seed, 300)
            pol = ConnectionPolicy()
            r = Router(tt, self.ap, pol)
            group = frozenset({'N1'})
            prof = r.recovery(frozenset({'LAX'}), group)
            rng = random.Random(seed)
            for _ in range(40):
                ap = rng.choice(['ORD', 'DEN', 'BOS', 'SEA', 'ATL'])
                t0 = tt.legs[0].dep + rng.randint(0, 900)
                self.assertEqual(prof(ap, t0), self.brute(tt, pol, ap, t0, 'LAX', group))

    def brute(self, tt, pol, src, t0, dst, group):
        """Earliest arrival by connection scan with the same buffers."""
        best = {src: t0}
        ready = {src: t0}
        for l in tt.legs:
            if l.mkt not in group and l.op not in group:
                continue
            if l.origin in ready and ready[l.origin] <= l.dep and l.origin != dst:
                if l.arr < best.get(l.dest, 1 << 62):
                    best[l.dest] = l.arr
                    if l.dest != dst:
                        ready[l.dest] = min(ready.get(l.dest, 1 << 62), l.arr + pol.mct(self.ap.get(l.dest).type))
        return best.get(dst)


class StubModel:
    """Delay model returning fixed forecasts, for exact risk checks. late = {flight number: dep bin}."""
    def __init__(self, p_cancel=0.0, late=None):
        self.p_cancel, self.late = p_cancel, late or {}

    def leg(self, op, op_fn, *a):
        dep = np.zeros(len(DEP_EDGES) - 1)
        dep[self.late.get(op_fn, 4)] = 1  # bin 4 = [0, 5) minutes
        enr = np.zeros(len(ENR_EDGES) - 1)
        enr[4] = 1  # [0, 5)
        return LegForecast(dep, enr, self.p_cancel, 0.0, 50, 'this flight', 50, {'dep': (5, 60), 'enr': (5, 20)})


class RiskTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ap = AirportIndex(fresh_db(Path(tempfile.mkdtemp())))

    def network(self):
        t = 29_600_000
        legs = [mk(0, 'N1', '1', 'BOS', 'ORD', t, t + 150, 867),
                mk(1, 'N1', '2', 'ORD', 'LAX', t + 200, t + 440, 1745),
                mk(2, 'N1', '3', 'ORD', 'LAX', t + 400, t + 640, 1745)]
        tt = Timetable(legs, {})
        return Router(tt, self.ap, ConnectionPolicy()), Itinerary([legs[0], legs[1]], 0, 0, ['online'])

    def test_no_delay_world_is_on_time(self):
        r, it = self.network()
        res = evaluate(it, r, StubModel(), frozenset({'LAX'}), 6, RiskParams(samples=500), 0.0, 'America/Los_Angeles')
        self.assertEqual(res['p_on_time'], 1.0)
        self.assertEqual(res['p_misconnect'], 0.0)

    def test_misconnect_rebooks_on_next_flight(self):
        r, it = self.network()
        # inbound leaves 30-45 min late (bin [30,45)); 50-min connection minus 25-min walk leaves 25 min of slack
        res = evaluate(it, r, StubModel(late={'1': 9}), frozenset({'LAX'}), 6, RiskParams(samples=400), 0.0, None)
        self.assertEqual(res['p_misconnect'], 1.0)
        self.assertLess(res['p_stranded'], 1e-9)
        # rebooked on flight 3, scheduled 200 min after flight 2
        self.assertTrue(195 <= res['p50_delay'] <= 205, res['p50_delay'])
        # both flights equally late: the outbound waits out the same delay, so the connection mostly holds
        res2 = evaluate(it, r, StubModel(late={'1': 12, '2': 12}), frozenset({'LAX'}), 6, RiskParams(samples=400), 0.0, None)
        self.assertLess(res2['p_misconnect'], 0.15)

    def test_certain_cancellation_uses_recovery(self):
        r, it = self.network()
        res = evaluate(it, r, StubModel(p_cancel=1.0), frozenset({'LAX'}), 6, RiskParams(samples=200), 0.0, None)
        self.assertEqual(res['p_cancel'], 1.0)
        self.assertEqual(res['p_on_time'], 0.0)


class EndToEndTests(unittest.TestCase):
    """Synthetic demo data through the public importers, model build, search and web API."""
    @classmethod
    def setUpClass(cls):
        from flightopt import synthetic
        from flightopt.cli import main
        cls.tmp = Path(tempfile.mkdtemp())
        cls.db = cls.tmp / 'e2e.sqlite'
        con = dbm.connect(cls.db)
        import_airports(con)
        gen = synthetic.generate(AirportIndex(con), cls.tmp / 'syn', start='2026-06-01', end='2026-07-31', seed=5)
        for f in gen['files']:
            ingest.import_ontime(con, cls.tmp / 'syn' / f, synthetic=True)
        ingest.import_t100(con, cls.tmp / 'syn' / 'SYNTHETIC_T100_Segment.csv', synthetic=True)
        ingest.import_fares(con, cls.tmp / 'syn' / 'SYNTHETIC_DB1C_Market.csv', 'synthetic', synthetic=True)
        with con:
            dbm.set_meta(con, 'affiliations', synthetic.AFFILIATIONS)
        con.close()
        assert main(['--db', str(cls.db), 'build']) == 0

    def test_model_round_trip(self):
        m = DelayModel.load(self.tmp / 'models')
        f = m.leg('N2', '999999', 'ATL', 'LGA', 500, 7)
        self.assertAlmostEqual(float(f.dep.sum()), 1.0, places=5)
        self.assertGreater(f.p_cancel, 0)
        self.assertNotEqual(f.evidence_level, 'this flight')  # unseen flight backs off to a parent level

    def test_model_recovers_carrier_ordering(self):
        # ground truth: N2 is the most punctual network carrier, U1 the least
        m = DelayModel.load(self.tmp / 'models')
        p = {c: m.leg(c, 'none', 'NOWHERE', 'NOWHERE', 600, 7).p_dep15 for c in ('N2', 'N1', 'U1')}
        self.assertLess(p['N2'], p['N1'])
        self.assertLess(p['N1'], p['U1'])

    def test_search_and_api(self):
        from flightopt.planner import Engine, SearchRequest
        from flightopt.server import make_handler
        from http.server import ThreadingHTTPServer
        eng = Engine(self.db)
        res = eng.search(SearchRequest('BOS', 'LAX', '2026-09-15', depart_after='06:00', samples=300))
        self.assertTrue(res['results'])
        top = res['results'][0]
        self.assertTrue(0 <= top['p_on_time'] <= 1)
        self.assertTrue(res['synthetic'])
        for r in res['results']:
            self.assertGreaterEqual(r['depart_local'][11:16], '06:00')
        httpd = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(eng))
        th = threading.Thread(target=httpd.serve_forever, daemon=True)
        th.start()
        try:
            base = f'http://127.0.0.1:{httpd.server_address[1]}'
            st = json.load(urllib.request.urlopen(base + '/api/status'))
            self.assertTrue(st['synthetic'])
            s = json.load(urllib.request.urlopen(base + '/api/search?origin=CHI&destination=SEA&date=2026-09-15&samples=200'))
            self.assertTrue(s['results'])
            self.assertTrue({o['code'] for o in s['origins']} >= {'ORD', 'MDW'})
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                urllib.request.urlopen(base + '/api/search?origin=ZZZ&destination=SEA&date=2026-09-15')
            self.assertEqual(ctx.exception.code, 400)
            html = urllib.request.urlopen(base + '/').read()
            self.assertIn(b'flightopt', html)
        finally:
            httpd.shutdown()
            httpd.server_close()

    def test_backtest_runs(self):
        from flightopt.evaluate import backtest
        con = dbm.connect(self.db)
        with self.assertRaisesRegex(ValueError, 'at least 3'):
            backtest(con)  # two months only: refuses rather than scoring on training data


if __name__ == '__main__':
    unittest.main()
