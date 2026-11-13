# flightopt: risk-priced route optimization for U.S. flights

`flightopt` finds and ranks U.S. passenger itineraries by what a trip is **likely** to cost you in time, risk and
money, not only by its printed schedule. It is a local command-line tool and web app built on public BTS and FAA
data, using the collector in [`collector/`](collector/README.md).

Most flight search ranks by scheduled duration, which hides the important problem: a 35-minute connection at a
congested hub can look fastest on paper but arrive later on average than a slower routing, and sometimes it means
spending the night. `flightopt` simulates every candidate itinerary against learned delay, cancellation and
diversion distributions, including **rebooking onto the next protected flight** when a connection is missed. It
then ranks itineraries on expected and 90th-percentile door-to-door time, with fare and stops.

It answers three kinds of question:

| Question | Command / tab | Output |
|---|---|---|
| *How should I fly from A to B on this date?* | `search` / **Plan a trip** | Ranked itineraries with on-time probability, misconnect risk, expected and P90 arrival, historical fare, Pareto flag and plain-language reasons |
| *How reliable is this route, by carrier and hour?* | `route` / **Route report** | Cancellation and 15-minute-late rates, by carrier and departure hour; T-100 seats, passengers and load factor; fare quartiles; that day's timetable |
| *Where is the network weak?* | `hubs`, `opportunities` / **Network** | Connectivity ranking, and unserved nonstop markets ranked by passenger-hours a nonstop would save |

## Quick start (demo, about 1 minute)

Requires Python 3.11+ and NumPy.

```sh
pip install .                     # or: python -m flightopt ... from this folder
flightopt demo                    # synthetic data -> import -> fit + tune model
flightopt search BOS LAX --date 2026-12-18 --after 07:00
flightopt serve                   # http://127.0.0.1:8765
flightopt evaluate --truth data/synthetic/SYNTHETIC_truth.csv.gz
```

**The demo uses synthetic flight data.** Airport locations and time zones are real. Carriers, schedules, delays,
traffic and fares are fictional. The fictional carrier codes (N1, N2, N3, L1, R1, U1) mean no output can be mistaken
for a real airline's record. The UI and CLI show a banner while synthetic data is loaded. Synthetic files use the BTS
column layouts, so the demo runs through the same importers as real downloads.

## Using real data

```sh
cd collector
python flight_data.py fetch --group reference history --out ../data/raw_downloads
python flight_data.py plan-ontime --start 2025-09 --end 2026-08 --out ontime_jobs.json
python flight_data.py fetch --jobs ontime_jobs.json --group history --out ../data/raw_downloads
python flight_data.py fetch --group fares --out ../data/raw_downloads --max-file-mb 2048 --max-total-mb 8192
cd ..
flightopt init
flightopt ingest collector data/raw_downloads    # airports, every on-time month, DB1C market
flightopt build                                  # fits on all months, tunes on the latest
flightopt evaluate                               # out-of-sample backtest on the latest month
flightopt serve
```

Other inputs:

* `flightopt ingest t100 T100_SEGMENT.csv` loads a BTS T-100 Domestic Segment export, which feeds the route reports.
* `flightopt ingest fares FILE --survey db1c|db1b` loads a market table. The DB1C column layout was not verified
  here. If a column isn't recognized, the importer stops and lists the columns it found; map them with
  `--column-map '{"fare":"..."}'`.
* `flightopt ingest schedule FILE --provider NAME` loads a **verified future schedule**, for example a provider
  export with columns `carrier, flight_number, origin, destination, departure_local, arrival_local|elapsed_minutes`.
  For each date it covers, a verified schedule replaces the historical pattern.
* **Prefer the BTS marketing-carrier on-time file when you can.** It records brand and operator, for example UA
  flights operated by OO. Without it, regional connections rely on an editable affiliation table
  (`flightopt/data/carrier_affiliations.json`, knowledge through mid-2025), and those connections are labeled
  "verify single ticket".

Use 12 or more months of history so seasonality is learned. With less, the response says when the travel month is not
covered.

## How it works

```
BTS on-time ─▶ flights ─┬─▶ hierarchical leg model (dep delay, en-route deviation, cancel, divert)
                        └─▶ timetable: verified schedule ▸ else recurring weekday pattern
DB1C/DB1B ─▶ fare distributions        │
T-100 ─▶ route traffic                 ▼
            router: legal itineraries (exact pruning) ─▶ Monte Carlo with rebooking ─▶ rank + explain
```

**1. Leg model (`delay_model.py`).** For each leg it predicts a 17-bin departure-delay distribution, a 9-bin en-route
deviation (arrival minus departure delay, so sampled arrivals stay consistent with sampled departures), and
cancellation and diversion probabilities. Each prediction is an empirical-Bayes Dirichlet backoff chain:
all flights → carrier → carrier at origin → carrier at origin in this time block → this specific recurring flight. A
level's counts are pulled toward its parent with a concentration that is tuned on a later held-out month.
Departure delay uses a hurdle decomposition, P(≥15 min) × the shape below 15 × the shape above 15, each part
smoothed separately. A single smoothing strength for the whole distribution is dominated by the on-time bins and
flattened the late-departure signal: in the synthetic backtest the decomposition raised the share of achievable
accuracy from 65% to 84%. Counts are stored by calendar month and reweighted by seasonal distance to the travel month,
with the distant-month weight also tuned, so one fitted model serves any date.

**2. Timetable (`schedule.py`).** If a verified schedule covers the date, it is used. Otherwise flights are taken
from the last four matching weekdays in history. A flight is kept if it operated on at least half of them, including
one of the last two. Every leg carries this basis into the output. Times are converted to UTC from each airport's
IANA zone, and arrival is departure plus scheduled elapsed time, so overnight and DST cases are handled.

**3. Router (`router.py`).** A depth-first search finds legal itineraries: carrier-compatible connections, minimum
buffers by airport size, a maximum layover, a circuity limit and no revisited airports. Through-flights are
recognized. A backward pass computes, for each airport and number of remaining legs, the latest departure that can
still reach the destination. This bound is a relaxation, so pruning never drops a feasible itinerary; a test checks
this against unpruned search on random networks. A backward profile scan gives O(log n) answers to "stranded at X at
time t: when do I arrive on the next protected option?"

**4. Risk (`risk.py`).** For each candidate, 2,000 simulated days with common random numbers per leg, so itineraries
that share a leg are compared on the same draws. A connection is missed when the inbound actual arrival plus a
physical transfer time is later than the outbound actual departure. Missed and cancelled passengers are rebooked
through the recovery profile. Outputs include on-time probability, misconnect, cancellation and stranding risk,
P50/P80/P90 delay, chance of arriving after midnight and chance of meeting an `--by` deadline.

**5. Ranking (`planner.py`).** Generalized cost = fare + value of time × expected door-to-door time + risk aversion
× (P90 − expected) + a transfer penalty. The profiles `balanced`, `fastest`, `reliable` and `cheapest` set the
weights, and `--vot` sets dollars per hour. Itineraries on the Pareto front of expected time, fare and P90 time are
flagged, and the response explains trade-offs, for example "scheduled 25 min longer but expected 12 min sooner".

Metro codes (`NYC`, `CHI`, `WAS`, `BAYAREA`, `LAAREA`, `DALLAS`, `HOUSTON`, `SFLA`, `ORLANDO`, `PHOENIX`, `TAMPA`)
search every airport in the area. `--nearby 80` adds alternate airports with an estimated drive time.

## Validation

Measured on the synthetic data (`flightopt evaluate --truth ...`). The model trains on March–June, tunes on July and
is scored on August, a month it never saw, using 80,000 sampled rows per target:

| Event | Global rate | Carrier only | Naive backoff (n≥20) | **Hierarchical model** | Best possible at booking time* |
|---|---|---|---|---|---|
| Departure ≥15 min late (Brier) | 0.1704 | 0.1696 | 0.1688 | **0.1675** | 0.1670 |
| Cancellation (Brier) | 0.01938 | 0.01934 | 0.01951 | **0.01933** | 0.01933 |
| Arrival ≥15 min late (Brier) | 0.1652 | – | – | **0.1623** | – |

\*The *structural oracle* is the generator's true probability averaged over the weather state, which is unknown when
booking. The model captures **84%** of the achievable gain for departure delays and **92%** for cancellations. Its
mean absolute error against the true probabilities is 1.6 points for departure delay and 0.13 points for
cancellation. Calibration deciles track observed rates within about 2 points. The naive backoff has the worst log
loss (2.29 vs 2.16), which shows why smoothing matters on sparse flight-level data.

These figures show that the estimator recovers known structure. **They are not real-world accuracy.** Run
`flightopt evaluate` after importing BTS data to measure real accuracy. Real delays have structure the generator
lacks, such as aircraft rotations and correlated weather.

The test suite (`python -m unittest discover tests`, 17 tests, about 30 s) covers:

* importer fail-closed behavior and month replacement
* DST and overnight conversion
* pattern frequency
* exact pruning on random networks
* recovery profiles against a brute-force connection scan
* risk outcomes in hand-built worlds
* an end-to-end import → build → search → HTTP API run

The collector's own 15 tests still pass.

## Limitations (also returned with every result)

* **Patterns are not schedules.** Without a verified schedule, results show flights that operated recently on that
  weekday, not proof that they operate on your date or have seats. BTS data lags about 3 months.
* **Connection buffers are planning defaults** (45 min at large airports, 35 at medium, 30 at small), not airline
  minimum connection times.
* **Legs are simulated independently**, so same-day weather correlation is not modeled. The first protected
  rebooking is assumed to have a seat, which understates disruption cost on full routes. The model is a booking-time
  forecast: it does not use live weather or FAA status.
* **Fares are historical** one-way market fares (DB1B/DB1C), not quotes. `opportunities` ranks time saved and is
  not a profitability forecast.
* Only BTS reporting carriers are covered. Small carriers that report only to T-100 have no timetable here.

## Layout

| Path | Role |
|---|---|
| `flightopt/ingest.py` | BTS on-time (reporting + marketing), T-100, DB1B/DB1C, verified schedule importers |
| `flightopt/airports.py` | Airport reference build/import, metro groups, nearby airports |
| `flightopt/schedule.py` | Timetables (verified or pattern), time-zone conversion |
| `flightopt/delay_model.py` | Hierarchical leg model, tuning, persistence |
| `flightopt/router.py` | Connection policy, itinerary search, recovery profiles |
| `flightopt/risk.py` | Monte Carlo itinerary simulation |
| `flightopt/fare_model.py` | Fare estimates with backoff |
| `flightopt/planner.py` | Application service shared by CLI and web app |
| `flightopt/evaluate.py` | Time-split backtest |
| `flightopt/synthetic.py` | Labeled synthetic data with ground truth |
| `flightopt/server.py`, `flightopt/web/` | Local web app (standard library server, no external JS) |
| `collector/` | The original data-collection starter (downloader, catalog, findings) |

Airport data: OurAirports (public domain) and airportsdata (MIT, see `licenses/`). Source data keeps its providers'
terms.
