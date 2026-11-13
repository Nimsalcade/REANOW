"""Application service shared by the CLI and the web app."""
from __future__ import annotations

import bisect
import datetime as dt
import math
import threading
from collections import OrderedDict, defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np

from . import db as dbm
from .airports import METRO_GROUPS, AirportIndex
from .delay_model import DelayModel
from .fare_model import FareModel
from .risk import RiskParams, evaluate, global_rebook_delay
from .router import ConnectionPolicy, Itinerary, Router, default_affiliations
from .schedule import build_timetable, from_minutes, local_minutes_to_utc

PROFILES = {
    # risk_weight: $ weight on (P90 - expected) door time, as a multiple of value of time
    'balanced': dict(risk_weight=0.5, transfer_penalty=20.0, fare_weight=1.0),
    'fastest': dict(risk_weight=0.0, transfer_penalty=5.0, fare_weight=0.0),
    'reliable': dict(risk_weight=1.5, transfer_penalty=30.0, fare_weight=0.3),
    'cheapest': dict(risk_weight=0.2, transfer_penalty=5.0, fare_weight=4.0),
}
SAMPLE_RATE = {'db1c': 0.40, 'db1b': 0.10, 'synthetic': 0.01}  # synthetic records stand for a 1% sample


@dataclass
class SearchRequest:
    origin: str
    destination: str
    date: str
    depart_after: str = '00:00'
    depart_before: str = '23:59'
    arrive_by: str | None = None          # 'HH:MM' local at destination, '+1' suffix for next day
    max_stops: int | None = None          # None: 1, widened to 2 when few options exist
    profile: str = 'balanced'
    value_of_time: float = 50.0           # $ per hour of door-to-door time
    min_connection: int | None = None
    nearby_miles: float = 0.0
    allow_separate_tickets: bool = False
    carriers: list[str] = field(default_factory=list)
    avoid_carriers: list[str] = field(default_factory=list)
    results: int = 8
    samples: int = 2000


def _hhmm(s: str) -> int:
    h, m = s.split(':')
    h, m = int(h), int(m)
    if not (0 <= h <= 24 and 0 <= m < 60):
        raise ValueError(f'invalid time {s!r}')
    return h * 60 + m


def _fmt_dur(m: float) -> str:
    m = int(round(m))
    sign = '-' if m < 0 else ''
    m = abs(m)
    return f'{sign}{m // 60}h{m % 60:02d}' if m >= 60 else f'{sign}{m}m'


class Engine:
    def __init__(self, db_path: str | Path, model_dir: str | Path | None = None):
        self.db_path = Path(db_path)
        self.con = dbm.connect(self.db_path)
        self.lock = threading.RLock()
        self.airports = AirportIndex(self.con)
        self.model_dir = Path(model_dir) if model_dir else self.db_path.parent / 'models'
        self.model = DelayModel.load(self.model_dir) if (self.model_dir / 'delay_model.json').exists() else None
        self.fares = FareModel(self.con)
        self.summary = dbm.dataset_summary(self.con)
        kinds = {s['kind'] for s in self.summary['sources']}
        stored = dbm.get_meta(self.con, 'affiliations')
        if stored is not None:
            self.affiliations = stored
        elif 'ontime_marketing' in kinds:
            self.affiliations = {}
        else:
            self.affiliations = default_affiliations()
        self.carrier_names = dbm.get_meta(self.con, 'carrier_names', {})
        self._tt: OrderedDict = OrderedDict()

    # ---------- infrastructure ----------
    def require_model(self):
        if self.model is None:
            raise ValueError('No fitted model; run `flightopt build` after importing on-time data')
        return self.model

    def timetable(self, date: dt.date):
        key = date.isoformat()
        with self.lock:
            tt = self._tt.get(key)
            if tt is None:
                tt = build_timetable(self.con, self.airports, date, days=3)
                self._tt[key] = tt
                while len(self._tt) > 6:
                    self._tt.popitem(last=False)
            else:
                self._tt.move_to_end(key)
            return tt

    def limitations(self) -> list[str]:
        out = []
        if self.summary['synthetic']:
            out.append('SYNTHETIC DEMO DATA: carriers, schedules, delays and fares are fictional. '
                       'Import BTS files for real analysis.')
        out += ['Flights come from recurring historical patterns unless a verified schedule was imported; '
                'confirm operation and seats with the airline.',
                'Connection buffers are planning defaults, not airline minimum connection times.',
                'Risk assumes legs are independent (same-day weather correlation is not modeled) and that the first '
                'protected rebooking has a seat.',
                'Fares are historical typical one-way market fares, not quotes.']
        return out

    # ---------- search ----------
    def search(self, req: SearchRequest) -> dict:
        model = self.require_model()
        if req.profile not in PROFILES:
            raise ValueError(f'profile must be one of {sorted(PROFILES)}')
        day = dt.date.fromisoformat(req.date)
        tt = self.timetable(day)
        served = tt.served
        origins = self.airports.resolve(req.origin, req.nearby_miles, served)
        dests = self.airports.resolve(req.destination, req.nearby_miles, served)
        origins = [o for o in origins if o['code'] in served]
        dests = [d for d in dests if d['code'] in served]
        if not origins or not dests:
            return self._empty(req, tt, 'No scheduled service found at the requested airports in the timetable')
        if {o['code'] for o in origins} & {d['code'] for d in dests}:
            raise ValueError('Origin and destination overlap')
        policy = ConnectionPolicy(user_min_connection=req.min_connection, allow_separate_tickets=req.allow_separate_tickets,
                                  affiliations=self.affiliations)
        router = Router(tt, self.airports, policy)
        windows = {}
        for o in origins:
            tz = self.airports.get(o['code']).tz
            windows[o['code']] = (local_minutes_to_utc(day, _hhmm(req.depart_after), tz),
                                  local_minutes_to_utc(day, _hhmm(req.depart_before), tz))
        deadline_user = None
        if req.arrive_by:
            s = req.arrive_by.strip()
            plus = 1 if s.endswith('+1') else 0
            s = s.removesuffix('+1')
            # earliest-arriving destination zone gives the strictest reading; use the first destination
            tz = self.airports.get(dests[0]['code']).tz
            deadline_user = local_minutes_to_utc(day + dt.timedelta(days=plus), _hhmm(s), tz)
        deadline = deadline_user or max(w[1] for w in windows.values()) + 20 * 60
        ocodes = {o['code']: o['access_minutes'] for o in origins}
        dcodes = {d['code']: d['access_minutes'] for d in dests}
        max_legs = (req.max_stops + 1) if req.max_stops is not None else 2
        cands = router.candidates(ocodes, dcodes, windows, deadline, max_legs)
        widened = False
        if req.max_stops is None and len(cands) < 6:
            cands = router.candidates(ocodes, dcodes, windows, deadline, 3)
            widened = True
        if req.carriers:
            allow = {c.upper() for c in req.carriers}
            cands = [c for c in cands if all(l.mkt in allow or l.op in allow for l in c.legs)]
        if req.avoid_carriers:
            avoid = {c.upper() for c in req.avoid_carriers}
            cands = [c for c in cands if not any(l.mkt in avoid or l.op in avoid for l in c.legs)]
        if not cands:
            return self._empty(req, tt, 'No itinerary satisfies the time window, connection rules and stop limit')
        cands = self._prefilter(cands, keep=150)
        month = day.month
        params = RiskParams(samples=req.samples)
        rebook = global_rebook_delay(model, month)
        dset = frozenset(dcodes)
        prof = PROFILES[req.profile]
        vot = req.value_of_time / 60
        rows = []
        for it in cands:
            dest_tz = self.airports.get(it.legs[-1].dest).tz
            risk = evaluate(it, router, model, dset, month, params, rebook, dest_tz, deadline_user)
            fare = self._fare(it)
            door_sched = it.origin_access + it.elapsed + it.dest_access
            door_exp = it.origin_access + (risk['expected_arrival'] - it.dep) + it.dest_access
            door_p90 = it.origin_access + (risk['p90_arrival'] - it.dep) + it.dest_access
            fare_mid = fare['p50'] if fare else None
            cost = (prof['fare_weight'] * (fare_mid or 0) + vot * door_exp
                    + vot * prof['risk_weight'] * (door_p90 - door_exp) + prof['transfer_penalty'] * it.stops)
            if req.profile == 'fastest':
                cost = door_exp + 1e-3 * door_sched
            rows.append(dict(it=it, risk=risk, fare=fare, door_sched=door_sched, door_exp=door_exp,
                             door_p90=door_p90, cost=cost))
        rows.sort(key=lambda r: r['cost'])
        self._mark_pareto(rows)
        chosen = rows[:req.results]
        extra = [r for r in rows[req.results:] if r['pareto']][:3]
        out = [self._present(r, i + 1, rows) for i, r in enumerate(chosen + extra)]
        return dict(
            request=asdict(req), profile=req.profile, results=out, considered=len(rows),
            widened_to_two_stops=widened, insights=self._insights(rows, chosen),
            origins=origins, destinations=dests, timetable_basis=tt.basis.get(day.isoformat()),
            model=self._model_brief(month), limitations=self.limitations(), synthetic=self.summary['synthetic'])

    def _empty(self, req, tt, why):
        return dict(request=asdict(req), results=[], considered=0, insights=[why],
                    timetable_basis=tt.basis.get(req.date), limitations=self.limitations(),
                    synthetic=self.summary['synthetic'])

    @staticmethod
    def _prefilter(cands: list[Itinerary], keep: int) -> list[Itinerary]:
        def score(it):
            tight = sum(max(0, 55 - lay) for lay, ct in zip(it.layovers(), it.connection_types) if ct != 'through')
            return it.elapsed + it.origin_access + it.dest_access + 30 * it.stops + 0.8 * tight
        cands = sorted(cands, key=score)
        nonstops = [c for c in cands if c.stops == 0]
        rest = [c for c in cands if c.stops > 0][: max(0, keep - len(nonstops))]
        return nonstops + rest

    def _fare(self, it: Itinerary) -> dict | None:
        if not self.fares.available:
            return None
        tickets, cur = [], [it.legs[0]]
        for leg, ct in zip(it.legs[1:], it.connection_types):
            if ct == 'separate_tickets':
                tickets.append(cur)
                cur = [leg]
            else:
                cur.append(leg)
        tickets.append(cur)
        total = dict(p25=0.0, p50=0.0, p75=0.0)
        bases = []
        for t in tickets:
            o, d = t[0].origin, t[-1].dest
            e = self.fares.estimate(o, d, t[0].mkt, len(t) == 1, self.airports.distance(o, d))
            if e is None:
                return None
            for k in total:
                total[k] += e[k]
            bases.append(e['basis'])
        return dict(total, basis='; '.join(bases), tickets=len(tickets), periods=self.fares.periods)

    @staticmethod
    def _mark_pareto(rows):
        pts = [(r['door_exp'], r['fare']['p50'] if r['fare'] else math.inf, r['door_p90']) for r in rows]
        for i, r in enumerate(rows):
            a = pts[i]
            r['pareto'] = not any(all(b[k] <= a[k] for k in range(3)) and any(b[k] < a[k] for k in range(3))
                                  for j, b in enumerate(pts) if j != i)

    def _local(self, minutes: float, code: str) -> str:
        return from_minutes(int(round(minutes)), self.airports.get(code).tz).isoformat(timespec='minutes')

    def _present(self, r, rank, rows) -> dict:
        it, risk = r['it'], r['risk']
        legs = []
        for i, l in enumerate(it.legs):
            lr = risk['legs'][i]
            legs.append(dict(
                flight=f'{l.mkt}{l.fn}', carrier=l.mkt, carrier_name=self.carrier_names.get(l.mkt),
                operating_carrier=l.op, origin=l.origin, destination=l.dest,
                depart_local=self._local(l.dep, l.origin), arrive_local=self._local(l.arr, l.dest),
                duration_min=l.elapsed, distance_mi=round(l.distance),
                basis='verified schedule' if l.verified else f'pattern: operated {l.frequency:.0%} of reference days',
                p_cancel=round(lr['p_cancel'], 4), p_arrive_15plus_late=round(lr['p_arr15'], 3),
                mean_arrival_delay_min=round(lr['mean_arr_delay'], 1),
                evidence=f'{lr["evidence_level"]}, {lr["evidence_n"]:.0f} seasonally weighted operations'))
        conns = []
        for i, (a, b) in enumerate(zip(it.legs, it.legs[1:])):
            conns.append(dict(airport=a.dest, minutes=b.dep - a.arr, type=it.connection_types[i],
                              p_miss=round(risk['connection_miss'][i], 4)))
        o, d = it.legs[0].origin, it.legs[-1].dest
        notes = self._notes(r, conns, rows)
        return dict(
            rank=rank, pareto_optimal=r['pareto'], origin=o, destination=d, stops=it.stops,
            depart_local=self._local(it.dep, o), arrive_local=self._local(it.arr, d),
            scheduled_minutes=it.elapsed, origin_access_min=it.origin_access, dest_access_min=it.dest_access,
            door_to_door_scheduled_min=r['door_sched'], door_to_door_expected_min=round(r['door_exp']),
            door_to_door_p90_min=round(r['door_p90']),
            expected_arrival_local=self._local(risk['expected_arrival'], d),
            p90_arrival_local=self._local(risk['p90_arrival'], d),
            p_on_time=round(risk['p_on_time'], 3), p_misconnect=round(risk['p_misconnect'], 4),
            p_cancel=round(risk['p_cancel'], 4), p_disrupted=round(risk['p_disrupted'], 4),
            p_stranded=round(risk['p_stranded'], 4),
            p_after_midnight=None if risk['p_after_midnight'] is None else round(risk['p_after_midnight'], 4),
            p_by_deadline=None if risk['p_by_deadline'] is None else round(risk['p_by_deadline'], 3),
            expected_delay_min=round(risk['expected_delay'], 1), p50_delay_min=round(risk['p50_delay'], 1),
            p80_delay_min=round(risk['p80_delay'], 1), p90_delay_min=round(risk['p90_delay'], 1),
            fare=None if not r['fare'] else {k: (round(v) if isinstance(v, float) else v) for k, v in r['fare'].items()},
            score=round(r['cost'], 2), legs=legs, connections=conns, notes=notes)

    def _notes(self, r, conns, rows) -> list[str]:
        it, risk = r['it'], r['risk']
        notes = []
        fastest = min(rows, key=lambda x: x['door_sched'])
        best_exp = min(rows, key=lambda x: x['door_exp'])
        if r is fastest:
            notes.append('Fastest on the schedule.')
        if r is best_exp:
            notes.append('Earliest expected arrival once delays, misconnects and rebooking are priced in.')
        if r is not fastest and r['door_sched'] > fastest['door_sched'] and r['door_exp'] <= fastest['door_exp']:
            notes.append(f'Scheduled {_fmt_dur(r["door_sched"] - fastest["door_sched"])} longer than the fastest option, '
                         f'but expected {_fmt_dur(fastest["door_exp"] - r["door_exp"])} sooner on average.')
        for c in conns:
            if c['type'] == 'through':
                notes.append(f'Same flight continues at {c["airport"]}; no aircraft change expected.')
            elif c['p_miss'] >= 0.08:
                notes.append(f'Tight {c["minutes"]}-min connection at {c["airport"]}: {c["p_miss"]:.0%} chance of missing it.')
            if c['type'] == 'affiliate_possible':
                notes.append(f'Carrier change at {c["airport"]} may be a partner connection; verify it is on one ticket.')
            if c['type'] == 'separate_tickets':
                notes.append(f'Separate tickets at {c["airport"]}: a missed connection is not protected.')
        if risk['p_after_midnight'] and risk['p_after_midnight'] >= 0.05:
            notes.append(f'{risk["p_after_midnight"]:.0%} chance of arriving after midnight local time.')
        if risk['p_stranded'] >= 0.01:
            notes.append(f'{risk["p_stranded"]:.0%} chance no protected same-carrier option exists within the timetable horizon.')
        if it.origin_access or it.dest_access:
            notes.append(f'Includes estimated ground access ({it.origin_access + it.dest_access} min) for an alternate airport.')
        return notes

    def _insights(self, rows, chosen) -> list[str]:
        out = []
        if not rows:
            return out
        fastest = min(rows, key=lambda x: x['door_sched'])
        gap = fastest['door_exp'] - fastest['door_sched']
        if fastest['it'].stops and fastest['risk']['p_misconnect'] >= 0.08:
            out.append(f'The fastest scheduled itinerary carries a {fastest["risk"]["p_misconnect"]:.0%} misconnect risk; '
                       f'its expected trip is {_fmt_dur(gap)} longer than scheduled.')
        nonstop = [r for r in rows if r['it'].stops == 0]
        if nonstop and chosen and chosen[0]['it'].stops > 0:
            ns = min(nonstop, key=lambda x: x['cost'])
            out.append(f'A nonstop exists ({ns["it"].legs[0].mkt}{ns["it"].legs[0].fn}) but ranks lower for this profile.')
        if not nonstop:
            out.append('No nonstop service appears in the timetable for this market.')
        p = [r['risk']['p_on_time'] for r in rows]
        out.append(f'On-time probability across {len(rows)} evaluated itineraries ranges {min(p):.0%}–{max(p):.0%}.')
        return out

    def _model_brief(self, month: int) -> dict:
        m = self.model.meta
        present = m.get('months_present', [])
        near = [x for x in present if min(abs(x - month), 12 - abs(x - month)) <= 1]
        return dict(training_rows=m.get('rows'), months_present=present,
                    first_day=str(dt.date(1970, 1, 1) + dt.timedelta(days=m.get('first_day', 0))),
                    last_day=str(dt.date(1970, 1, 1) + dt.timedelta(days=m.get('last_day', 0))),
                    seasonal_coverage='travel month within ±1 month of training data' if near else
                    'no training data within ±1 month of the travel month; seasonal effects are not represented',
                    tuning=m.get('tuning'))

    # ---------- route and network analytics ----------
    def route_report(self, origin: str, dest: str, date: str | None = None) -> dict:
        o, d = origin.upper(), dest.upper()
        if not self.airports.get(o) or not self.airports.get(d):
            raise ValueError('Unknown airport code')
        q = '''SELECT mkt, op, COUNT(*) n, COUNT(DISTINCT flight_date) days, AVG(cancelled) cxl,
               AVG(CASE WHEN cancelled=0 AND diverted=0 AND arr_delay IS NOT NULL THEN (arr_delay>=15) END) late15,
               AVG(CASE WHEN cancelled=0 AND diverted=0 THEN arr_delay END) mean_delay,
               SUM(CASE WHEN cancelled=0 AND diverted=0 AND arr_delay IS NOT NULL THEN 1 ELSE 0 END) n_delay,
               AVG(elapsed) elapsed, MIN(flight_date) first, MAX(flight_date) last
               FROM flights WHERE origin=? AND dest=? GROUP BY mkt, op ORDER BY n DESC'''
        carriers = [dict(r) for r in self.con.execute(q, (o, d))]
        span = self.con.execute('SELECT COUNT(DISTINCT flight_date) FROM flights').fetchone()[0] or 1
        for c in carriers:
            c['flights_per_day'] = round(c['n'] / span, 2)
            for k in ('cxl', 'late15', 'mean_delay', 'elapsed'):
                c[k] = None if c[k] is None else round(c[k], 3 if k in ('cxl', 'late15') else 1)
        by_hour = [dict(r) for r in self.con.execute(
            '''SELECT dep_local/60 hour, COUNT(*) n, AVG(cancelled) cxl,
               AVG(CASE WHEN cancelled=0 AND diverted=0 AND arr_delay IS NOT NULL THEN (arr_delay>=15) END) late15
               FROM flights WHERE origin=? AND dest=? GROUP BY hour ORDER BY hour''', (o, d))]
        t100 = [dict(r) for r in self.con.execute(
            '''SELECT year, month, carrier, SUM(departures_performed) departures, SUM(seats) seats, SUM(passengers) passengers,
               CASE WHEN SUM(seats)>0 THEN ROUND(SUM(passengers)*1.0/SUM(seats),3) END load_factor
               FROM t100 WHERE origin=? AND dest=? AND (service_class IS NULL OR service_class='F')
               GROUP BY year, month, carrier ORDER BY year DESC, month DESC, passengers DESC LIMIT 24''', (o, d))]
        fares = [dict(r) for r in self.con.execute(
            '''SELECT period, carrier, nonstop, ROUND(passengers) passengers, ROUND(fare_p25) p25, ROUND(fare_p50) p50,
               ROUND(fare_p75) p75 FROM fares WHERE origin=? AND dest=? ORDER BY period DESC, passengers DESC LIMIT 30''',
            (o, d))]
        timetable = []
        if date:
            tt = self.timetable(dt.date.fromisoformat(date))
            day0 = tt.departures(o, -(1 << 62), 1 << 62)
            for l in day0:
                if l.dest == d and l.local_date == date:
                    timetable.append(dict(flight=f'{l.mkt}{l.fn}', operating=l.op, depart=self._local(l.dep, o),
                                          arrive=self._local(l.arr, d), minutes=l.elapsed, frequency=l.frequency,
                                          verified=l.verified))
        return dict(origin=o, destination=d, distance_mi=round(self.airports.distance(o, d)),
                    history_days=span, carriers=carriers, by_departure_hour=by_hour, t100=t100, fares=fares,
                    timetable=timetable, synthetic=self.summary['synthetic'],
                    note='Historical observations; rates are over operated (delay) or scheduled (cancellation) flights.')

    def hubs(self, date: str, limit: int = 20, window=(40, 180)) -> dict:
        tt = self.timetable(dt.date.fromisoformat(date))
        policy = ConnectionPolicy(affiliations=self.affiliations)
        day_legs = [l for l in tt.legs if l.local_date == date]
        arrivals, deps = defaultdict(list), defaultdict(list)
        for l in day_legs:
            arrivals[l.dest].append(l)
            deps[l.origin].append(l)
        out = []
        for ap, dl in deps.items():
            arr = sorted(arrivals.get(ap, []), key=lambda l: l.arr)
            dl = sorted(dl, key=lambda l: l.dep)
            times = [l.dep for l in dl]
            pairs = set()
            for a in arr:
                i = bisect.bisect_left(times, a.arr + window[0])
                j = bisect.bisect_right(times, a.arr + window[1])
                for b in dl[i:j]:
                    if b.dest != a.origin and policy.connection_type(a, b):
                        pairs.add((a.origin, b.dest))
            nonstop = {l.dest for l in dl}
            out.append(dict(airport=ap, name=self.airports.get(ap).name if self.airports.get(ap) else ap,
                            departures=len(dl), nonstop_destinations=len(nonstop),
                            connecting_markets=len(pairs),
                            carriers=sorted({l.mkt for l in dl})))
        out.sort(key=lambda x: (-x['connecting_markets'], -x['departures']))
        return dict(date=date, connection_window_min=list(window), hubs=out[:limit], synthetic=self.summary['synthetic'],
                    method='Connecting markets = distinct origin-destination pairs joinable through the airport on '
                           'one brand within the window; a simplified connectivity index.')

    def opportunities(self, date: str, limit: int = 25, min_miles: float = 250, max_miles: float = 2600) -> dict:
        """Rank unserved nonstop markets by passenger-hours a nonstop would save per day."""
        if not self.fares.available:
            return dict(opportunities=[], note='Requires DB1C/DB1B market data (passenger demand); import fares first.')
        tt = self.timetable(dt.date.fromisoformat(date))
        nonstop = {(l.origin, l.dest) for l in tt.legs}
        legs = [l for l in tt.legs if l.local_date == date]
        if len(legs) < 10:
            return dict(opportunities=[], note='Timetable too small for this date')
        x = np.array([l.distance for l in legs])
        y = np.array([l.elapsed for l in legs])
        b, a = np.polyfit(x, y, 1)
        periods = self.fares.periods
        survey_rows = self.con.execute(
            f'SELECT survey, origin, dest, SUM(passengers) pax FROM fares WHERE carrier="*" AND period IN '
            f'({",".join("?" * len(periods))}) GROUP BY survey, origin, dest', periods).fetchall()
        days = 0
        for p in periods:
            if 'Q' in p:
                days += 91
            else:
                y_, m_ = map(int, p.split('-'))
                days += ((dt.date(y_ + (m_ == 12), m_ % 12 + 1, 1)) - dt.date(y_, m_, 1)).days
        markets = []
        for r in survey_rows:
            o, d = r['origin'], r['dest']
            if (o, d) in nonstop or not self.airports.get(o) or not self.airports.get(d):
                continue
            dist = self.airports.distance(o, d)
            if not min_miles <= dist <= max_miles:
                continue
            daily = r['pax'] / SAMPLE_RATE.get(r['survey'], 1.0) / max(days, 1)
            markets.append((daily, o, d, dist))
        markets.sort(reverse=True)
        policy = ConnectionPolicy(affiliations=self.affiliations)
        router = Router(tt, self.airports, policy)
        out = []
        for daily, o, d, dist in markets[: limit * 6]:
            ao = self.airports.get(o)
            if o not in tt.served or d not in tt.served:
                continue
            w = (local_minutes_to_utc(dt.date.fromisoformat(date), 0, ao.tz),
                 local_minutes_to_utc(dt.date.fromisoformat(date), 1439, ao.tz))
            cands = router.candidates({o: 0}, {d: 0}, {o: w}, w[1] + 20 * 60, 2)
            if not cands:
                continue
            best = min(cands, key=lambda c: c.elapsed)
            ns_est = a + b * dist
            saved = best.elapsed - ns_est
            if saved <= 0:
                continue
            out.append(dict(origin=o, destination=d, distance_mi=round(dist), est_daily_passengers=round(daily, 1),
                            best_connection_min=best.elapsed, via=best.legs[0].dest,
                            est_nonstop_min=round(ns_est), minutes_saved=round(saved),
                            passenger_hours_saved_per_day=round(daily * saved / 60, 1)))
        out.sort(key=lambda r: -r['passenger_hours_saved_per_day'])
        return dict(date=date, opportunities=out[:limit], block_time_model=f'{a:.0f} + {b:.3f} x miles',
                    synthetic=self.summary['synthetic'],
                    method='Demand from DB1C/DB1B market passengers scaled by survey sample rate; markets with no '
                           'nonstop in the timetable; time saved = fastest current connection - modeled nonstop block time. '
                           'Not a profitability forecast.')

    def status(self) -> dict:
        s = dict(self.summary)
        s['model'] = None if self.model is None else self.model.meta
        s['fares_available'] = self.fares.available
        s['affiliation_mode'] = 'marketing data' if not self.affiliations else 'regional affiliation table'
        s['metro_groups'] = {k: v[1] for k, v in METRO_GROUPS.items()}
        s['limitations'] = self.limitations()
        return s
