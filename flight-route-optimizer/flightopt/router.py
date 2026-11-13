"""Itinerary search over a timetable.

* `ConnectionPolicy` owns which connections are legal and their minimum buffers.
* `Router.candidates` enumerates itineraries with an exact relaxation bound: a backward pass
  computes, for each airport and number of remaining legs, the latest departure that can
  still reach a destination before the deadline. Any partial itinerary that cannot meet
  that bound is pruned, which never discards a feasible itinerary.
* `Router.recovery` builds a backward earliest-arrival profile to the destinations for a
  carrier group, answering "if I am stranded at X at time t, when do I get there on the
  next protected option?" in O(log n).
"""
from __future__ import annotations

import bisect
import json
from dataclasses import dataclass, field
from importlib import resources

from .schedule import Leg, Timetable

NEG = -(1 << 62)
POS = 1 << 62


def default_affiliations() -> dict[str, list[str]]:
    p = resources.files('flightopt') / 'data' / 'carrier_affiliations.json'
    return json.loads(p.read_text())['affiliations']


@dataclass
class ConnectionPolicy:
    min_connection: dict[str, int] = field(default_factory=lambda: {'large_airport': 45, 'medium_airport': 35})
    default_min_connection: int = 30
    physical_min: dict[str, int] = field(default_factory=lambda: {'large_airport': 25})
    default_physical_min: int = 20
    user_min_connection: int | None = None
    max_layover: int = 300
    allow_affiliates: bool = True
    allow_separate_tickets: bool = False
    separate_ticket_min: int = 120
    max_circuity: float = 1.8
    circuity_slack_miles: float = 250
    affiliations: dict[str, list[str]] = field(default_factory=dict)

    def mct(self, airport_type: str) -> int:
        if self.user_min_connection is not None:
            return self.user_min_connection
        return self.min_connection.get(airport_type, self.default_min_connection)

    def physical(self, airport_type: str) -> int:
        return self.physical_min.get(airport_type, self.default_physical_min)

    def brands(self, leg: Leg) -> set[str]:
        s = {leg.mkt}
        s.update(self.affiliations.get(leg.mkt, ()))
        return s

    def connection_type(self, a: Leg, b: Leg) -> str | None:
        if a.mkt == b.mkt:
            if a.fn == b.fn and a.op == b.op:
                return 'through'
            return 'online'
        if self.allow_affiliates and (self.brands(a) & self.brands(b)):
            return 'affiliate_possible'
        if self.allow_separate_tickets:
            return 'separate_tickets'
        return None

    def required_buffer(self, ctype: str, airport_type: str) -> int:
        if ctype == 'through':
            return 0
        if ctype == 'separate_tickets':
            return max(self.separate_ticket_min, self.mct(airport_type))
        return self.mct(airport_type)

    def group(self, leg: Leg) -> frozenset[str]:
        """Carriers whose flights can protect a rebooking for a passenger on this leg."""
        g = set(self.brands(leg))
        for regional, partners in self.affiliations.items():
            if leg.mkt in partners:
                g.add(regional)
        return frozenset(g)


@dataclass
class Itinerary:
    legs: list[Leg]
    origin_access: int = 0
    dest_access: int = 0
    connection_types: list[str] = field(default_factory=list)

    @property
    def dep(self) -> int:
        return self.legs[0].dep

    @property
    def arr(self) -> int:
        return self.legs[-1].arr

    @property
    def stops(self) -> int:
        return len(self.legs) - 1

    @property
    def elapsed(self) -> int:
        return self.arr - self.dep

    def layovers(self) -> list[int]:
        return [b.dep - a.arr for a, b in zip(self.legs, self.legs[1:])]

    def key(self) -> tuple:
        return tuple(l.id for l in self.legs)


class Router:
    def __init__(self, timetable: Timetable, airports, policy: ConnectionPolicy):
        self.tt = timetable
        self.airports = airports
        self.policy = policy
        self._profiles: dict = {}
        self._atype = {}

    def atype(self, code: str) -> str:
        t = self._atype.get(code)
        if t is None:
            a = self.airports.get(code)
            t = self._atype[code] = a.type if a else ''
        return t

    def latest_departure_bounds(self, dests: set[str], deadline: int, max_legs: int) -> list[dict[str, int]]:
        bounds = [{d: POS for d in dests}]
        legs = [l for l in self.tt.legs if l.arr <= deadline]
        for k in range(1, max_legs + 1):
            prev, cur = bounds[-1], dict(bounds[-1])
            for l in legs:
                need = prev.get(l.dest, NEG)
                if l.dest in dests or l.arr <= need:
                    if l.dep > cur.get(l.origin, NEG):
                        cur[l.origin] = l.dep
            bounds.append(cur)
        return bounds

    def candidates(self, origins: dict[str, int], dests: dict[str, int], windows: dict[str, tuple[int, int]],
                   deadline: int, max_legs: int, limit: int = 20000, use_bounds: bool = True) -> list[Itinerary]:
        """All legal itineraries departing origin o within windows[o] and arriving by deadline.

        use_bounds=False disables pruning; tests use it to show pruning never drops an itinerary.
        """
        p = self.policy
        dset = set(dests)
        if use_bounds:
            bounds = self.latest_departure_bounds(dset, deadline, max_legs)
        else:
            everywhere = {a: POS for a in self.tt.served}
            bounds = [{d: POS for d in dset}] + [everywhere] * max_legs
        out: list[Itinerary] = []
        gc = {}
        for o in origins:
            for d in dests:
                gc[(o, d)] = self.airports.distance(o, d) if o != d else 0
        avoid = set(origins)

        def dfs(path: list[Leg], ctypes: list[str], flown: float, origin: str):
            if len(out) >= limit:
                return
            last = path[-1]
            if last.dest in dset:
                out.append(Itinerary(list(path), origins[origin], dests[last.dest], list(ctypes)))
                return
            left = max_legs - len(path)
            if left <= 0:
                return
            at = self.atype(last.dest)
            window_start = last.arr  # through-flights may leave immediately; others filtered below
            for nxt in self.tt.departures(last.dest, window_start, last.arr + p.max_layover):
                if nxt.dest in avoid or any(nxt.dest == l.origin for l in path):
                    continue
                if nxt.arr > deadline:
                    continue
                if nxt.dest not in dset and bounds[left - 1].get(nxt.dest, NEG) < nxt.arr:
                    continue
                ctype = p.connection_type(last, nxt)
                if ctype is None or nxt.dep - last.arr < p.required_buffer(ctype, at):
                    continue
                if ctype == 'through' and nxt.dep - last.arr > 120:
                    ctype = 'online'
                    if nxt.dep - last.arr < p.required_buffer(ctype, at):
                        continue
                f = flown + nxt.distance
                best_gc = min(gc[(origin, d)] for d in dset)
                if f > p.max_circuity * best_gc + p.circuity_slack_miles:
                    continue
                path.append(nxt)
                ctypes.append(ctype)
                dfs(path, ctypes, f, origin)
                path.pop()
                ctypes.pop()

        for o in origins:
            for leg in self.tt.departures(o, *windows[o]):
                if leg.arr > deadline or leg.dest in avoid:
                    continue
                if leg.dest not in dset and bounds[max_legs - 1].get(leg.dest, NEG) < leg.arr:
                    continue
                dfs([leg], [], leg.distance, o)
        return out

    # ---------- recovery profiles ----------
    def recovery(self, dests: frozenset[str], group: frozenset[str] | None):
        key = (dests, group)
        prof = self._profiles.get(key)
        if prof is not None:
            return prof
        p = self.policy
        neg_deps: dict[str, list[int]] = {}
        arrs: dict[str, list[int]] = {}

        def query(airport: str, ready: int) -> int | None:
            nd = neg_deps.get(airport)
            if not nd:
                return None
            # entries are in increasing -dep (decreasing dep); those with dep >= ready form a prefix
            i = bisect.bisect_right(nd, -ready)
            return arrs[airport][i - 1] if i else None

        for l in reversed(self.tt.legs):
            if group is not None and l.mkt not in group and l.op not in group:
                continue
            if l.origin in dests:
                continue
            if l.dest in dests:
                a = l.arr
            else:
                a = query(l.dest, l.arr + p.mct(self.atype(l.dest)))
                if a is None:
                    continue
            lst = arrs.setdefault(l.origin, [])
            if not lst or a < lst[-1]:
                neg_deps.setdefault(l.origin, []).append(-l.dep)
                lst.append(a)
        prof = query
        self._profiles[key] = prof
        return prof
