"""Importers for BTS on-time, BTS T-100, DB1B/DB1C market fares and verified schedules.

Each importer owns the interpretation of its source format: it maps header names once,
validates values, and stores normalized facts with a source id. Consumers never re-read
raw files.
"""
from __future__ import annotations

import contextlib
import csv
import datetime as dt
import io
import math
import re
import zipfile
from collections import defaultdict
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np

from .db import register_source, sha256_file


def field_key(name: str) -> str:
    return re.sub(r'[^a-z0-9]', '', str(name).lower())


@contextlib.contextmanager
def open_table(path: str | Path, member: str | None = None):
    """Yield (header_keys, row_iterator) for a CSV or a ZIP holding one CSV, streaming."""
    path = Path(path)
    with contextlib.ExitStack() as stack:
        if zipfile.is_zipfile(path):
            z = stack.enter_context(zipfile.ZipFile(path))
            members = [m for m in z.infolist() if not m.is_dir() and m.filename.lower().endswith(('.csv', '.txt'))
                       and not m.filename.lower().endswith('readme.html')]
            if member:
                members = [m for m in members if Path(m.filename).name == member]
            csvs = [m for m in members if m.filename.lower().endswith('.csv')] or members
            if len(csvs) != 1:
                raise ValueError(f'{path.name}: expected one CSV member, found {[m.filename for m in csvs]}; '
                                 'pass --member to choose one')
            f = stack.enter_context(io.TextIOWrapper(z.open(csvs[0]), encoding='utf-8-sig', newline=''))
        else:
            f = stack.enter_context(path.open(encoding='utf-8-sig', newline=''))
        reader = csv.reader(f)
        header = [field_key(h) for h in next(reader, [])]
        yield header, reader


def _num(v: str) -> float | None:
    if v is None:
        return None
    v = v.strip()
    if v in ('', 'NA', 'M'):
        return None
    n = float(v)
    if not math.isfinite(n):
        raise ValueError(f'non-finite number {v!r}')
    return n


def _hhmm_minutes(v: str) -> int:
    n = _num(v)
    if n is None or n != int(n):
        raise ValueError(f'invalid HHMM {v!r}')
    n = int(n)
    if n == 2400:
        return 1440
    if not 0 <= n <= 2359 or n % 100 >= 60:
        raise ValueError(f'invalid HHMM {v!r}')
    return (n // 100) * 60 + n % 100


def _pick(index: dict[str, int], *names: str) -> int | None:
    for n in names:
        if n in index:
            return index[n]
    return None


ONTIME_FIELDS = {
    'date': ('flightdate',),
    'origin': ('origin',),
    'dest': ('dest',),
    'crsdep': ('crsdeptime',),
    'cancelled': ('cancelled',),
    'diverted': ('diverted',),
}


def import_ontime(con, path: str | Path, synthetic: bool = False, allow_partial: bool = False) -> dict:
    """Import a BTS reporting-carrier or marketing-carrier on-time CSV/ZIP.

    Every month present in the file replaces previously stored rows for that month, so a
    revised national file never double-counts. Partial (filtered) files are refused unless
    allow_partial is set, because they would replace a complete month with a subset.
    """
    path = Path(path)
    sha = sha256_file(path)
    with open_table(path) as (header, rows):
        ix = {h: i for i, h in enumerate(header)}
        missing = [k for k, names in ONTIME_FIELDS.items() if _pick(ix, *names) is None]
        if missing:
            raise ValueError(f'{path.name}: not a BTS on-time file; missing {missing}')
        marketing = _pick(ix, 'iatacodemarketingairline', 'marketingairlinenetwork') is not None
        if marketing:
            kind = 'ontime_marketing'
            c_mkt = _pick(ix, 'iatacodemarketingairline', 'marketingairlinenetwork')
            c_op = _pick(ix, 'iatacodeoperatingairline', 'operatingairline')
            c_fn = _pick(ix, 'flightnumbermarketingairline')
            c_opfn = _pick(ix, 'flightnumberoperatingairline')
        else:
            kind = 'ontime_reporting'
            c_mkt = c_op = _pick(ix, 'reportingairline', 'iatacodereportingairline', 'uniquecarrier')
            c_fn = c_opfn = _pick(ix, 'flightnumberreportingairline', 'flightnum')
        if None in (c_mkt, c_op, c_fn, c_opfn):
            raise ValueError(f'{path.name}: carrier or flight-number columns not found')
        c_date, c_o, c_d = ix['flightdate'], ix['origin'], ix['dest']
        c_crs, c_can, c_div = ix['crsdeptime'], ix['cancelled'], ix['diverted']
        c_el = _pick(ix, 'crselapsedtime')
        c_dist = _pick(ix, 'distance')
        c_dd = _pick(ix, 'depdelay')
        c_ad = _pick(ix, 'arrdelay')
        batch, periods, count, skipped = [], set(), 0, defaultdict(int)
        con.execute('DROP TABLE IF EXISTS temp.incoming')
        con.execute('CREATE TEMP TABLE incoming AS SELECT * FROM flights WHERE 0')
        ins = 'INSERT INTO incoming VALUES (' + ','.join('?' * 18) + ')'
        for r in rows:
            if not r:
                continue
            day = r[c_date][:10]
            d = dt.date.fromisoformat(day)
            cancelled, diverted = _num(r[c_can]), _num(r[c_div])
            if cancelled not in (0, 1) or diverted not in (0, 1):
                raise ValueError(f'{path.name}: invalid cancelled/diverted flag on {day}')
            try:
                dep = _hhmm_minutes(r[c_crs])
            except ValueError:
                skipped['invalid_scheduled_departure'] += 1
                continue
            elapsed = _num(r[c_el]) if c_el is not None else None
            if elapsed is None or elapsed <= 0:
                skipped['missing_scheduled_elapsed'] += 1
                continue
            period = day[:7]
            periods.add(period)
            batch.append((day, d.month, d.isoweekday(), r[c_mkt].strip(), r[c_op].strip(),
                          r[c_fn].strip(), r[c_opfn].strip(), r[c_o].strip(), r[c_d].strip(), dep,
                          int(elapsed), _num(r[c_dist]) if c_dist is not None else None,
                          _num(r[c_dd]) if c_dd is not None else None,
                          _num(r[c_ad]) if c_ad is not None else None,
                          int(cancelled), int(diverted), period, 0))
            count += 1
            if len(batch) >= 20000:
                con.executemany(ins, batch)
                batch.clear()
        if batch:
            con.executemany(ins, batch)
    if not count:
        con.execute('DROP TABLE temp.incoming')
        raise ValueError(f'{path.name}: no usable flight rows')
    if count < 100_000 and not synthetic and not allow_partial:
        con.execute('DROP TABLE temp.incoming')
        raise ValueError(f'{path.name}: only {count} rows; a national month has ~500k+. Refusing to replace '
                         'complete months with a filtered file (use --allow-partial in a separate --db).')
    with con:
        sid = register_source(con, sha, kind, path.name, ','.join(sorted(periods)), count, synthetic,
                              detail=dict(skipped=dict(skipped)))
        for p in periods:
            con.execute('DELETE FROM flights WHERE period=?', (p,))
        con.execute('UPDATE incoming SET source_id=?', (sid,))
        con.execute('INSERT INTO flights SELECT * FROM incoming')
    con.execute('DROP TABLE temp.incoming')
    return dict(kind=kind, rows=count, periods=sorted(periods), skipped=dict(skipped), file=path.name)


def import_t100(con, path: str | Path, synthetic: bool = False) -> dict:
    path = Path(path)
    sha = sha256_file(path)
    out = []
    with open_table(path) as (header, rows):
        ix = {h: i for i, h in enumerate(header)}
        cols = dict(year=_pick(ix, 'year'), month=_pick(ix, 'month'),
                    carrier=_pick(ix, 'uniquecarrier', 'carrier'), origin=_pick(ix, 'origin'),
                    dest=_pick(ix, 'dest'), depsch=_pick(ix, 'departuresscheduled'),
                    depperf=_pick(ix, 'departuresperformed'), seats=_pick(ix, 'seats'),
                    pax=_pick(ix, 'passengers'), dist=_pick(ix, 'distance'), cls=_pick(ix, 'class'))
        need = [k for k in ('year', 'month', 'carrier', 'origin', 'dest', 'seats', 'pax') if cols[k] is None]
        if need:
            raise ValueError(f'{path.name}: not a T-100 segment export; missing {need}')

        def g(r, k):
            return _num(r[cols[k]]) if cols[k] is not None else None
        for r in rows:
            if not r:
                continue
            out.append((int(g(r, 'year')), int(g(r, 'month')), r[cols['carrier']].strip(), r[cols['origin']].strip(),
                        r[cols['dest']].strip(), r[cols['cls']].strip() if cols['cls'] is not None else None,
                        g(r, 'depsch'), g(r, 'depperf'), g(r, 'seats'), g(r, 'pax'), g(r, 'dist')))
    periods = sorted({f'{y:04d}-{m:02d}' for y, m, *_ in out})
    with con:
        sid = register_source(con, sha, 't100', path.name, ','.join(periods), len(out), synthetic)
        for p in periods:
            y, m = map(int, p.split('-'))
            con.execute('DELETE FROM t100 WHERE year=? AND month=? AND source_id<>?', (y, m, sid))
        con.executemany('INSERT INTO t100 VALUES (?,?,?,?,?,?,?,?,?,?,?,?)', [x + (sid,) for x in out])
    return dict(kind='t100', rows=len(out), periods=periods, file=path.name)


FARE_ALIASES = {
    'origin': ('origin', 'originairport', 'mktorigin'),
    'dest': ('dest', 'destination', 'mktdest'),
    'carrier': ('tkcarrier', 'ticketingcarrier', 'tkcarriergroup', 'opcarrier', 'reportingcarrier'),
    'passengers': ('passengers', 'pax'),
    'fare': ('mktfare', 'marketfare', 'fare'),
    'coupons': ('mktcoupons', 'coupons', 'numcoupons'),
    'distance': ('nonstopmiles', 'mktdistance', 'distance'),
    'bulk': ('bulkfare',),
    'year': ('year',),
    'quarter': ('quarter',),
    'month': ('month',),
}
FARE_BINS = np.exp(np.linspace(math.log(25), math.log(5000), 61))


def _weighted_quantiles(hist: np.ndarray, qs=(0.25, 0.5, 0.75)) -> list[float]:
    cdf = np.cumsum(hist) / hist.sum()
    out = []
    for q in qs:
        i = int(np.searchsorted(cdf, q))
        lo, hi = FARE_BINS[i], FARE_BINS[min(i + 1, len(FARE_BINS) - 1)]
        prev = cdf[i - 1] if i else 0.0
        frac = (q - prev) / max(cdf[i] - prev, 1e-12)
        out.append(float(lo * (hi / lo) ** frac))
    return out


def import_fares(con, path: str | Path, survey: str, column_map: dict | None = None,
                 member: str | None = None, synthetic: bool = False) -> dict:
    """Aggregate a DB1B/DB1C Market table into directional O&D fare distributions.

    Drops fares below $25 (award/frequent-flyer/zero-fare records) and above $5,000 per
    direction, and bulk fares when flagged. Values are historical one-direction market
    fares per passenger, not quotes.
    """
    path = Path(path)
    sha = sha256_file(path)
    agg: dict[tuple, int] = {}
    hists, pax, fare_sum, dist_sum = [], [], [], []
    periods, dropped, kept = set(), defaultdict(int), 0
    with open_table(path, member) as (header, rows):
        ix = {h: i for i, h in enumerate(header)}
        col = {k: _pick(ix, *(([field_key(column_map[k])] if column_map and k in column_map else []) + list(v)))
               for k, v in FARE_ALIASES.items()}
        need = [k for k in ('origin', 'dest', 'carrier', 'passengers', 'fare', 'year') if col[k] is None]
        if need:
            raise ValueError(f'{path.name}: missing fare columns {need}. Found: {header[:60]}. '
                             'Pass --column-map \'{"fare": "ColumnName"}\' after reading the survey definitions.')
        for r in rows:
            if not r:
                continue
            fare, p = _num(r[col['fare']]), _num(r[col['passengers']])
            if p is None or p <= 0 or fare is None:
                dropped['missing'] += 1
                continue
            if col['bulk'] is not None and _num(r[col['bulk']]) == 1:
                dropped['bulk'] += 1
                continue
            per_pax = fare  # DB1B/DB1C MktFare is already per passenger; Passengers counts identical records
            if not 25 <= per_pax <= 5000:
                dropped['fare_out_of_range'] += 1
                continue
            year = int(_num(r[col['year']]))
            if col['month'] is not None and r[col['month']].strip():
                period = f'{year:04d}-{int(_num(r[col["month"]])):02d}'
            elif col['quarter'] is not None:
                period = f'{year:04d}Q{int(_num(r[col["quarter"]]))}'
            else:
                period = f'{year:04d}'
            periods.add(period)
            coupons = _num(r[col['coupons']]) if col['coupons'] is not None else None
            nonstop = -1 if coupons is None else int(coupons == 1)
            o, d, c = r[col['origin']].strip(), r[col['dest']].strip(), r[col['carrier']].strip()
            dist = _num(r[col['distance']]) if col['distance'] is not None else None
            b = min(max(int(np.searchsorted(FARE_BINS, per_pax)) - 1, 0), len(FARE_BINS) - 2)
            for key in ((period, o, d, c, nonstop), (period, o, d, '*', nonstop)):
                i = agg.get(key)
                if i is None:
                    i = agg[key] = len(hists)
                    hists.append(np.zeros(len(FARE_BINS) - 1))
                    pax.append(0.0)
                    fare_sum.append(0.0)
                    dist_sum.append(0.0)
                hists[i][b] += p
                pax[i] += p
                fare_sum[i] += per_pax * p
                dist_sum[i] += (dist or 0) * p
            kept += 1
    rows_out = []
    for (period, o, d, c, ns), i in agg.items():
        q25, q50, q75 = _weighted_quantiles(hists[i])
        rows_out.append((period, survey, o, d, c, ns, pax[i], q25, q50, q75, fare_sum[i] / pax[i],
                         dist_sum[i] / pax[i] if dist_sum[i] else None))
    with con:
        sid = register_source(con, sha, f'fares_{survey}', path.name, ','.join(sorted(periods)), kept, synthetic,
                              detail=dict(dropped=dict(dropped)))
        for p in periods:
            con.execute('DELETE FROM fares WHERE period=? AND survey=? AND source_id<>?', (p, survey, sid))
        con.executemany('INSERT INTO fares VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)', [x + (sid,) for x in rows_out])
    return dict(kind=f'fares_{survey}', records_kept=kept, markets=len(rows_out), periods=sorted(periods),
                dropped=dict(dropped), file=path.name)


def import_schedule(con, path: str | Path, provider: str, airports) -> dict:
    """Import a verified future schedule CSV.

    Columns: carrier, flight_number, origin, destination, departure_local (YYYY-MM-DDTHH:MM),
    and arrival_local or elapsed_minutes; operating_carrier optional.
    """
    path = Path(path)
    sha = sha256_file(path)
    out, unresolved = [], defaultdict(int)
    with open_table(path) as (header, rows):
        ix = {h: i for i, h in enumerate(header)}
        need = [k for k in ('carrier', 'flightnumber', 'origin', 'destination', 'departurelocal') if k not in ix]
        if need or ('arrivallocal' not in ix and 'elapsedminutes' not in ix):
            raise ValueError(f'{path.name}: schedule needs carrier, flight_number, origin, destination, '
                             'departure_local and arrival_local or elapsed_minutes')
        for r in rows:
            if not r:
                continue
            o, d = r[ix['origin']].strip().upper(), r[ix['destination']].strip().upper()
            ao, ad = airports.get(o), airports.get(d)
            if not ao or not ad or not ao.tz or not ad.tz:
                unresolved['unknown_airport_or_timezone'] += 1
                continue
            dep_l = dt.datetime.fromisoformat(r[ix['departurelocal']].strip())
            dep = dep_l.replace(tzinfo=ZoneInfo(ao.tz)).astimezone(dt.timezone.utc)
            if 'arrivallocal' in ix and r[ix['arrivallocal']].strip():
                arr = (dt.datetime.fromisoformat(r[ix['arrivallocal']].strip())
                       .replace(tzinfo=ZoneInfo(ad.tz)).astimezone(dt.timezone.utc))
            else:
                arr = dep + dt.timedelta(minutes=float(r[ix['elapsedminutes']]))
            if arr <= dep:
                raise ValueError(f'{path.name}: arrival not after departure for {r}')
            mkt = r[ix['carrier']].strip()
            op = r[ix['operatingcarrier']].strip() if 'operatingcarrier' in ix and r[ix['operatingcarrier']].strip() else mkt
            out.append((provider, mkt, op, r[ix['flightnumber']].strip(), o, d, dep.isoformat(), arr.isoformat(),
                        dep_l.date().isoformat()))
    dates = sorted({x[-1] for x in out})
    with con:
        sid = register_source(con, sha, 'schedule', path.name, f'{dates[0]}..{dates[-1]}' if dates else None, len(out))
        con.executemany('INSERT OR REPLACE INTO schedule VALUES (?,?,?,?,?,?,?,?,?,?)', [x + (sid,) for x in out])
    return dict(kind='schedule', rows=len(out), dates=[dates[0], dates[-1]] if dates else [], unresolved=dict(unresolved))
