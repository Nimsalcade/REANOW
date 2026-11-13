"""Synthetic demo data with known ground truth.

Airports and geography are real (bundled reference file). Carriers, schedules, delays,
traffic and fares are SYNTHETIC: fictional carrier codes (N1, N2, N3, L1, R1, U1) are used so
no output can be mistaken for a real airline's performance. Files are written in the BTS
reporting-carrier on-time, T-100 segment and DB1B-style market column layouts, so the demo
exercises the same importers as real downloads. A truth file records the generating
probabilities, which lets `flightopt evaluate --truth` measure how close the fitted model
gets to the best achievable forecast.
"""
from __future__ import annotations

import csv
import datetime as dt
import gzip
import io
import math
import zipfile
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np

from .geo import great_circle_miles

CARRIERS = {
    # code: (name, hubs, p_late_base, p_cancel_base)
    'N1': ('Synthetic Network One', ['ORD', 'DEN', 'IAH', 'EWR', 'SFO'], 0.17, 0.012),
    'N2': ('Synthetic Network Two', ['ATL', 'DTW', 'MSP', 'SLC'], 0.13, 0.008),
    'N3': ('Synthetic Network Three', ['DFW', 'CLT', 'PHL', 'MIA'], 0.19, 0.015),
    'L1': ('Synthetic Low Cost', [], 0.20, 0.012),
    'R1': ('Synthetic Regional (flies for N1 and N2)', [], 0.22, 0.025),
    'U1': ('Synthetic Ultra Low Cost', [], 0.28, 0.030),
}
AFFILIATIONS = {'R1': ['N1', 'N2']}
SPOKES = ['LAX', 'SEA', 'BOS', 'LGA', 'JFK', 'DCA', 'IAD', 'BWI', 'MCO', 'TPA', 'FLL', 'LAS', 'PHX', 'SAN', 'PDX',
          'AUS', 'BNA', 'RDU', 'STL', 'MCI', 'CLE', 'PIT', 'IND', 'CMH', 'CVG', 'MKE', 'MSY', 'SAT', 'SMF', 'SJC',
          'OAK', 'BDL', 'RSW', 'JAX', 'OMA', 'ABQ', 'TUS', 'BOI', 'ANC', 'HNL', 'OGG', 'SJU', 'CHS', 'SAV', 'RIC',
          'BUF', 'ALB', 'PVD', 'MEM', 'OKC', 'TUL', 'DSM', 'MSN', 'GRR', 'ELP', 'RNO', 'GEG', 'BZN', 'JAC', 'ASE']
SMALL = {'BZN', 'JAC', 'ASE', 'MSN', 'GRR', 'DSM', 'BOI', 'GEG', 'TUL', 'ALB', 'SAV', 'CHS'}
L1_CITIES = ['MDW', 'BWI', 'LAS', 'DEN', 'PHX', 'DAL', 'HOU', 'MCO', 'OAK', 'BNA', 'AUS', 'STL', 'SAN', 'TPA',
             'MCI', 'SMF', 'SJC', 'FLL', 'ATL', 'LAX']
U1_CITIES = ['FLL', 'LAS', 'MCO', 'DTW', 'EWR', 'ORD', 'ATL', 'PHL', 'DFW']
CONGESTION = {'EWR': 0.08, 'LGA': 0.07, 'JFK': 0.05, 'ORD': 0.05, 'SFO': 0.04, 'DEN': 0.03, 'ATL': 0.02,
              'BOS': 0.03, 'MIA': 0.03, 'DFW': 0.02}
HOUR_EFFECT = [-0.07, -0.03, 0.0, 0.03, 0.07, 0.09]
STORM = {'ATL', 'DEN', 'ORD', 'EWR', 'DFW', 'IAH', 'MCO', 'MIA', 'CLT', 'PHL', 'LGA', 'JFK'}
SNOW = {'ORD', 'MSP', 'DEN', 'DTW', 'BOS', 'EWR', 'LGA', 'JFK', 'SLC', 'BUF', 'CLE'}
LATE_TAIL_P = 1 - 0.5 * math.erfc(-(math.log(10) - math.log(25)) / math.sqrt(2))  # P(5+LN(ln25,1) >= 15)
TRUTH_HB_EDGES = [420, 600, 780, 960, 1140]


def _elapsed(a, b, dist):
    wind = -0.06 if b.lon > a.lon else 0.06
    return int(round((35 + dist * 0.125) * (1 + wind * min(1, dist / 1500))))


def _bad_day_p(code, month):
    p = 0.025
    if month in (6, 7, 8) and code in STORM:
        p = 0.06
    if month in (1, 2, 3, 12) and code in SNOW:
        p = 0.05
    return p


def build_schedule(airports, rng):
    """Return a list of scheduled flights: dict(carrier, fn, o, d, dep_utc_min_of_day, elapsed, days)."""
    flights = []
    fn_counter = {c: 100 for c in CARRIERS}

    def add(c, o, d, dep_local_min, days=frozenset(range(1, 8))):
        a, b = airports[o], airports[d]
        dist = great_circle_miles(a.lat, a.lon, b.lat, b.lon)
        fn_counter[c] += 1
        flights.append(dict(carrier=c, fn=str(fn_counter[c]), o=o, d=d, dep_local=int(dep_local_min) % 1440,
                            elapsed=_elapsed(a, b, dist), distance=round(dist), days=days))

    def local_offset(code, day=dt.date(2026, 6, 15)):
        z = ZoneInfo(airports[code].tz)
        return int(dt.datetime.combine(day, dt.time(12)).replace(tzinfo=z).utcoffset().total_seconds() // 60)

    for c, (_, hubs, *_r) in CARRIERS.items():
        if not hubs:
            continue
        banks = [390 + 105 * k for k in range(9)]  # hub-local bank times 06:30 .. 20:30
        spokes = [s for s in SPOKES if s not in hubs]
        rng.shuffle(spokes)
        for h in hubs:
            mine = spokes[: int(len(spokes) * 0.55)]
            rng.shuffle(spokes)
            for s in mine + [x for x in hubs if x != h]:
                big = s in hubs or airports[s].type == 'large_airport'
                freq = int(rng.integers(3, 7)) if big else int(rng.integers(1, 4))
                if s in ('HNL', 'OGG', 'ANC', 'SJU'):
                    freq = 1
                op = 'R1' if (s in SMALL and c in AFFILIATIONS['R1']) else c
                chosen = sorted(rng.choice(len(banks), size=min(freq, len(banks)), replace=False))
                for k in chosen:
                    b = banks[k]
                    days = frozenset(range(1, 7)) if (freq >= 3 and rng.random() < 0.2) else frozenset(range(1, 8))
                    # outbound: leave hub ~45-70 min after the bank's arrival wave
                    add(op, h, s, b + int(rng.integers(40, 70)), days)
                    # inbound: arrive at hub 5-35 min before the bank
                    arr_hub_local = b - int(rng.integers(5, 35))
                    a, bb = airports[s], airports[h]
                    el = _elapsed(a, bb, great_circle_miles(a.lat, a.lon, bb.lat, bb.lon))
                    dep_spoke_local = arr_hub_local - el - local_offset(h) + local_offset(s)
                    if 300 <= dep_spoke_local % 1440 <= 1380:
                        add(op, s, h, dep_spoke_local, days)
    big = [x for x in SPOKES if airports[x].type == 'large_airport']
    for c in ('N1', 'N2', 'N3'):
        pool = [x for x in big if x not in CARRIERS[c][1]]
        for _ in range(10):
            o, d = rng.choice(pool, 2, replace=False)
            if great_circle_miles(airports[o].lat, airports[o].lon, airports[d].lat, airports[d].lon) < 600:
                continue
            for _k in range(int(rng.integers(1, 3))):
                t = int(rng.integers(360, 1200))
                add(c, o, d, t)
                add(c, d, o, (t + 90) % 1440 if (t + 90) % 1440 > 330 else 420)
    for c, cities, pairs_per in (('L1', L1_CITIES, 7), ('U1', U1_CITIES, 4)):
        for o in cities:
            others = [x for x in cities if x != o]
            rng.shuffle(others)
            for d in others[:pairs_per]:
                for _ in range(int(rng.integers(1, 4)) if c == 'L1' else 1):
                    add(c, o, d, int(rng.integers(330, 1290)))
    return flights


def generate(airport_index, out_dir: str | Path, start='2026-03-01', end='2026-08-31', seed: int = 2026) -> dict:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(seed)
    airports = {c: airport_index.get(c) for c in set(SPOKES) | {h for v in CARRIERS.values() for h in v[1]}
                | set(L1_CITIES) | set(U1_CITIES)}
    missing = [c for c, a in airports.items() if a is None or not a.tz]
    if missing:
        raise ValueError(f'bundled airport file lacks {missing}')
    sched = build_schedule(airports, rng)
    d0, d1 = dt.date.fromisoformat(start), dt.date.fromisoformat(end)
    header = ['Year', 'Quarter', 'Month', 'DayofMonth', 'DayOfWeek', 'FlightDate', 'Reporting_Airline',
              'IATA_CODE_Reporting_Airline', 'Tail_Number', 'Flight_Number_Reporting_Airline', 'Origin',
              'OriginCityName', 'OriginState', 'Dest', 'DestCityName', 'DestState', 'CRSDepTime', 'DepTime',
              'DepDelay', 'DepDel15', 'CRSArrTime', 'ArrTime', 'ArrDelay', 'ArrDel15', 'Cancelled',
              'CancellationCode', 'Diverted', 'CRSElapsedTime', 'ActualElapsedTime', 'Distance']
    truth = io.StringIO()
    tw = csv.writer(truth)
    tw.writerow(['flight_date', 'op', 'op_fn', 'origin', 'dest', 'p_dep15', 'p_cancel', 'p_dep15_structural',
                 'p_cancel_structural'])
    t100: dict[tuple, list] = {}
    files, total = [], 0
    zones = {c: ZoneInfo(a.tz) for c, a in airports.items()}
    month_rows: dict[str, list] = {}
    day = d0
    while day <= d1:
        bad = {c: rng.random() < _bad_day_p(c, day.month) for c in airports}
        summer = 0.04 if day.month in (6, 7, 8) else 0.0
        for f in sched:
            if day.isoweekday() not in f['days']:
                continue
            c, o, d = f['carrier'], f['o'], f['d']
            _, _, pl, pc = CARRIERS[c]
            hb = sum(f['dep_local'] >= e for e in TRUTH_HB_EDGES)
            winter = 1.6 if day.month in (1, 2, 3, 12) else 1.0

            def late(b_o):
                return min(max(pl + CONGESTION.get(o, 0) + HOUR_EFFECT[hb] + summer + (0.35 if b_o else 0), 0.02), 0.9)

            def cxl(b_o, b_d):
                return min(pc * winter * (8 if b_o else 1) * (3 if b_d else 1), 0.6)
            p_late, p_cxl = late(bad[o]), cxl(bad[o], bad[d])
            p_dep15 = p_late * LATE_TAIL_P
            # structural truth: expectation over the unknown weather state, i.e. the best booking-time forecast
            qo, qd = _bad_day_p(o, day.month), _bad_day_p(d, day.month)
            s_dep15 = LATE_TAIL_P * ((1 - qo) * late(False) + qo * late(True))
            s_cxl = sum(po * pd * cxl(bo, bd) for bo, po in ((False, 1 - qo), (True, qo))
                        for bd, pd in ((False, 1 - qd), (True, qd)))
            tw.writerow([day.isoformat(), c, f['fn'], o, d, f'{p_dep15:.5f}', f'{p_cxl:.5f}', f'{s_dep15:.5f}',
                         f'{s_cxl:.5f}'])
            dep_local_dt = dt.datetime.combine(day, dt.time()) + dt.timedelta(minutes=f['dep_local'])
            dep_utc = dep_local_dt.replace(tzinfo=zones[o]).astimezone(dt.timezone.utc)
            arr_sched = (dep_utc + dt.timedelta(minutes=f['elapsed'])).astimezone(zones[d])
            cancelled = rng.random() < p_cxl
            diverted = (not cancelled) and rng.random() < (0.002 + (0.01 if bad[d] else 0))
            row = dict(dep='', dd='', d15='', arr='', ad='', a15='', ael='', code='')
            if cancelled:
                row['code'] = 'B' if bad[o] or bad[d] else 'A'
            elif not diverted:
                if rng.random() < p_late:
                    dd = 5 + rng.lognormal(math.log(25), 1.0)
                else:
                    dd = min(max(rng.normal(-3, 5), -20), 14)
                dd = round(dd)
                enr = rng.normal(-4 + 0.5 * CONGESTION.get(d, 0) * 100 / 2 + (15 if bad[d] else 0), 7)
                ad = round(dd + enr)
                dep_act = dep_local_dt + dt.timedelta(minutes=dd)
                arr_act = arr_sched + dt.timedelta(minutes=ad)
                row.update(dep=dep_act.strftime('%H%M'), dd=f'{dd:.2f}', d15='1.00' if dd >= 15 else '0.00',
                           arr=arr_act.strftime('%H%M'), ad=f'{ad:.2f}', a15='1.00' if ad >= 15 else '0.00',
                           ael=f'{f["elapsed"] + ad - dd:.2f}')
            ao, ad_ = airports[o], airports[d]
            month_rows.setdefault(f'{day:%Y_%m}', []).append([
                day.year, (day.month - 1) // 3 + 1, day.month, day.day, day.isoweekday(), day.isoformat(), c, c, '',
                f['fn'], o, f'{ao.city}, {ao.state}', ao.state, d, f'{ad_.city}, {ad_.state}', ad_.state,
                f'{f["dep_local"] // 60:02d}{f["dep_local"] % 60:02d}', row['dep'], row['dd'], row['d15'],
                arr_sched.strftime('%H%M'), row['arr'], row['ad'], row['a15'], '1.00' if cancelled else '0.00',
                row['code'], '1.00' if diverted else '0.00', f'{f["elapsed"]:.2f}', row['ael'], f'{f["distance"]:.2f}'])
            key = (day.year, day.month, c, o, d)
            seats = 76 if c == 'R1' else (186 if f['distance'] > 900 else 150)
            agg = t100.setdefault(key, [0, 0, 0, 0, f['distance']])
            agg[0] += 1
            if not cancelled:
                lf = min(0.98, max(0.5, rng.normal(0.84, 0.07)))
                agg[1] += 1
                agg[2] += seats
                agg[3] += round(seats * lf)
            total += 1
        day += dt.timedelta(days=1)
        if day.day == 1 or day > d1:
            for ym, rows in month_rows.items():
                path = out / f'SYNTHETIC_On_Time_Reporting_Carrier_{ym}.zip'
                buf = io.StringIO()
                w = csv.writer(buf, quoting=csv.QUOTE_MINIMAL)
                w.writerow(header)
                w.writerows(rows)
                with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as z:
                    z.writestr(f'SYNTHETIC_On_Time_Reporting_Carrier_{ym}.csv', buf.getvalue())
                files.append(path.name)
            month_rows.clear()
    with gzip.open(out / 'SYNTHETIC_truth.csv.gz', 'wt', encoding='utf-8', newline='') as g:
        g.write(truth.getvalue())
    with (out / 'SYNTHETIC_T100_Segment.csv').open('w', newline='') as fh:
        w = csv.writer(fh)
        w.writerow(['DEPARTURES_SCHEDULED', 'DEPARTURES_PERFORMED', 'SEATS', 'PASSENGERS', 'DISTANCE',
                    'UNIQUE_CARRIER', 'ORIGIN', 'DEST', 'YEAR', 'MONTH', 'CLASS'])
        for (y, m, c, o, d), (ds, dp, seats, pax, dist) in sorted(t100.items()):
            w.writerow([ds, dp, seats, pax, dist, c, o, d, y, m, 'F'])
    n_fares = _write_fares(out / 'SYNTHETIC_DB1C_Market.csv', airports, sched, rng)
    (out / 'README.txt').write_text(
        'SYNTHETIC DEMO DATA. Real airport locations; fictional carriers, schedules, delays, traffic and fares.\n'
        'Do not use these files to judge any real airline or route.\n')
    return dict(flights_generated=total, scheduled_patterns=len(sched), files=files, fare_records=n_fares,
                out=str(out))


def _write_fares(path, airports, sched, rng) -> int:
    nonstop = {}
    hubs_of = {c: set(v[1]) for c, v in CARRIERS.items()}
    for f in sched:
        mkt = 'N1' if f['carrier'] == 'R1' and (f['o'] in hubs_of['N1'] or f['d'] in hubs_of['N1']) else (
              'N2' if f['carrier'] == 'R1' else f['carrier'])
        nonstop.setdefault((f['o'], f['d']), set()).add(mkt)
    codes = sorted(airports)
    weight = {c: (10 if airports[c].type == 'large_airport' else 3) for c in codes}
    n = 0
    with path.open('w', newline='') as fh:
        w = csv.writer(fh)
        w.writerow(['ItinID', 'MktID', 'MktCoupons', 'Year', 'Quarter', 'Month', 'Origin', 'Dest', 'TkCarrier',
                    'OpCarrier', 'BulkFare', 'Passengers', 'MktFare', 'MktDistance', 'NonStopMiles'])
        for month in (3, 4, 5, 6, 7):
            for o in codes:
                for d in codes:
                    if o == d:
                        continue
                    a, b = airports[o], airports[d]
                    dist = great_circle_miles(a.lat, a.lon, b.lat, b.lon)
                    demand = 0.02 * weight[o] * weight[d] * (1000 / max(dist, 150)) ** 0.8
                    k = int(rng.poisson(demand))
                    if not k:
                        continue
                    direct = nonstop.get((o, d), set())
                    via = [c for c in ('N1', 'N2', 'N3') if not direct or c not in direct]
                    for _ in range(k):
                        if direct and rng.random() < 0.8:
                            c, coupons = sorted(direct)[int(rng.integers(len(direct)))], 1
                        else:
                            c, coupons = via[int(rng.integers(len(via)))] if via else sorted(direct)[0], 2
                        base = 60 + 0.11 * dist
                        mult = {'L1': 0.75, 'U1': 0.55}.get(c, 1.0) * (1.15 if coupons == 1 and c[0] == 'N' else 1.0)
                        mult *= 1.2 if o in hubs_of.get(c, ()) and len(direct) == 1 else 1.0
                        fare = round(base * mult * rng.lognormal(0, 0.45), 2)
                        n += 1
                        w.writerow([n, n, coupons, 2026, (month - 1) // 3 + 1, month, o, d, c, c, 0,
                                    int(rng.integers(1, 4)), fare, round(dist * (1.0 if coupons == 1 else 1.25)),
                                    round(dist)])
    return n
