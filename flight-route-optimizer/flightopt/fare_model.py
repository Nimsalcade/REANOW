"""Historical fare estimates from aggregated DB1B/DB1C market fares.

Backoff: carrier+market+nonstop -> market+nonstop -> reverse direction -> distance model
fitted on all markets. Always a historical typical one-way market fare, never a quote.
"""
from __future__ import annotations

import math

import numpy as np


class FareModel:
    def __init__(self, con, recent_periods: int = 6):
        periods = [r[0] for r in con.execute('SELECT DISTINCT period FROM fares ORDER BY period DESC')][:recent_periods]
        self.periods = sorted(periods)
        self.surveys = sorted({r[0] for r in con.execute('SELECT DISTINCT survey FROM fares')})
        self.table: dict[tuple, dict] = {}
        self.coef = None
        if not periods:
            return
        q = (f'SELECT origin, dest, carrier, nonstop, SUM(passengers) pax, SUM(fare_p25*passengers)/SUM(passengers) p25,'
             f' SUM(fare_p50*passengers)/SUM(passengers) p50, SUM(fare_p75*passengers)/SUM(passengers) p75,'
             f' AVG(distance) dist FROM fares WHERE period IN ({",".join("?" * len(periods))})'
             ' GROUP BY origin, dest, carrier, nonstop')
        xs, ys, ws, ns = [], [], [], []
        for r in con.execute(q, periods):
            rec = dict(p25=r['p25'], p50=r['p50'], p75=r['p75'], passengers=r['pax'])
            self.table[(r['origin'], r['dest'], r['carrier'], r['nonstop'])] = rec
            if r['carrier'] == '*' and r['dist'] and r['nonstop'] in (0, 1):
                xs.append(math.log(max(r['dist'], 50)))
                ys.append(math.log(r['p50']))
                ws.append(r['pax'])
                ns.append(r['nonstop'])
        if len(xs) >= 20:
            X = np.column_stack([np.ones(len(xs)), xs, ns])
            W = np.sqrt(np.array(ws))
            self.coef, *_ = np.linalg.lstsq(X * W[:, None], np.array(ys) * W, rcond=None)
            resid = np.array(ys) - X @ self.coef
            self.spread = float(np.sqrt(np.average(resid ** 2, weights=ws)))

    @property
    def available(self) -> bool:
        return bool(self.table)

    def market_passengers(self, o: str, d: str) -> float:
        return sum(self.table.get((o, d, '*', ns), {}).get('passengers', 0) for ns in (0, 1, -1))

    def estimate(self, o: str, d: str, carrier: str, nonstop: bool, distance: float) -> dict | None:
        if not self.table:
            return None
        ns = int(nonstop)
        for key, basis in (((o, d, carrier, ns), f'{carrier} {"nonstop" if ns else "connecting"} fares {o}-{d}'),
                           ((o, d, '*', ns), f'all-carrier {"nonstop" if ns else "connecting"} fares {o}-{d}'),
                           ((d, o, carrier, ns), f'{carrier} fares in the reverse direction {d}-{o}'),
                           ((d, o, '*', ns), f'all-carrier fares in the reverse direction {d}-{o}'),
                           ((o, d, '*', 1 - ns), f'all-carrier fares {o}-{d}, other routing type')):
            rec = self.table.get(key)
            if rec and rec['passengers'] >= 10:
                return dict(rec, basis=basis, periods=self.periods, kind='historical_market_fare')
        if self.coef is not None:
            mu = self.coef[0] + self.coef[1] * math.log(max(distance, 50)) + self.coef[2] * ns
            z = 0.674 * self.spread
            return dict(p25=math.exp(mu - z), p50=math.exp(mu), p75=math.exp(mu + z), passengers=0,
                        basis='distance model fitted across markets (no market history)', periods=self.periods,
                        kind='modeled_fare')
        return None
