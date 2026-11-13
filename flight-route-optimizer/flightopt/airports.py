"""Airport reference data: import, lookup, metro groups and nearby alternatives."""
from __future__ import annotations

import csv
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from zoneinfo import ZoneInfo

from .db import register_source, sha256_file
from .geo import estimated_drive_minutes, great_circle_miles

# ---- reference-file build (OurAirports facility facts + airportsdata time zones) ----
US_COUNTRIES = {'US', 'PR', 'VI', 'GU', 'MP', 'AS', 'UM'}
# Regions with exactly one civil time zone (derivation, recorded as tz_source=region_rule).
SINGLE_ZONE = {
    'US-HI': 'Pacific/Honolulu', 'PR-U-A': 'America/Puerto_Rico', 'VI-U-A': 'America/St_Thomas',
    'GU-U-A': 'Pacific/Guam', 'MP-U-A': 'Pacific/Saipan', 'AS-U-A': 'Pacific/Pago_Pago',
    'US-WA': 'America/Los_Angeles', 'US-CA': 'America/Los_Angeles',
    'US-UT': 'America/Denver', 'US-CO': 'America/Denver', 'US-WY': 'America/Denver', 'US-MT': 'America/Denver',
    'US-NM': 'America/Denver', 'US-IL': 'America/Chicago', 'US-WI': 'America/Chicago',
    'US-MN': 'America/Chicago', 'US-IA': 'America/Chicago', 'US-MO': 'America/Chicago', 'US-AR': 'America/Chicago',
    'US-LA': 'America/Chicago', 'US-MS': 'America/Chicago', 'US-AL': 'America/Chicago', 'US-OK': 'America/Chicago',
    'US-NY': 'America/New_York', 'US-NJ': 'America/New_York', 'US-PA': 'America/New_York', 'US-MA': 'America/New_York',
    'US-CT': 'America/New_York', 'US-RI': 'America/New_York', 'US-VT': 'America/New_York', 'US-NH': 'America/New_York',
    'US-ME': 'America/New_York', 'US-DE': 'America/New_York', 'US-MD': 'America/New_York', 'US-DC': 'America/New_York',
    'US-VA': 'America/New_York', 'US-WV': 'America/New_York', 'US-NC': 'America/New_York', 'US-SC': 'America/New_York',
    'US-GA': 'America/New_York', 'US-OH': 'America/New_York',
}


def build_airport_file(ourairports: str, airportsdata: str, out: str) -> int:
    tz_by_code: dict[str, set[str]] = {}
    with open(airportsdata, encoding='utf-8-sig', newline='') as f:
        for r in csv.DictReader(f):
            for code in (r.get('iata'), r.get('icao'), r.get('lid')):
                if code and r.get('tz'):
                    tz_by_code.setdefault(code, set()).add(r['tz'])
    rows = []
    with open(ourairports, encoding='utf-8-sig', newline='') as f:
        for r in csv.DictReader(f):
            if r['iso_country'] not in US_COUNTRIES or not r['iata_code'] or r['type'] == 'closed':
                continue
            zones: set[str] = set()
            for code in (r['iata_code'], r['icao_code'], r['gps_code'], r['local_code']):
                zones |= tz_by_code.get(code, set())
            tz, source = (next(iter(zones)), 'airportsdata') if len(zones) == 1 else ('', '')
            if not tz and not zones:
                region = r['iso_region']
                if region in SINGLE_ZONE:
                    tz, source = SINGLE_ZONE[region], 'region_rule'
                elif region == 'US-AK':
                    lon = float(r['longitude_deg'])
                    tz, source = ('America/Adak' if lon < -169.5 else 'America/Anchorage'), 'region_rule'
            if tz:
                ZoneInfo(tz)
            rows.append(dict(
                iata=r['iata_code'], icao=r['icao_code'], faa_lid=r['local_code'], name=r['name'],
                city=r['municipality'],
                state=r['iso_region'].split('-', 1)[-1] if r['iso_country'] == 'US' else r['iso_country'],
                country=r['iso_country'],
                type=r['type'], scheduled_service=int(r['scheduled_service'] == 'yes'),
                lat=f"{float(r['latitude_deg']):.6f}", lon=f"{float(r['longitude_deg']):.6f}",
                tz=tz, tz_source=source))
    # One row per IATA code: prefer scheduled service, then larger facility type.
    rank = {'large_airport': 0, 'medium_airport': 1, 'small_airport': 2, 'seaplane_base': 3, 'heliport': 4}
    best: dict[str, dict] = {}
    for r in rows:
        key = (-r['scheduled_service'], rank.get(r['type'], 9))
        if r['iata'] not in best or key < best[r['iata']]['_k']:
            best[r['iata']] = {**r, '_k': key}
    fields = ['iata', 'icao', 'faa_lid', 'name', 'city', 'state', 'country', 'type',
              'scheduled_service', 'lat', 'lon', 'tz', 'tz_source']
    with open(out, 'w', encoding='utf-8', newline='') as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction='ignore')
        w.writeheader()
        for code in sorted(best):
            w.writerow(best[code])
    return len(best)


# Multi-airport metro markets. Keys are user-facing search tokens.
METRO_GROUPS = {
    'NYC': ('New York area', ['JFK', 'LGA', 'EWR']),
    'CHI': ('Chicago area', ['ORD', 'MDW']),
    'WAS': ('Washington DC area', ['DCA', 'IAD', 'BWI']),
    'BAYAREA': ('San Francisco Bay Area', ['SFO', 'OAK', 'SJC']),
    'LAAREA': ('Los Angeles area', ['LAX', 'BUR', 'LGB', 'SNA', 'ONT']),
    'DALLAS': ('Dallas-Fort Worth area', ['DFW', 'DAL']),
    'HOUSTON': ('Houston area', ['IAH', 'HOU']),
    'SFLA': ('South Florida', ['MIA', 'FLL', 'PBI']),
    'ORLANDO': ('Orlando area', ['MCO', 'SFB']),
    'PHOENIX': ('Phoenix area', ['PHX', 'AZA']),
    'TAMPA': ('Tampa Bay area', ['TPA', 'PIE']),
}


@dataclass(frozen=True, slots=True)
class Airport:
    iata: str
    name: str
    city: str
    state: str
    country: str
    type: str
    scheduled_service: bool
    lat: float
    lon: float
    tz: str | None

    @property
    def zone(self) -> ZoneInfo | None:
        return ZoneInfo(self.tz) if self.tz else None


def bundled_airports_path() -> Path:
    return Path(str(resources.files('flightopt') / 'data' / 'us_airports.csv'))


def import_airports(con, path: str | Path | None = None) -> int:
    path = Path(path) if path else bundled_airports_path()
    with path.open(encoding='utf-8-sig', newline='') as f:
        rows = list(csv.DictReader(f))
    need = {'iata', 'name', 'country', 'lat', 'lon', 'tz', 'scheduled_service'}
    if not rows or not need.issubset(rows[0].keys()):
        raise ValueError(f'Airport CSV needs columns {sorted(need)}; build it with tools/build_airports.py')
    with con:
        register_source(con, sha256_file(path), 'airports', path.name, None, len(rows))
        con.execute('DELETE FROM airports')
        for r in rows:
            tz = r['tz'] or None
            if tz:
                ZoneInfo(tz)  # fail closed on an unknown zone name
            con.execute('INSERT OR REPLACE INTO airports VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)', (
                r['iata'], r.get('icao') or None, r.get('faa_lid') or None, r['name'], r.get('city'),
                r.get('state'), r['country'], r.get('type'), int(r['scheduled_service']),
                float(r['lat']), float(r['lon']), tz, r.get('tz_source') or None))
    return len(rows)


class AirportIndex:
    def __init__(self, con):
        self.by_code: dict[str, Airport] = {}
        for r in con.execute('SELECT * FROM airports'):
            self.by_code[r['iata']] = Airport(r['iata'], r['name'], r['city'] or '', r['state'] or '',
                                              r['country'], r['type'] or '', bool(r['scheduled_service']),
                                              r['lat'], r['lon'], r['tz'])
        if not self.by_code:
            raise ValueError('No airports loaded; run `flightopt init` first')

    def get(self, code: str) -> Airport | None:
        return self.by_code.get(code.upper())

    def distance(self, a: str, b: str) -> float:
        x, y = self.by_code[a], self.by_code[b]
        return great_circle_miles(x.lat, x.lon, y.lat, y.lon)

    def search(self, text: str, limit: int = 12, served: set[str] | None = None) -> list[dict]:
        q = text.strip().upper()
        out = []
        for key, (label, codes) in METRO_GROUPS.items():
            if q and (key.startswith(q) or q in label.upper()):
                out.append(dict(code=key, label=f'{label} ({", ".join(codes)})', kind='metro'))
        scored = []
        for a in self.by_code.values():
            if served is not None and a.iata not in served:
                continue
            hay = f'{a.iata} {a.name} {a.city} {a.state}'.upper()
            if not q:
                score = 3
            elif a.iata == q:
                score = 0
            elif a.iata.startswith(q):
                score = 1
            elif q in hay:
                score = 2
            else:
                continue
            scored.append((score, not a.scheduled_service, a.iata, a))
        for *_, a in sorted(scored)[:limit]:
            out.append(dict(code=a.iata, label=f'{a.iata} · {a.name}, {a.city} {a.state}'.strip(), kind='airport'))
        return out[:limit]

    def resolve(self, token: str, nearby_miles: float = 0, served: set[str] | None = None) -> list[dict]:
        """Resolve a search token into airports with estimated ground-access minutes.

        Returns dicts: code, access_minutes, reason. Metro-group members have 0 access time
        because the traveler chose the metro area. Nearby airports carry a drive estimate.
        """
        token = token.strip().upper()
        if token in METRO_GROUPS:
            base = [(c, 0, f'{METRO_GROUPS[token][0]} airport') for c in METRO_GROUPS[token][1]]
        elif token in self.by_code:
            base = [(token, 0, 'requested airport')]
        else:
            raise ValueError(f'Unknown airport or metro code: {token}')
        seen = {c for c, *_ in base}
        out = [dict(code=c, access_minutes=m, reason=r) for c, m, r in base]
        if nearby_miles > 0:
            anchors = [self.by_code[c] for c in seen if c in self.by_code]
            for a in self.by_code.values():
                if a.iata in seen or not a.scheduled_service or (served is not None and a.iata not in served):
                    continue
                d = min(great_circle_miles(x.lat, x.lon, a.lat, a.lon) for x in anchors)
                if d <= nearby_miles:
                    out.append(dict(code=a.iata, access_minutes=estimated_drive_minutes(d),
                                    reason=f'nearby alternative, {d:.0f} mi (drive time estimated)'))
                    seen.add(a.iata)
        return out
