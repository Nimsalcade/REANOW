#!/usr/bin/env python3
"""Local flight-data collection and basic imports. Python 3.11+, standard library only."""
from __future__ import annotations

import argparse
import contextlib
import csv
import datetime as dt
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

ROOT = Path(__file__).resolve().parent
AS_OF = '2026-11-13'
US_COUNTRIES = {'US', 'PR', 'VI', 'GU', 'MP', 'AS', 'UM'}


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=path.name+'.', suffix='.part', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(value, f, indent=2)
            f.write('\n')
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def jobs_for(path, groups, ids):
    jobs = read_json(path)['jobs']
    if len({j['id'] for j in jobs}) != len(jobs):
        raise ValueError('Duplicate job IDs in plan')
    known_groups = {j['group'] for j in jobs}
    known_ids = {j['id'] for j in jobs}
    if set(groups) - known_groups - {'all'}:
        raise ValueError('Unknown group: '+', '.join(sorted(set(groups)-known_groups-{'all'})))
    if set(ids)-known_ids:
        raise ValueError('Unknown job ID: '+', '.join(sorted(set(ids)-known_ids)))
    if ids:
        return [j for j in jobs if j['id'] in ids]
    return [j for j in jobs if 'all' in groups or j['group'] in groups]


class HTTPSRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urllib.parse.urlsplit(newurl).scheme != 'https':
            raise ValueError('HTTPS redirect required')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def valid_download(path, kind, content_type):
    with path.open('rb') as f:
        head = f.read(4096)
    low = head.lstrip().lower()
    if not head or 'text/html' in content_type.lower() or low.startswith((b'<!doctype html', b'<html')):
        raise ValueError('Empty or HTML response; no data saved')
    if kind == 'zip':
        if not zipfile.is_zipfile(path):
            raise ValueError('Expected ZIP container')
        with zipfile.ZipFile(path) as z:
            if not z.infolist():
                raise ValueError('Empty ZIP container')
    elif kind.startswith('gzip_'):
        if not head.startswith(b'\x1f\x8b'):
            raise ValueError('Expected gzip data')
    elif kind == 'json':
        with path.open(encoding='utf-8-sig') as f:
            json.load(f)
    elif kind == 'xml':
        if not low.startswith(b'<'):
            raise ValueError('Expected XML data')
    elif kind == 'csv':
        first = head.decode('utf-8-sig').splitlines()[0]
        if ',' not in first:
            raise ValueError('Expected CSV header')


def download(job, out, max_bytes, user_agent, opener=None):
    """Save successful bytes by content hash; never replace an earlier good snapshot."""
    out = Path(out)
    url = job['url']
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('A public HTTPS URL without embedded credentials is required')
    if max_bytes <= 0:
        raise ValueError('Byte limit must be positive')
    (out/'raw').mkdir(parents=True, exist_ok=True)
    (out/'tmp').mkdir(parents=True, exist_ok=True)
    client = opener or urllib.request.build_opener(HTTPSRedirect()).open
    for attempt in range(3):
        temporary = None
        try:
            req = urllib.request.Request(url, headers={'User-Agent':user_agent,'Accept':'*/*'})
            with client(req, timeout=60) as response:
                if response.status != 200:
                    raise ValueError(f'Expected HTTP 200, received {response.status}')
                length = response.headers.get('Content-Length')
                if length and int(length) > max_bytes:
                    raise ValueError('File exceeds byte limit; raise --max-file-mb if intended')
                fd, tempname = tempfile.mkstemp(dir=out/'tmp', suffix='.part')
                temporary = Path(tempname)
                sha = hashlib.sha256()
                count = 0
                with os.fdopen(fd, 'wb') as f:
                    while chunk := response.read(1024*1024):
                        count += len(chunk)
                        if count > max_bytes:
                            raise ValueError('Streaming byte limit exceeded')
                        f.write(chunk)
                        sha.update(chunk)
                if length and count != int(length):
                    raise ValueError('Incomplete download; byte count differs from Content-Length')
                valid_download(temporary, job['format'], response.headers.get('Content-Type',''))
                digest = sha.hexdigest()
                basename = Path(job['filename']).name
                final = out/'raw'/(digest+'-'+basename)
                os.replace(temporary, final)
                temporary = None
                return dict(job_id=job['id'],source_id=job['source_id'],url=url,
                    data_period=job.get('data_period'), retrieved_at_utc=now(), status='downloaded',
                    path=str(final.relative_to(out)), bytes=count, sha256=digest,
                    http_date=response.headers.get('Date'),
                    last_modified=response.headers.get('Last-Modified'),etag=response.headers.get('ETag'))
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and attempt < 2:
                retry_after = e.headers.get('Retry-After') if e.headers else None
                try:
                    delay = float(retry_after) if retry_after else 2**(attempt+1)
                except ValueError:
                    from email.utils import parsedate_to_datetime
                    try:
                        delay = max(0, (parsedate_to_datetime(retry_after)-dt.datetime.now(dt.timezone.utc)).total_seconds())
                    except (ValueError, TypeError):
                        delay = 2**(attempt+1)
                if delay > 60:
                    raise RuntimeError(f'Server requests a later retry ({delay:.0f}s); try again later') from e
                time.sleep(max(0,delay))
                continue
            raise
        finally:
            if temporary:
                temporary.unlink(missing_ok=True)
    raise RuntimeError('Download attempts exhausted')


def fetch(args):
    jobs = jobs_for(args.jobs,args.group,args.id)
    out = Path(args.out)
    out.mkdir(parents=True,exist_ok=True)
    indexpath = out/'current.json'
    index = read_json(indexpath) if indexpath.exists() else {}
    failed = 0
    total = 0
    for j in jobs:
        remaining = int(args.max_total_mb*1024*1024)-total
        try:
            if remaining <= 0:
                raise ValueError('Total run byte limit reached')
            record = download(j,out,min(int(args.max_file_mb*1024*1024),remaining),args.user_agent)
            total += record['bytes']
            index[j['id']] = record
            atomic_json(indexpath,index)
        except (OSError, ValueError, RuntimeError, zipfile.BadZipFile) as e:
            failed += 1
            record = dict(job_id=j['id'],source_id=j['source_id'],url=j['url'],
                          attempted_at_utc=now(),status='failed',error=str(e))
        with (out/'downloads.jsonl').open('a',encoding='utf-8') as f:
            f.write(json.dumps(record)+'\n')
        print(json.dumps(record),flush=True)
        # One request at a time; no aggressive retries or source-site crawling.
        if j is not jobs[-1]:
            time.sleep(0.5)
    print(json.dumps({'attempted':len(jobs),'failed':failed,'bytes_saved':total}))
    return 1 if failed else 0


def sha_file(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        while block := f.read(1024*1024):
            h.update(block)
    return h.hexdigest()


def connection(path):
    Path(path).parent.mkdir(parents=True,exist_ok=True)
    con = sqlite3.connect(path)
    con.executescript((ROOT/'schema.sql').read_text())
    return con


@contextlib.contextmanager
def csv_rows(path):
    """Read a CSV or a single-CSV ZIP as a stream without extracting paths."""
    path = Path(path)
    with contextlib.ExitStack() as stack:
        if zipfile.is_zipfile(path):
            z = stack.enter_context(zipfile.ZipFile(path))
            members = [x for x in z.infolist() if x.filename.lower().endswith('.csv') and not x.is_dir()]
            if len(members) != 1:
                raise ValueError('Import needs one CSV; choose a specific CSV for a multi-table ZIP')
            if members[0].file_size > 16*1024**3:
                raise ValueError('CSV exceeds the 16 GiB import limit; partition it first')
            raw = stack.enter_context(z.open(members[0]))
            f = stack.enter_context(io.TextIOWrapper(raw,encoding='utf-8-sig',newline=''))
        else:
            f = stack.enter_context(path.open(encoding='utf-8-sig',newline=''))
        reader = csv.DictReader(f)
        reader.fieldnames = [str(x).strip() for x in (reader.fieldnames or [])]
        yield reader


def number(value):
    if value in (None,'','M','NA'):
        return None
    n = float(value)
    if not math.isfinite(n):
        raise ValueError('Non-finite numeric value')
    return n


def import_airports(args):
    zones = {}
    if args.timezones:
        with csv_rows(args.timezones) as rows:
            if not {'icao','tz'}.issubset(rows.fieldnames):
                raise ValueError('Expected airportsdata CSV with icao and tz fields')
            for r in rows:
                for key in [r.get('icao'),r.get('iata'),r.get('lid')]:
                    if key:
                        zones.setdefault(key,set()).add(r['tz'])
    con = connection(args.db)
    kept = 0
    unresolved = 0
    digest = sha_file(args.airports)
    countries = {'US'} if args.states_only else US_COUNTRIES
    with con:
        with csv_rows(args.airports) as rows:
            required = {'id','ident','name','iso_country','latitude_deg','longitude_deg','type'}
            if not required.issubset(rows.fieldnames):
                raise ValueError('Expected OurAirports columns: '+', '.join(sorted(required)))
            con.execute('DELETE FROM airports')
            for r in rows:
                if r['iso_country'] not in countries or r['type'] in {'closed','closed_airport'}:
                    continue
                lat,lon=number(r['latitude_deg']),number(r['longitude_deg'])
                if lat is None or lon is None or not (-90<=lat<=90 and -180<=lon<=180):
                    raise ValueError('Invalid airport coordinates: '+r['ident'])
                tzset=set()
                for key in [r.get('icao_code'),r.get('gps_code'),r.get('iata_code'),r.get('local_code')]:
                    tzset.update(zones.get(key,set()))
                timezone = next(iter(tzset)) if len(tzset)==1 else None
                if timezone:
                    try:
                        ZoneInfo(timezone)
                    except ZoneInfoNotFoundError:
                        timezone=None
                unresolved += timezone is None
                con.execute('INSERT INTO airports VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(
                    int(r['id']),r['ident'],r.get('iata_code') or None,r.get('icao_code') or None,
                    r.get('local_code') or None,r['name'],r['iso_country'],r.get('iso_region'),
                    r['type'],r.get('scheduled_service')=='yes',lat,lon,timezone,digest))
                kept += 1
        con.execute('INSERT OR REPLACE INTO imports VALUES (?,?,?,?)',
                    (digest,'airports',now(),str(Path(args.airports).name)))
    con.close()
    print(json.dumps({'airports':kept,'missing_or_ambiguous_timezones':unresolved,'db':args.db}))
    return 0


def field_key(s):
    return re.sub(r'[^a-z0-9]','',s.lower())


def normalize_row(row):
    return {field_key(k):v for k,v in row.items() if k}


def hhmm(value):
    if value in (None,''):
        return None
    n=float(value)
    if n != int(n):
        raise ValueError('Non-integer HHMM: '+str(value))
    n=int(n)
    if n==2400:
        return n
    if not (0<=n<=2359) or n%100>=60:
        raise ValueError('Invalid HHMM: '+str(value))
    return n


def import_ontime(args):
    path=Path(args.file)
    digest=sha_file(path)
    con=connection(args.db)
    count=0
    periods=set()
    with con:
        with csv_rows(path) as rows:
            cols={field_key(x) for x in rows.fieldnames}
            required={'flightdate','origin','dest','crsdeptime','crsarrtime','cancelled','diverted'}
            if not required.issubset(cols) or 'reportingairline' not in cols:
                raise ValueError('Expected BTS reporting-carrier CSV, not marketing-carrier data')
            con.execute('CREATE TEMP TABLE incoming AS SELECT * FROM flight_history WHERE 0')
            insert='INSERT INTO incoming VALUES ('+','.join('?' for _ in range(17))+')'
            batch=[]
            for raw in rows:
                r=normalize_row(raw)
                day=r['flightdate'][:10]
                dt.date.fromisoformat(day)
                period=day[:7]
                periods.add(period)
                cancelled=number(r['cancelled'])
                diverted=number(r['diverted'])
                if cancelled not in (0,1) or diverted not in (0,1):
                    raise ValueError('Missing or invalid cancellation/diversion flag')
                delay_15 = number(r.get('arrdel15'))
                if delay_15 not in (None,0,1):
                    raise ValueError('Invalid arrival-delay flag')
                batch.append((day,r['reportingairline'],r.get('dotidreportingairline') or None,
                    r.get('flightnumberreportingairline') or None,r.get('originairportid') or None,
                    r['origin'],r.get('destairportid') or None,r['dest'],hhmm(r['crsdeptime']),
                    hhmm(r['crsarrtime']),number(r.get('crselapsedtime')),int(cancelled),int(diverted),
                    number(r.get('arrdelay')),delay_15,digest,period))
                count += 1
                if len(batch)>=10000:
                    con.executemany(insert,batch)
                    batch.clear()
            if batch:
                con.executemany(insert,batch)
            if not count:
                raise ValueError('No flight rows; existing records retained')
            # This importer expects complete national monthly exports. A revised month replaces
            # the previous month, avoiding double-counting changed/cancelled rows.
            if len(periods) != 1:
                raise ValueError('Import one complete monthly reporting-carrier file at a time')
            period=next(iter(periods))
            con.execute('DELETE FROM flight_history WHERE period=?',(period,))
            con.execute('INSERT INTO flight_history SELECT * FROM incoming')
            con.execute('DROP TABLE incoming')
            con.execute('INSERT OR REPLACE INTO imports VALUES (?,?,?,?)',(digest,'ontime',now(),path.name))
    con.close()
    print(json.dumps({'rows_imported':count,'period':period,'sha256':digest,'db':args.db}))
    return 0


def route_stats(args):
    con=connection(args.db)
    con.row_factory=sqlite3.Row
    rows=con.execute('''SELECT carrier, origin, destination, COUNT(*) scheduled_flights,
      MIN(flight_date) first_observed, MAX(flight_date) last_observed,
      AVG(cancelled) cancellation_rate,
      AVG(CASE WHEN cancelled=0 AND diverted=0 THEN arrival_delay_minutes END) mean_arrival_delay_minutes,
      SUM(CASE WHEN cancelled=0 AND diverted=0 AND arrival_delay_15 IS NOT NULL THEN 1 ELSE 0 END) delay_observations,
      AVG(CASE WHEN cancelled=0 AND diverted=0 THEN arrival_delay_15 END) arrival_delay_15_rate
      FROM flight_history WHERE origin=? AND destination=?
      GROUP BY carrier,origin,destination ORDER BY scheduled_flights DESC''',
      (args.origin.upper(),args.destination.upper())).fetchall()
    print(json.dumps({'kind':'historical_summary','not_future_availability':True,
                      'rows':[dict(r) for r in rows]},indent=2))
    con.close()
    return 0


def plan_ontime(args):
    start=dt.date.fromisoformat(args.start+'-01')
    end=dt.date.fromisoformat(args.end+'-01')
    if start>end or start<dt.date(2018,1,1) or end>dt.date(2026,8,1):
        raise ValueError('Supported plan range: 2018-01 through verified latest month 2026-08')
    jobs=[]
    month=start
    while month<=end:
        filename=f'On_Time_Reporting_Carrier_On_Time_Performance_1987_present_{month.year}_{month.month}.zip'
        jobs.append(dict(id=f'bts_ontime_{month:%Y_%m}',source_id='bts_ontime_reporting',group='history',
            url='https://transtats.bts.gov/PREZIP/'+filename,format='zip',filename='bts/'+filename,
            data_period=f'{month:%Y-%m}',verification='Provider filename pattern; each download must succeed before use.'))
        month=dt.date(month.year+int(month.month==12),month.month%12+1,1)
    atomic_json(args.out,dict(as_of=AS_OF,jobs=jobs))
    print(json.dumps({'jobs':len(jobs),'plan':args.out,'network_requests':0}))
    return 0


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    sub=p.add_subparsers(dest='command',required=True)
    cat=sub.add_parser('catalog',help='Show the researched source metadata')
    cat.set_defaults(func=lambda a:print(json.dumps(read_json(ROOT/'catalog.json'),indent=2)) or 0)
    for cmd in ('plan','fetch'):
        c=sub.add_parser(cmd,help='List jobs without network requests' if cmd=='plan' else 'Download a selected set of public files')
        c.add_argument('--jobs',default=str(ROOT/'download_jobs.json'))
        c.add_argument('--group',nargs='+',default=['reference'])
        c.add_argument('--id',nargs='+',default=[])
        if cmd=='plan':
            c.set_defaults(func=lambda a:print(json.dumps(jobs_for(a.jobs,a.group,a.id),indent=2)) or 0)
        else:
            c.add_argument('--out',default='data')
            c.add_argument('--max-file-mb',type=float,default=512)
            c.add_argument('--max-total-mb',type=float,default=2048)
            c.add_argument('--user-agent',default='USFlightDataResearch/1.0')
            c.set_defaults(func=fetch)
    c=sub.add_parser('plan-ontime',help='Create a monthly BTS download plan without network requests')
    c.add_argument('--start',required=True,help='YYYY-MM')
    c.add_argument('--end',default='2026-08',help='YYYY-MM')
    c.add_argument('--out',default='ontime_jobs.json')
    c.set_defaults(func=plan_ontime)
    c=sub.add_parser('import-airports',help='Import U.S. airport CSV and optional time-zone CSV')
    c.add_argument('--airports',required=True)
    c.add_argument('--timezones')
    c.add_argument('--states-only',action='store_true',help='Keep country US only; omit separately coded territories')
    c.add_argument('--db',default='data/flights.sqlite')
    c.set_defaults(func=import_airports)
    c=sub.add_parser('import-ontime',help='Import one complete national monthly reporting-carrier CSV or ZIP')
    c.add_argument('--file',required=True)
    c.add_argument('--db',default='data/flights.sqlite')
    c.set_defaults(func=import_ontime)
    c=sub.add_parser('route-stats',help='Summarize observed historical flights; does not return bookable flights')
    c.add_argument('--origin',required=True)
    c.add_argument('--destination',required=True)
    c.add_argument('--db',default='data/flights.sqlite')
    c.set_defaults(func=route_stats)
    args=p.parse_args(argv)
    try:
        return args.func(args)
    except (OSError, ValueError, sqlite3.Error, zipfile.BadZipFile) as e:
        print(str(e),file=sys.stderr)
        return 1


if __name__=='__main__':
    raise SystemExit(main())
