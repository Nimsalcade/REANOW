"""SQLite storage. One database owns every imported fact and its source provenance."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import sqlite3
from pathlib import Path

SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sources(
  id INTEGER PRIMARY KEY,
  sha256 TEXT NOT NULL,
  kind TEXT NOT NULL,
  filename TEXT NOT NULL,
  period TEXT,
  rows INTEGER NOT NULL,
  synthetic INTEGER NOT NULL DEFAULT 0,
  imported_at_utc TEXT NOT NULL,
  detail TEXT,
  UNIQUE(sha256, kind)
);
CREATE TABLE IF NOT EXISTS airports(
  iata TEXT PRIMARY KEY, icao TEXT, faa_lid TEXT, name TEXT NOT NULL, city TEXT, state TEXT,
  country TEXT NOT NULL, type TEXT, scheduled_service INTEGER NOT NULL,
  lat REAL NOT NULL, lon REAL NOT NULL, tz TEXT, tz_source TEXT
);
-- Observed historical operations (BTS on-time). Times are scheduled local times at origin.
CREATE TABLE IF NOT EXISTS flights(
  flight_date TEXT NOT NULL, month INTEGER NOT NULL, dow INTEGER NOT NULL,
  mkt TEXT NOT NULL, op TEXT NOT NULL, fn TEXT NOT NULL, op_fn TEXT NOT NULL,
  origin TEXT NOT NULL, dest TEXT NOT NULL,
  dep_local INTEGER NOT NULL, elapsed INTEGER, distance REAL,
  dep_delay REAL, arr_delay REAL, cancelled INTEGER NOT NULL, diverted INTEGER NOT NULL,
  period TEXT NOT NULL, source_id INTEGER NOT NULL REFERENCES sources(id)
);
CREATE INDEX IF NOT EXISTS flights_date ON flights(flight_date);
CREATE INDEX IF NOT EXISTS flights_route ON flights(origin, dest);
CREATE INDEX IF NOT EXISTS flights_period ON flights(period);
-- BTS T-100 segment, monthly carrier-route totals.
CREATE TABLE IF NOT EXISTS t100(
  year INTEGER NOT NULL, month INTEGER NOT NULL, carrier TEXT NOT NULL, origin TEXT NOT NULL,
  dest TEXT NOT NULL, service_class TEXT, departures_scheduled REAL, departures_performed REAL,
  seats REAL, passengers REAL, distance REAL, source_id INTEGER NOT NULL REFERENCES sources(id)
);
CREATE INDEX IF NOT EXISTS t100_route ON t100(origin, dest);
-- Aggregated DB1B/DB1C market fares (directional, per passenger, one market direction).
CREATE TABLE IF NOT EXISTS fares(
  period TEXT NOT NULL, survey TEXT NOT NULL, origin TEXT NOT NULL, dest TEXT NOT NULL,
  carrier TEXT NOT NULL, nonstop INTEGER NOT NULL, passengers REAL NOT NULL,
  fare_p25 REAL, fare_p50 REAL, fare_p75 REAL, fare_mean REAL, distance REAL,
  source_id INTEGER NOT NULL REFERENCES sources(id)
);
CREATE INDEX IF NOT EXISTS fares_od ON fares(origin, dest);
-- Future schedule from a separately verified provider. Never filled from history.
CREATE TABLE IF NOT EXISTS schedule(
  provider TEXT NOT NULL, mkt TEXT NOT NULL, op TEXT NOT NULL, fn TEXT NOT NULL,
  origin TEXT NOT NULL, dest TEXT NOT NULL, dep_utc TEXT NOT NULL, arr_utc TEXT NOT NULL,
  dep_date_local TEXT NOT NULL, source_id INTEGER NOT NULL REFERENCES sources(id),
  UNIQUE(provider, mkt, fn, origin, dep_utc)
);
CREATE INDEX IF NOT EXISTS schedule_date ON schedule(dep_date_local);
"""


def now_utc() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def connect(path: str | Path) -> sqlite3.Connection:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path, check_same_thread=False)
    con.executescript(SCHEMA)
    con.row_factory = sqlite3.Row
    return con


def sha256_file(path: str | Path) -> str:
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        while block := f.read(1 << 20):
            h.update(block)
    return h.hexdigest()


def register_source(con, sha, kind, filename, period, rows, synthetic=False, detail=None) -> int:
    """Insert or replace the provenance row for a source file and return its id.

    Re-importing the same bytes replaces the previous rows owned by that source.
    """
    old = con.execute('SELECT id FROM sources WHERE sha256=? AND kind=?', (sha, kind)).fetchone()
    if old:
        for table in ('flights', 't100', 'fares', 'schedule'):
            con.execute(f'DELETE FROM {table} WHERE source_id=?', (old['id'],))
        con.execute('DELETE FROM sources WHERE id=?', (old['id'],))
    cur = con.execute(
        'INSERT INTO sources(sha256,kind,filename,period,rows,synthetic,imported_at_utc,detail) '
        'VALUES (?,?,?,?,?,?,?,?)',
        (sha, kind, filename, period, rows, int(synthetic), now_utc(), json.dumps(detail) if detail else None))
    return cur.lastrowid


def set_meta(con, key: str, value) -> None:
    con.execute('INSERT OR REPLACE INTO meta VALUES (?,?)', (key, json.dumps(value)))


def get_meta(con, key: str, default=None):
    row = con.execute('SELECT value FROM meta WHERE key=?', (key,)).fetchone()
    return json.loads(row['value']) if row else default


def dataset_summary(con) -> dict:
    s = {}
    s['synthetic'] = bool(con.execute('SELECT MAX(synthetic) FROM sources').fetchone()[0] or 0)
    r = con.execute('SELECT COUNT(*), MIN(flight_date), MAX(flight_date) FROM flights').fetchone()
    s['flights'] = dict(rows=r[0], first_date=r[1], last_date=r[2])
    s['periods'] = [x[0] for x in con.execute('SELECT DISTINCT period FROM flights ORDER BY period')]
    s['airports'] = con.execute('SELECT COUNT(*) FROM airports').fetchone()[0]
    s['t100_rows'] = con.execute('SELECT COUNT(*) FROM t100').fetchone()[0]
    s['fare_markets'] = con.execute('SELECT COUNT(*) FROM fares').fetchone()[0]
    s['fare_periods'] = [x[0] for x in con.execute('SELECT DISTINCT period FROM fares ORDER BY period')]
    s['verified_schedule_rows'] = con.execute('SELECT COUNT(*) FROM schedule').fetchone()[0]
    s['sources'] = [dict(r) for r in con.execute(
        'SELECT kind, filename, period, rows, synthetic, imported_at_utc, sha256 FROM sources ORDER BY id')]
    return s
