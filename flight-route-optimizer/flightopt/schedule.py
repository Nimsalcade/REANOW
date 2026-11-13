"""Timetables: verified future schedules, or recurring patterns inferred from history.

A pattern timetable answers "which flights operated on this weekday in recent weeks", which
is a planning proxy and never proof that a future flight operates or has seats. Every leg
carries that basis so results can say so.
"""
from __future__ import annotations

import bisect
import datetime as dt
from collections import defaultdict
from dataclasses import dataclass, field
from zoneinfo import ZoneInfo

EPOCH = dt.datetime(1970, 1, 1, tzinfo=dt.timezone.utc)


def to_minutes(t: dt.datetime) -> int:
    return int((t - EPOCH).total_seconds() // 60)


def from_minutes(m: int, tz: str | None = None) -> dt.datetime:
    t = EPOCH + dt.timedelta(minutes=m)
    return t.astimezone(ZoneInfo(tz)) if tz else t


def local_minutes_to_utc(day: dt.date, minutes: int, tz: str) -> int:
    local = dt.datetime.combine(day, dt.time()) + dt.timedelta(minutes=minutes)
    return to_minutes(local.replace(tzinfo=ZoneInfo(tz)))


@dataclass(slots=True)
class Leg:
    id: int
    mkt: str
    op: str
    fn: str
    op_fn: str
    origin: str
    dest: str
    dep: int          # UTC minutes since epoch
    arr: int
    dep_local: int    # scheduled minutes after local midnight at origin
    local_date: str
    distance: float
    frequency: float  # share of reference days on which the pattern operated (1.0 if verified)
    verified: bool

    @property
    def elapsed(self) -> int:
        return self.arr - self.dep

    @property
    def label(self) -> str:
        return f'{self.mkt}{self.fn}' + (f' (operated by {self.op})' if self.op != self.mkt else '')


@dataclass
class Timetable:
    legs: list[Leg]
    basis: dict
    by_origin: dict[str, list[Leg]] = field(default_factory=dict)
    _deps: dict[str, list[int]] = field(default_factory=dict)

    def __post_init__(self):
        self.legs.sort(key=lambda l: (l.dep, l.id))
        groups = defaultdict(list)
        for l in self.legs:
            groups[l.origin].append(l)
        self.by_origin = dict(groups)
        self._deps = {k: [l.dep for l in v] for k, v in groups.items()}
        self.served = set(self.by_origin) | {l.dest for l in self.legs}

    def departures(self, airport: str, t0: int, t1: int) -> list[Leg]:
        deps = self._deps.get(airport)
        if not deps:
            return []
        i, j = bisect.bisect_left(deps, t0), bisect.bisect_right(deps, t1)
        return self.by_origin[airport][i:j]


def _reference_dates(con, day: dt.date, weeks: int) -> list[str]:
    last = con.execute('SELECT MAX(flight_date) FROM flights').fetchone()[0]
    if not last:
        return []
    cap = min(dt.date.fromisoformat(last), day - dt.timedelta(days=1))
    dow = day.isoweekday()
    have = {r[0] for r in con.execute(
        'SELECT DISTINCT flight_date FROM flights WHERE dow=? AND flight_date<=? ORDER BY flight_date DESC LIMIT ?',
        (dow, cap.isoformat(), weeks))}
    return sorted(have, reverse=True)


def pattern_legs(con, airports, day: dt.date, weeks: int, min_frequency: float, start_id: int):
    refs = _reference_dates(con, day, weeks)
    if not refs:
        return [], dict(kind='pattern', reference_dates=[])
    q = ('SELECT flight_date, mkt, op, fn, op_fn, origin, dest, dep_local, elapsed, distance FROM flights '
         f'WHERE flight_date IN ({",".join("?" * len(refs))})')
    seen: dict[tuple, dict[str, list]] = defaultdict(lambda: defaultdict(list))
    for r in con.execute(q, refs):
        seen[(r['mkt'], r['fn'], r['origin'], r['dest'])][r['flight_date']].append(tuple(r))
    legs, skipped_tz, id_ = [], 0, start_id
    recent = set(refs[:2])
    for key, by_date in seen.items():
        freq = len(by_date) / len(refs)
        if freq < min_frequency or not recent & by_date.keys():
            continue
        latest = max(by_date)
        ao, ad = airports.get(key[2]), airports.get(key[3])
        if not ao or not ad or not ao.tz:
            skipped_tz += len(by_date[latest])
            continue
        for occ in by_date[latest]:
            _, mkt, op, fn, op_fn, o, d, dep_local, elapsed, distance = occ
            dep = local_minutes_to_utc(day, dep_local, ao.tz)
            legs.append(Leg(id_, mkt, op, fn, op_fn, o, d, dep, dep + int(elapsed), dep_local, day.isoformat(),
                            distance or airports.distance(o, d), round(freq, 3), False))
            id_ += 1
    return legs, dict(kind='pattern', reference_dates=refs, weeks=weeks, min_frequency=min_frequency,
                      skipped_unknown_timezone=skipped_tz)


def verified_legs(con, airports, day: dt.date, start_id: int):
    rows = con.execute('SELECT * FROM schedule WHERE dep_date_local=?', (day.isoformat(),)).fetchall()
    legs = []
    for i, r in enumerate(rows):
        dep = to_minutes(dt.datetime.fromisoformat(r['dep_utc']))
        arr = to_minutes(dt.datetime.fromisoformat(r['arr_utc']))
        ao = airports.get(r['origin'])
        local = from_minutes(dep, ao.tz)
        legs.append(Leg(start_id + i, r['mkt'], r['op'], r['fn'], r['fn'], r['origin'], r['dest'], dep, arr,
                        local.hour * 60 + local.minute, day.isoformat(), airports.distance(r['origin'], r['dest']),
                        1.0, True))
    providers = sorted({r['provider'] for r in rows})
    return legs, dict(kind='verified', providers=providers, rows=len(rows))


def build_timetable(con, airports, start: dt.date, days: int = 3, weeks: int = 4,
                    min_frequency: float = 0.5, prefer_verified: bool = True) -> Timetable:
    """Timetable for [start, start+days). Verified schedule rows win for any day they cover."""
    legs, basis = [], {}
    for k in range(days):
        day = start + dt.timedelta(days=k)
        new, info = verified_legs(con, airports, day, len(legs)) if prefer_verified else ([], {})
        if not new:
            new, info = pattern_legs(con, airports, day, weeks, min_frequency, len(legs))
        legs.extend(new)
        basis[day.isoformat()] = info
    return Timetable(legs, basis)
