# Data for a U.S. flight route and schedule tool

Research date: **November 13, 2026**. Scope: passenger flight suggestions across the 50 states, with U.S. territories tagged separately. This also supports early route-network and demand research. It is not an aircraft dispatch or flight-path planning system.

The most useful public foundation is **FAA airport data + BTS flight performance + BTS route traffic + BTS historical tickets/fares + NOAA weather + FAA airport disruptions**. These can support historical route analysis, departure-time reliability estimates and disruption context. A service that recommends flights for a future date also needs verified future schedules. Bookable prices and available seats require live airline or booking-provider content.

The source catalog covers 39 entries, including open downloads, manual public exports, restricted services, legacy sources and two commercial options for missing functionality. It is a broad primary-source inventory, not a claim that every airport website or local dataset has been exhausted. No upstream bulk datasets were downloaded because this workspace blocked direct access to the tested source hosts. The package provides collection instructions and runnable code for the included public URLs.

## Current data periods

| Data | Latest period verified today | Implication |
| --- | --- | --- |
| BTS reporting/marketing on-time data | August 2026 | Detailed past flight records, with a release lag |
| BTS domestic T-100 traffic | August 2026 | Recent observed routes and monthly capacity |
| BTS DB1C Ticket, Market, Coupon, Segment | July 2026 | Historical ticket paths and fares |
| FAA NASR | Effective October 29, 2026 | Use this cycle for today's airport reference data; November 26 is a future cycle |
| FAA passenger boardings | Calendar year 2025 | Airport importance and coverage checks |
| DOT Air Travel Consumer Report | October 2026 publication; mostly August data | Airline service quality, with mixed reporting periods |
| OurAirports | Download page lists November 12, 2026 files | Rolling community airport reference |
| IANA time-zone rules | 2026f, released October 27 | Current local-time conversion rules |

Sources: [BTS release log](https://www.transtats.bts.gov/releaseinfo.asp), [DB1C Ticket](https://www.bts.gov/topics/airlines-and-airports/origin-and-destination-survey-data-ticket), [Market](https://www.bts.gov/topics/airlines-and-airports/origin-and-destination-survey-data-market), [Coupon](https://www.bts.gov/topics/airlines-and-airports/origin-and-destination-survey-data-coupon), [Segment](https://www.bts.gov/topics/airlines-and-airports/origin-and-destination-survey-data-segment), [FAA cycles](https://www.faa.gov/air_traffic/flight_info/aeronav/Aero_Data/NASR_Subscription/), [FAA boardings](https://www.faa.gov/airports/planning_capacity/passenger_allcargo_stats/passenger), [DOT report](https://www.transportation.gov/resources/individuals/aviation-consumer-protection/october-2026-air-travel-consumer-report-august), [OurAirports](https://ourairports.com/data/), [IANA](https://www.iana.org/time-zones).

An access date is different from a dataset's period, revision date and effective date. Keep all four when available. Search results can retain old descriptions; prefer the actual dataset and release pages. BTS revised earlier T-100 months in its latest release, so repeatable results need file hashes and retained versions.

## What each layer contributes

| Project need | Start here | What remains missing |
| --- | --- | --- |
| Airport search and nearby alternatives | FAA NASR; OurAirports; airportsdata | Exact terminal facilities and current airline service need confirmation |
| Correct local departure/arrival times | airportsdata plus IANA tzdb | Airline schedule dates and reliable day offsets |
| Existing route-network research | T-100 Domestic Segment | Date-specific future operation |
| Departure-time and carrier reliability | BTS reporting-carrier history | Smaller-carrier coverage and immediate aircraft/crew conditions |
| Typical historical fares and demand | DB1C, with DB1B for older periods | Today's fare, seats and fare conditions |
| Weather risk | AWC current data; GHCNh history | A validated delay model and forecast-as-issued archive |
| Airport disruptions | FAA NAS status and ATCSCC advisories | Airline flight-specific status |
| Rural and Alaska coverage checks | T-100, DOT EAS, FAA boardings | Current small-carrier timetables and sales content |
| Viable future connections | Verified schedule provider plus connection rules | Complete free nationwide minimum-connection data was not identified |
| Bookable itinerary | Live airline/booking offers | Public statistical data cannot supply inventory |

These are project-design recommendations based on the source characteristics, not measured model results.

## Changes older tutorials may miss

**DB1B became DB1C.** DB1B collected a quarterly 10% ticket sample through June 2025. DB1C began in July 2025 with monthly 40% sampling. Build separate ingestion paths and retain a survey-version field. Do not concatenate records and treat the samples or weights as interchangeable. [BTS survey description](https://www.bts.gov/topics/airlines-and-airports/origin-and-destination-survey-data)

**The DB1C Segment file is not the entire nonstop network.** It contains segments created from coupons that have intermediate via points. Coupons without via points must also be included to represent all segments. Join with the documented ticket/coupon keys and preserve each table's record level. [BTS Segment explanation](https://www.bts.gov/topics/airlines-and-airports/origin-and-destination-survey-data-segment)

**Use GHCNh for current hourly weather history.** NOAA says the older ISD product has no updates after August 24, 2025 and announced retirement of its old download service in 2026. GHCNh is the replacement and supports PSV and Parquet access. [NOAA notice](https://www.nesdis.noaa.gov/news/service-location-change-integrated-surface-data-global-hourly), [GHCNh](https://www.ncei.noaa.gov/products/global-historical-climatology-network-hourly)

**OpenSky is not an unrestricted production feed.** Its current terms require a written license for commercial use and for operational REST API use, including internal live systems. Current authenticated API access uses OAuth2. Tracking observations also do not provide a future airline timetable. [Terms](https://opensky-network.org/about/terms-of-use), [API](https://openskynetwork.github.io/opensky-api/rest.html)

**OpenFlights routes are historical.** Route updates ended in June 2014. Use those files only for old-study reproduction or tests, not today's service map. [OpenFlights](https://openflights.org/data)

## The public-data gaps

I did not identify a single unrestricted, complete U.S. feed combining future flight schedules, current fares, remaining seats, flight-specific status, gates and carrier-specific connection rules. Each is a different product. Airport departure boards and airline sites may show some of these facts publicly; that does not establish bulk reuse rights or complete nationwide coverage.

[OAG's developer service](https://developers.oag.com/) is one option to test future schedule and connection functionality; its documented free tier has limits and is not an open national download. [Duffel offers](https://duffel.com/docs/api/offer-requests) are an example of live shopping content once access is enabled. Its [test mode](https://duffel.com/docs/api/overview/test-mode) can return unrealistic schedules and prices. These are optional gap options, not part of the public-data collection jobs. No paid service is needed for the historical prototype.

FAA SWIM and NOTAM services can add detail, but require their own access process and terms. Full ASPM records also have restrictions. The catalog distinguishes these from files available without a key. [SWIM access](https://www.faa.gov/air_traffic/technology/swim/products/get_connected), [NOTAM API](https://www.faa.gov/about/initiatives/notam), [ASPM access](https://www.aspm.faa.gov/getInfo.asp)

No bookings, payments, account creation, credential requests or access-agreement acceptance were performed.

## Recommended collection order

1. **Reference data.** Collect FAA airports and weather stations, OurAirports and airport time zones. Build explicit mappings among IATA, ICAO, FAA location codes and stable BTS IDs. Preserve unknown and conflicting codes for review.
2. **Historical route and performance data.** Begin with one complete on-time month to confirm parsing. Then collect 24–36 months of reporting-carrier data and matching T-100 passenger segments. Keep marketing-carrier data separate to avoid counting the same operations twice.
3. **Historical fares.** Add DB1C Ticket/Market data and the necessary Coupon/Segment joins. Retain passenger counts, fare units, itinerary type and data-quality fields. Keep DB1B as a separate older series.
4. **Weather.** Map airport stations, then select corresponding GHCNh station/year records. Take AWC and FAA status snapshots for current-condition experiments. Large radar/model archives can wait until a baseline shows a reason to add them.
5. **Future suggestions.** Connect a verified future-schedule source. Add live offers if users need prices or booking. Every candidate must correspond to an actual future flight record.

For a local pilot, 20–50 GB of free disk is a planning estimate for reference files, a few years of selected history, a database and intermediate work. Actual use depends on selected fields and indexes. One published August 2026 on-time ZIP is roughly 32 MB compressed; expanded data and database storage are larger. This package does not measure full archive sizes. Downloading all gridded weather is a separate, much larger task. [BTS ZIP listing](https://transtats.bts.gov/PREZIP/)

## Data model and quality checks

Keep airports, carrier history, observed flights, future flights, monthly route traffic, ticket/market fares, weather observations, forecasts and disruption events in separate tables. Every table should identify its source file or provider, record time, retrieval time, period and version. Store raw files so you can reproduce derived tables.

Use UTC for ordering future legs and keep each airport's IANA zone for display. Retain the original local date/time. Account for overnight arrivals, `2400`, daylight-saving ambiguity and date changes. Do not prepend `K` to every U.S. airport code: Alaska, Hawaii and territories use other patterns, and many facilities have only local identifiers.

For nationwide scope, test Alaska and Hawaii separately from the lower 48. Keep Puerto Rico, U.S. Virgin Islands, Guam, Northern Mariana Islands, American Samoa and minor outlying islands explicitly tagged. A lower-48 bounding box is insufficient. No country list by itself proves commercial-service coverage.

Distinguish the operating airline from the marketing brand. Use stable airline and airport IDs with validity dates for historical joins. A city market may contain multiple airports; an airport change within a connection requires ground travel that a flight-only recommendation must not silently assume.

Track flight sample size and missing values alongside every risk estimate. Missing data means unknown, not zero delay. Cancellation rates use all scheduled rows; arrival-delay metrics need a stated eligible-flight denominator. A route seen last year may no longer operate. A fare average is not a quote. Neither ADS-B proximity nor a recurring historical flight number proves a current scheduled leg.

## How to use the data in the AI tool

Use a schedule-search layer to enumerate valid direct flights and connections. Apply real connection rules, date constraints, maximum stops and arrival deadlines before ranking. If connection rules are unavailable, show that limitation or limit the first version to direct flights. A fixed buffer can be a user preference, but should not be presented as an airline-approved minimum.

Start with understandable ranking factors: total travel time, stops, departure/arrival preference, verified price when present, cancellation risk and arrival-delay risk. Weight choices depend on the intended users; they have not been calibrated here. An LLM can interpret preferences and explain results after the flights and scores have been calculated from data.

For predictive work, split training and evaluation by time. Use only information available at the moment of each proposed recommendation. Actual arrival time, later cancellation codes, later weather observations and later-revised records are future information for an earlier prediction. Retain forecasts as issued rather than replacing them with observed weather. Evaluate error and calibration separately for large hubs, small airports, Alaska, Hawaii and territories; report insufficient coverage.

An initial useful output could be: “Among verified flights on your requested date, these options fit your time window; this route and departure window had lower historical disruption rates in the available sample.” The starter does not claim that result has already been calculated.

## What the package does and does not complete

The catalog, source links, download plans, local schema, basic airport/on-time importers and offline checks are prepared. The documentation supports a public historical-data prototype. Raw nationwide collection, live endpoint validation, T-100/DB1C/weather importers, a future-schedule provider, a predictive model and a user interface remain project work. The collected source metadata is real; test flight rows are synthetic and used only inside the tests.
