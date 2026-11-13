"""Monte Carlo itinerary risk with disruption recovery.

Each sample draws, per leg, a cancellation, a diversion, a departure delay and an en-route
deviation from the leg forecast. A connection is missed when the inbound actual arrival plus
the physical minimum walk/boarding time is later than the outbound actual departure. A
missed or cancelled passenger is rebooked onto the earliest protected option to the
destination (recovery profile), assuming a seat is available on it. Random numbers are keyed
by leg, so two itineraries sharing a leg see the same draws (common random numbers), which
makes their comparison far less noisy than independent simulation.

Known simplifications, surfaced in results: legs are independent (same-day weather
correlation is not modeled), rebooked travel uses the model's average arrival delay, and the
first protected option is assumed to have a seat.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import numpy as np

from .delay_model import DEP_EDGES, ENR_EDGES, bin_means, sample_from_bins
from .schedule import from_minutes, to_minutes


@dataclass
class RiskParams:
    samples: int = 2000
    cancel_rebook_lag: int = 60     # minutes after scheduled departure before re-accommodation can depart
    divert_delay: int = 240         # assumed extra minutes for a diverted flight to reach its destination
    stranded_penalty: int = 24 * 60  # used when no protected option exists in the timetable horizon
    seed: int = 20261113


def global_rebook_delay(model, month: int) -> float:
    f = model.leg('', '', '', '', 720, month)  # unseen keys fall back to the global level
    return max(0.0, f.mean_arr_delay)


def evaluate(itin, router, model, dests: frozenset, month: int, params: RiskParams, rebook_delay: float,
             dest_tz: str | None, deadline: int | None = None) -> dict:
    N = params.samples
    legs = itin.legs
    policy = router.policy
    final = np.full(N, np.nan)
    resolved = np.zeros(N, bool)
    missed_any = np.zeros(N, bool)
    cancelled_any = np.zeros(N, bool)
    diverted_any = np.zeros(N, bool)
    stranded = np.zeros(N, bool)
    ready = np.full(N, -np.inf)
    leg_out, conn_miss = [], []

    def recover(mask, airport, ready_times):
        idx = np.flatnonzero(mask)
        if not len(idx):
            return
        prof = router.recovery(dests, policy.group(leg))
        cache = {}
        for j in idx:
            t = int(np.ceil(ready_times[j]))
            a = cache.get(t, 0)
            if a == 0:
                a = cache[t] = prof(airport, t)
            if a is None:
                stranded[j] = True
                final[j] = t + params.stranded_penalty
            else:
                final[j] = a + rebook_delay
        resolved[idx] = True

    for i, leg in enumerate(legs):
        f = model.leg(leg.op, leg.op_fn, leg.origin, leg.dest, leg.dep_local, month)
        rng = np.random.default_rng([params.seed, leg.id])
        u = rng.random((6, N))
        cxl = u[0] < f.p_cancel
        div = ~cxl & (u[1] < f.p_divert)
        d = sample_from_bins(f.dep, DEP_EDGES, f.tails['dep'], u[2], u[3])
        e = sample_from_bins(f.enr, ENR_EDGES, f.tails['enr'], u[4], u[5])
        dep_act = leg.dep + d
        through = i > 0 and itin.connection_types[i - 1] == 'through'
        if through:
            dep_act = np.maximum(dep_act, ready)  # same aircraft continues; it cannot leave without you
        arr_act = dep_act + leg.elapsed + e
        active = ~resolved
        leg_out.append(dict(p_cancel=f.p_cancel, p_dep15=f.p_dep15, p_arr15=float(np.mean(arr_act - leg.arr >= 15)),
                            mean_arr_delay=float(bin_means(f.dep, DEP_EDGES, f.tails['dep'])
                                                 + bin_means(f.enr, ENR_EDGES, f.tails['enr'])),
                            evidence_n=round(f.evidence_n, 1), evidence_level=f.evidence_level,
                            cancel_evidence_n=round(f.cancel_n, 1)))
        if i > 0:
            missed = active & ~cxl & (ready > dep_act)
            conn_miss.append(float(np.mean(missed)))
            missed_any |= missed
            recover(missed, leg.origin, np.maximum(ready, leg.dep + 1))
            active = ~resolved
        c = active & cxl
        cancelled_any |= c
        recover(c, leg.origin, np.maximum(ready, leg.dep + params.cancel_rebook_lag))
        active = ~resolved
        dv = active & div
        diverted_any |= dv
        if i == len(legs) - 1:
            final[dv] = leg.arr + params.divert_delay
            resolved |= dv
            ok = ~resolved
            final[ok] = arr_act[ok]
            resolved[:] = True
        else:
            if dv.any():
                recover(dv, leg.dest, np.full(N, leg.arr + params.divert_delay + policy.physical(router.atype(leg.dest))))
            nxt_through = itin.connection_types[i] == 'through'
            ready = arr_act + (0 if nxt_through else policy.physical(router.atype(leg.dest)))
    delay = final - itin.arr
    q = np.percentile(delay, [50, 80, 90])
    p_overnight = None
    if dest_tz:
        local = from_minutes(itin.arr, dest_tz)
        midnight = to_minutes(dt.datetime.combine(local.date() + dt.timedelta(days=1), dt.time(),
                                                  tzinfo=local.tzinfo))
        p_overnight = float(np.mean(final >= midnight))
    return dict(
        p_on_time=float(np.mean(delay < 15)), expected_delay=float(np.mean(delay)),
        p50_delay=float(q[0]), p80_delay=float(q[1]), p90_delay=float(q[2]),
        p_misconnect=float(np.mean(missed_any)), p_cancel=float(np.mean(cancelled_any)),
        p_divert=float(np.mean(diverted_any)), p_disrupted=float(np.mean(missed_any | cancelled_any | diverted_any)),
        p_stranded=float(np.mean(stranded)), p_after_midnight=p_overnight,
        expected_arrival=float(itin.arr + np.mean(delay)), p90_arrival=float(itin.arr + q[2]),
        p_by_deadline=None if deadline is None else float(np.mean(final <= deadline)),
        connection_miss=conn_miss, legs=leg_out, samples=N)
