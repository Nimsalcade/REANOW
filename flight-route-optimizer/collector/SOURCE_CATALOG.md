# U.S. flight data source catalog

Checked 2026-11-13. 39 source entries. Raw source files have not been downloaded.

Access labels separate public downloads, manual exports, account requirements, legacy data, and commercial gap options.

## Reference

### FAA NASR airport and facility data

**Priority:** core · **Access:** free_no_key · **Formats:** CSV ZIP, AIXM, TXT, shapefile

Official airport identifiers, coordinates, facilities, runways and weather-station links.

- Coverage: U.S. airports and landing facilities, including Alaska, Hawaii and territories; check each file scope.
- Updates: 28-day cycle
- Latest verified: 2026-10-29 effective; next cycle 2026-11-26
- Limits: Many facilities have no passenger service. Effective date differs from publication date.
- Reuse: Public government release. Retain source attribution and any file-specific notices.

[Source 1](https://www.faa.gov/air_traffic/flight_info/aeronav/Aero_Data/NASR_Subscription/)
[Source 2](https://www.faa.gov/air_traffic/flight_info/aeronav/aero_data/NASR_Subscription/2026-10-29/)

### OurAirports

**Priority:** core · **Access:** free_no_key · **Formats:** CSV

Airport search, coordinates, IATA/ICAO/local codes, runways, frequencies and service flag.

- Coverage: Global; filter U.S. and territories explicitly.
- Updates: Daily
- Latest verified: Download page lists November 12, 2026 files
- Limits: Community data. A service flag does not prove that a flight operates on a requested date.
- Reuse: Public domain, as stated by OurAirports.

[Source 1](https://ourairports.com/data/)
[Source 2](https://ourairports.com/help/data-dictionary.html)
[Source 3](https://github.com/davidmegginson/ourairports-data)

### airportsdata airport time zones

**Priority:** core · **Access:** free_no_key · **Formats:** CSV, Python package

Map airport identifiers to IANA time zones and FAA location identifiers.

- Coverage: Global airport directory.
- Updates: Project releases and repository updates
- Latest verified: Current documentation checked; file contents not downloaded
- Limits: Current airports only; not a historical airport-code table. Some identifiers are internal codes.
- Reuse: MIT; retain the project license when redistributing its data or code.

[Source 1](https://github.com/mborsetti/airportsdata/blob/main/README.rst)
[Source 2](https://github.com/mborsetti/airportsdata/blob/main/airportsdata/airports.csv)

### IANA time-zone database

**Priority:** core · **Access:** free_no_key · **Formats:** tzdata archive

Correct local-time conversion, daylight-saving changes and overnight connections.

- Coverage: Worldwide time-zone rules.
- Updates: When rules change
- Latest verified: 2026f, released 2026-10-27
- Limits: Use a named zone and date. A fixed UTC offset is insufficient.
- Reuse: Public-domain time-zone data; retain bundled notices.

[Source 1](https://www.iana.org/time-zones)
[Source 2](https://data.iana.org/time-zones/tz-link.html)

### BTS airport history and code lookup tables

**Priority:** core · **Access:** free_export · **Formats:** CSV exports, lookup tables

Join AirportID, AirportSeqID, CityMarketID and DOT airline identifiers across years.

- Coverage: Airports, city markets, airlines and other codes used by BTS.
- Updates: As BTS revises tables
- Latest verified: Current schema checked; individual exports not downloaded
- Limits: Airport and airline display codes can be reused. Preserve date ranges and stable IDs.
- Reuse: Public government release. Retain source attribution and any file-specific notices.

[Source 1](https://www.transtats.bts.gov/DL_SelectFields.aspx?QO_fu146_anzr=&gnoyr_VQ=FLL)
[Source 2](https://www.transtats.bts.gov/Fields.asp?gnoyr_VQ=FGJ)

## Demand

### FAA airport boardings and cargo totals

**Priority:** useful · **Access:** free_export · **Formats:** XLSX, PDF

Airport importance, hub categories and checks on passenger coverage.

- Coverage: U.S. commercial-service and other reporting airports.
- Updates: Annual
- Latest verified: 2025; boarding files updated 2026-09-24
- Limits: Annual totals are not current flight schedules or available seats.
- Reuse: Public government release. Retain source attribution and any file-specific notices.

[Source 1](https://www.faa.gov/airports/planning_capacity/passenger_allcargo_stats/passenger)

### DOT Essential Air Service reports

**Priority:** useful · **Access:** free_export · **Formats:** XLSX, PDF, orders

Check small-community routes and carriers absent from large-airline performance files.

- Coverage: Subsidized small-community service; separate Alaska reports.
- Updates: Periodic; contracts vary
- Latest verified: May 2026 status reports listed
- Limits: Contracted service and published status do not establish a date-specific bookable flight.
- Reuse: Public government release. Retain source attribution and any file-specific notices.

[Source 1](https://www.transportation.gov/office-policy/aviation-policy/essential-air-service-reports)
[Source 2](https://www.transportation.gov/policy/aviation-policy/small-community-rural-air-service/essential-air-service)

### FAA airport capacity profiles

**Priority:** optional · **Access:** free_export · **Formats:** PDF

Background for congestion and runway-capacity analysis.

- Coverage: Selected busy airports.
- Updates: Occasional
- Latest verified: 2014 profiles described on current page
- Limits: Old planning assumptions; not current declared capacity or a live operating restriction.
- Reuse: Public government release. Retain source attribution and any file-specific notices.

[Source 1](https://www.faa.gov/airports/planning_capacity/profiles)

### FAA airport activity forecasts

**Priority:** optional · **Access:** free_export · **Formats:** XLSX, query tables, PDF

Long-term airport demand and route-network research.

- Coverage: Airports in NPIAS; scope described by FAA.
- Updates: Annual
- Latest verified: 2025 Terminal Area Forecast data; February 2026 page update
- Limits: This FAA TAF means activity forecast. It differs from the aviation-weather TAF.
- Reuse: Public government release. Retain source attribution and any file-specific notices.

[Source 1](https://www.faa.gov/data_research/aviation/taf)
[Source 2](https://www.faa.gov/data_research/aviation)

### BTS Data Bank 20

**Priority:** optional · **Access:** free_export · **Formats:** download files

Traffic, capacity and load-factor checks at broader levels.

- Coverage: Certificated and commuter U.S. carriers; scheduled and nonscheduled activity.
- Updates: Monthly
- Latest verified: August 2026
- Limits: Aggregates overlap other BTS releases; do not add them to T-100 totals.
- Reuse: Public government release. Retain source attribution and any file-specific notices.

[Source 1](https://www.bts.gov/browse-statistical-products-and-data/db20)

### TSA daily checkpoint volumes

**Priority:** optional · **Access:** public_page_unconfirmed · **Formats:** HTML tables

Seasonality and nationwide travel-demand context.

- Coverage: National screening totals.
- Updates: Daily
- Latest verified: Official page blocked during research; no latest count claimed
- Limits: Not per-flight demand, seat availability or airport wait times. Screenings differ from boardings.
- Reuse: Public government release. Retain source attribution and any file-specific notices.

[Source 1](https://www.tsa.gov/travel/passenger-volumes)
[Source 2](https://www.bts.gov/browse-statistical-products-and-data/preliminary-estimates/preliminary-estimates-enplanements-tsa)

## History

### BTS Reporting Carrier On-Time Performance

**Priority:** core · **Access:** free_no_key · **Formats:** CSV ZIP

Flight-level scheduled and actual times, cancellations, diversions and delay labels.

- Coverage: Domestic nonstop flights of reporting operators; not every U.S. carrier.
- Updates: Monthly, with revisions
- Latest verified: August 2026; full series begins 1987
- Limits: Retrospective scheduled times cannot establish future service. Reporting coverage changes.
- Reuse: Public government release. Retain source attribution and any file-specific notices.

[Source 1](https://transtats.bts.gov/TableInfo.asp?QO_fu146_anzr=b0-gvzr&V0s1_b0yB=D&gnoyr_VQ=FGJ)
[Source 2](https://transtats.bts.gov/PREZIP/)
[Source 3](https://www.transtats.bts.gov/releaseinfo.asp)

### BTS Marketing Carrier On-Time Performance

**Priority:** useful · **Access:** free_export · **Formats:** CSV ZIP

Customer-facing brand comparisons and operating/marketing carrier mapping.

- Coverage: Reporting marketing-airline networks and their operating carriers.
- Updates: Monthly
- Latest verified: August 2026; series begins January 2018
- Limits: Overlaps reporting-carrier data. Do not append both files as independent flights.
- Reuse: Public government release. Retain source attribution and any file-specific notices.

[Source 1](https://transtats.bts.gov/DL_SelectFields.aspx?QO_fu146_anzr=b0-gvzr&gnoyr_VQ=FGK)

### BTS T-100 Domestic Segment, All Carriers

**Priority:** core · **Access:** free_export · **Formats:** CSV ZIP

Observed route graph, monthly departures, capacity, passengers, distance and aircraft categories.

- Coverage: Reported U.S. domestic nonstop segments; filter passenger service.
- Updates: Monthly, revised releases
- Latest verified: August 2026
- Limits: Monthly aggregates have no flight departure times. Separate passenger service from cargo and charter.
- Reuse: Public government release. Retain source attribution and any file-specific notices.

[Source 1](https://www.transtats.bts.gov/Fields.asp?gnoyr_VQ=GEE)
[Source 2](https://www.transtats.bts.gov/releaseinfo.asp)

### BTS T-100 Domestic Market, All Carriers

**Priority:** useful · **Access:** free_export · **Formats:** CSV ZIP

On-flight passenger flows by carrier and airport pair.

- Coverage: Reported domestic on-flight markets.
- Updates: Monthly
- Latest verified: August 2026
- Limits: An on-flight market differs from both a nonstop segment and a whole passenger itinerary.
- Reuse: Public government release. Retain source attribution and any file-specific notices.

[Source 1](https://www.transtats.bts.gov/releaseinfo.asp)

### BTS DB1C / OD40 ticket survey

**Priority:** core · **Access:** free_no_key · **Formats:** ZIP data files, PDF definitions

Historical fares, passenger flows, itinerary paths and connection patterns.

- Coverage: Sampled reported airline passenger tickets; apply domestic itinerary filters.
- Updates: Monthly since July 2025
- Latest verified: July 2026 listed for Ticket, Market, Coupon and Segment files
- Limits: 40% sample. Not live quotes or inventory. Use the current field definitions and ticket weights.
- Reuse: Public government release. Retain source attribution and any file-specific notices.

[Source 1](https://www.bts.gov/topics/airlines-and-airports/origin-and-destination-survey-data)
[Source 2](https://www.bts.gov/topics/airlines-and-airports/origin-and-destination-survey-data-ticket)
[Source 3](https://www.bts.gov/topics/airlines-and-airports/origin-and-destination-survey-data-market)
[Source 4](https://www.bts.gov/topics/airlines-and-airports/origin-and-destination-survey-data-coupon)
[Source 5](https://www.bts.gov/topics/airlines-and-airports/origin-and-destination-survey-data-segment)

### BTS DB1B legacy ticket survey

**Priority:** optional · **Access:** free_no_key · **Formats:** CSV ZIP

Longer-run fare and itinerary baselines.

- Coverage: Historical ticket sample.
- Updates: Quarterly, ended June 2025
- Latest verified: 2025 Q2 is final collection period
- Limits: 10% sample. Keep separate from DB1C until schema, weighting and sampling changes are handled.
- Reuse: Public government release. Retain source attribution and any file-specific notices.

[Source 1](https://www.bts.gov/topics/airlines-and-airports/origin-and-destination-survey-data)
[Source 2](https://www.bts.gov/topics/airlines-and-airports/origin-and-destination-survey-data-product)
[Source 3](https://transtats.bts.gov/PREZIP/)

### DOT Domestic Airfare Consumer Report

**Priority:** useful · **Access:** free_export · **Formats:** PDF, download tables

Simple route/city fare baselines and competition measures.

- Coverage: Top domestic city-pair markets; report scope is not all airports.
- Updates: Quarterly with reporting lag
- Latest verified: 2026 Q1 report verified; separate BTS average-fare release covers 2026 Q2
- Limits: Averages are historical and may mix trip types. No current bookable fares.
- Reuse: Public government release. Retain source attribution and any file-specific notices.

[Source 1](https://www.transportation.gov/policy/aviation-policy/domestic-airfare-consumer-report)
[Source 2](https://www.transportation.gov/aviation-analysis/domestic-airline-consumer-airfare-report-1st-quarter-2026)
[Source 3](https://www.bts.gov/newsroom/second-quarter-2026-average-air-fare-increased-20-first-quarter-2026)

### DOT Air Travel Consumer Report

**Priority:** useful · **Access:** free_export · **Formats:** PDF, tables

Baggage, wheelchair, oversales, complaint and service-quality context.

- Coverage: Reporting airline service measures.
- Updates: Monthly; oversales quarterly
- Latest verified: October 2026 report: August data; oversales 2026 Q2
- Limits: Airline-level averages do not establish a particular flight or passenger outcome.
- Reuse: Public government release. Retain source attribution and any file-specific notices.

[Source 1](https://www.transportation.gov/resources/individuals/aviation-consumer-protection/air-travel-consumer-reports-2026-0)
[Source 2](https://www.transportation.gov/resources/individuals/aviation-consumer-protection/october-2026-air-travel-consumer-report-august)

### FAA OPSNET operations and delays

**Priority:** useful · **Access:** free_export · **Formats:** web queries, exports

Airport/system congestion history independent of airline-reported delays.

- Coverage: FAA facility operations and delay reports.
- Updates: Daily records; public release delayed
- Latest verified: Documentation checked; exact latest public month not confirmed
- Limits: Public access follows the 20th of the next month. Definitions differ from airline arrival delay.
- Reuse: Public government release. Retain source attribution and any file-specific notices.

[Source 1](https://www.aspm.faa.gov/opsnet/sys/main.asp)

## Restricted

### FAA ASPM performance data

**Priority:** optional · **Access:** mixed_public_restricted · **Formats:** reports, restricted data

Airport efficiency and operating constraints where authorized.

- Coverage: Selected airports and performance measures.
- Updates: Varies
- Latest verified: Access rules checked
- Limits: Complete actual flight/airport data are password-restricted; do not treat full ASPM as open.
- Reuse: Public reports and restricted products have different access conditions.

[Source 1](https://www.aspm.faa.gov/getInfo.asp)

### FAA NOTAM / NMS data

**Priority:** useful · **Access:** account_or_approval · **Formats:** API, AIXM, messages

Runway, facility and airport changes affecting service.

- Coverage: FAA NOTAM coverage; terms depend on distribution service.
- Updates: As issued
- Latest verified: Current NMS API access page checked
- Limits: Public viewing does not equal unrestricted bulk API access. NMS API access must be requested.
- Reuse: FAA access agreement and service-specific terms apply.

[Source 1](https://www.faa.gov/about/initiatives/notam)
[Source 2](https://www.faa.gov/about/initiatives/notam/faqs)
[Source 3](https://aa.data.faa.gov/data/)

### FAA SWIM / SWIFT distribution

**Priority:** optional · **Access:** account_or_approval · **Formats:** message streams, XML, service-specific formats

Deeper disruption and operational-data research after access approval.

- Coverage: Service-specific NAS flight, weather and traffic messages.
- Updates: Near real time
- Latest verified: Current access process checked
- Limits: Not an anonymous REST feed. Services and reuse depend on the agreement; some services need separate requests.
- Reuse: FAA describes data as currently without subscription fees; access agreements and integration costs remain.

[Source 1](https://www.faa.gov/air_traffic/technology/swim/products/get_connected)
[Source 2](https://www.faa.gov/air_traffic/technology/swim/questions_answers)
[Source 3](https://support.swim.faa.gov/hc/en-us)

### OpenSky aircraft observations

**Priority:** optional · **Access:** account_and_license_limits · **Formats:** JSON, research datasets

Observed movement research under the applicable agreement.

- Coverage: Receiver-dependent tracking; not complete national coverage.
- Updates: Live and selected archives
- Latest verified: Current API and license checked
- Limits: Not airline schedules. Current terms require written permission for commercial or operational API use.
- Reuse: Restricted research/education terms; OAuth2 API credentials; no automatic production reuse rights.

[Source 1](https://openskynetwork.github.io/opensky-api/rest.html)
[Source 2](https://opensky-network.org/about/terms-of-use)

## Weather

### NOAA Aviation Weather Center

**Priority:** core · **Access:** free_no_key · **Formats:** JSON, GeoJSON, XML, CSV, gzip caches

Airport observations, terminal forecasts, aviation hazards, pilot reports and station metadata.

- Coverage: METAR/TAF worldwide; hazard coverage varies, including separate Alaska products.
- Updates: Product-dependent; common cache files refresh each minute
- Latest verified: Live feeds; not downloaded here
- Limits: Use bulk caches for broad access. API history is limited; keep issue and valid times.
- Reuse: Public NOAA data; retain attribution and distinguish your derived outputs from original NOAA data.

[Source 1](https://aviationweather.gov/data/api/)

### NWS forecasts and alerts API

**Priority:** useful · **Access:** free_no_key · **Formats:** JSON, GeoJSON, CAP

Airport-area forecasts and severe-weather alerts.

- Coverage: NWS forecast areas; validate coverage by location.
- Updates: As products update
- Latest verified: Current documentation checked
- Limits: Required application User-Agent; rate limits apply. Short retained history is not a training archive.
- Reuse: NWS describes API data as free for any purpose; respect caching and request limits.

[Source 1](https://www.weather.gov/documentation/services-web-api)

### NOAA HRRR forecasts and archive

**Priority:** optional · **Access:** free_no_key · **Formats:** GRIB2, Zarr

Short-range weather features for disruption forecasting.

- Coverage: CONUS and Alaska sectors; not all U.S. islands.
- Updates: Frequent model cycles
- Latest verified: Current NCEP and NOAA-hosted catalog checked
- Limits: Large volumes. Select airport areas, variables, model runs and forecast hours.
- Reuse: Public NOAA data; retain attribution and distinguish your derived outputs from original NOAA data.

[Source 1](https://www.nco.ncep.noaa.gov/pmb/products/hrrr/)
[Source 2](https://registry.opendata.aws/noaa-hrrr-pds/)

### NOAA GFS forecasts

**Priority:** optional · **Access:** free_no_key · **Formats:** GRIB2, model archives

Broader weather outlook and regions outside HRRR coverage.

- Coverage: Global, including Hawaii and territories.
- Updates: Four model cycles daily
- Latest verified: Current product catalog checked
- Limits: Coarse forecasts are uncertain; save the forecast as issued for honest backtests.
- Reuse: Public NOAA data; retain attribution and distinguish your derived outputs from original NOAA data.

[Source 1](https://www.ncei.noaa.gov/products/weather-climate-models/global-forecast)
[Source 2](https://nomads.ncep.noaa.gov/)

### NOAA radar and MRMS data

**Priority:** optional · **Access:** free_no_key · **Formats:** GRIB2, radar files, cloud objects

Thunderstorm and precipitation features near airports and along routes.

- Coverage: Product-specific radar coverage.
- Updates: Near real time and archives
- Latest verified: NODD catalog checked
- Limits: High data volume; radar availability and product coverage vary by region.
- Reuse: Public NOAA data; retain attribution and distinguish your derived outputs from original NOAA data.

[Source 1](https://www.noaa.gov/nodd/datasets)
[Source 2](https://www.weather.gov/documentation/services-web-api)

## Weather History

### NOAA GHCNh hourly weather

**Priority:** core · **Access:** free_no_key · **Formats:** PSV, Parquet, archives

Historical wind, visibility, temperature, clouds and precipitation for delay models.

- Coverage: Global land stations; select U.S. airport/weather-station matches.
- Updates: Daily
- Latest verified: Current replacement for ISD; documentation v1.1.0
- Limits: Keep quality flags and station history; one station is not automatically one airport.
- Reuse: Public NOAA data; retain attribution and distinguish your derived outputs from original NOAA data.

[Source 1](https://www.ncei.noaa.gov/products/global-historical-climatology-network-hourly)
[Source 2](https://www.ncei.noaa.gov/oa/global-historical-climatology-network/hourly/doc/ghcnh_DOCUMENTATION.pdf)
[Source 3](https://www.ncei.noaa.gov/oa/global-historical-climatology-network/index.html)

### NOAA ISD legacy hourly weather

**Priority:** legacy · **Access:** free_no_key · **Formats:** NODD archives

Reproduce older studies or retain an existing historical pipeline.

- Coverage: Global historical stations.
- Updates: No new data after 2025-08-24
- Latest verified: Superseded; 2026 service-move notice checked
- Limits: Do not use an old ISD download script as the current-weather ingestion path.
- Reuse: Public NOAA data; retain attribution and distinguish your derived outputs from original NOAA data.

[Source 1](https://www.nesdis.noaa.gov/news/service-location-change-integrated-surface-data-global-hourly)

### Iowa State IEM airport weather archive

**Priority:** useful · **Access:** free_no_key · **Formats:** CSV, text

Convenient historical airport observations and raw METAR for research.

- Coverage: Many U.S. and worldwide airport stations; station history varies.
- Updates: Continuous archive; documented sync cycle
- Latest verified: Current documentation checked
- Limits: Limited quality checking; missing values have multiple causes. Keep source and station identifiers.
- Reuse: Public university archive; review current terms and cite IEM and upstream providers.

[Source 1](https://www.mesonet.agron.iastate.edu/request/download.phtml?network=IA_ASOS)

### NOAA Storm Events

**Priority:** optional · **Access:** free_export · **Formats:** CSV, web queries

Historical event labels and severe-weather case studies.

- Coverage: Reported significant U.S. weather events.
- Updates: Delayed periodic updates
- Latest verified: Landing page checked; latest event month not confirmed
- Limits: Retrospective reports cannot be used as information available before departure.
- Reuse: Public NOAA data; retain attribution and distinguish your derived outputs from original NOAA data.

[Source 1](https://www.ncei.noaa.gov/access/storm-events-database/)
[Source 2](https://www.ncei.noaa.gov/stormevents/ftp.jsp)

## Live

### FAA NAS airport status

**Priority:** core · **Access:** free_no_key · **Formats:** XML, website

Current airport disruption flags for candidate itineraries.

- Coverage: Reported NAS airport delays, ground stops and closures.
- Updates: Live; inspect feed timestamp
- Latest verified: Official XML endpoint linked from FAA FAQ
- Limits: Airport-level status is not flight-specific status. Empty or unavailable data do not prove normal operations.
- Reuse: Public government release. Retain source attribution and any file-specific notices.

[Source 1](https://nasstatus.faa.gov/)
[Source 2](https://www.fly.faa.gov/fly/FAQ/faq)
[Source 3](https://nasstatus.faa.gov/api/airport-status-information)

### FAA ATCSCC advisories

**Priority:** useful · **Access:** free_export · **Formats:** HTML, text

Ground programs, route advisories, outages and operating plans.

- Coverage: National airspace traffic-management advisories.
- Updates: As issued; archive queries
- Latest verified: Current public query form checked
- Limits: Parse issue/validity times. Text may require review; absence is not proof of no restriction.
- Reuse: Public government release. Retain source attribution and any file-specific notices.

[Source 1](https://www.fly.faa.gov/adv/advAdvisoryForm)
[Source 2](https://www.faa.gov/air_traffic/publications/aim_html/chap5_section_1.html)

### FAA Temporary Flight Restrictions

**Priority:** optional · **Access:** free_export · **Formats:** JSON, XML, web map

Restriction context where relevant to a flight disruption.

- Coverage: Published U.S. TFRs and covered territories.
- Updates: As issued
- Latest verified: FAA help describes JSON/XML list export
- Limits: TFRs are only one subset of restrictions. Not a complete airport-closure dataset.
- Reuse: Public government release. Retain source attribution and any file-specific notices.

[Source 1](https://tfr.faa.gov/)
[Source 2](https://tfr.faa.gov/tfr3/AboutTFRWebsite.pdf)

## Legacy

### OpenFlights legacy route data

**Priority:** legacy · **Access:** free_no_key · **Formats:** DAT/CSV

Tests or reproduction of older network research only.

- Coverage: Historical global route snapshot.
- Updates: Routes stopped updating in June 2014
- Latest verified: June 2014 route data
- Limits: Unsuitable for current route suggestions.
- Reuse: ODbL and Database Contents License; attribution and relevant sharing terms apply.

[Source 1](https://openflights.org/data)

## Licensed

### OAG schedules, status and connections

**Priority:** gap_option · **Access:** account_free_tier_then_licensed · **Formats:** JSON API, licensed files

Future flight instances, connection rules and flight status.

- Coverage: Commercial global schedule coverage; confirm U.S. small-carrier scope.
- Updates: Product-dependent
- Latest verified: Developer page lists limited free-tier access
- Limits: Not open bulk data or a booking service; free-tier limits and storage/redistribution terms apply.
- Reuse: Provider subscription terms; no purchase or account created here.

[Source 1](https://developers.oag.com/)
[Source 2](https://www.oag.com/airline-schedules-data)

### Duffel flight offers

**Priority:** gap_option · **Access:** account_and_commercial_terms · **Formats:** JSON API

Date-specific itineraries with live offers when live access is enabled.

- Coverage: Available airline content varies by integration.
- Updates: At search time
- Latest verified: Current offer and test-mode docs checked
- Limits: Not an open dataset. Test mode can contain unrealistic schedules and prices.
- Reuse: Commercial service terms and live-access requirements apply; no account created here.

[Source 1](https://duffel.com/docs/api/offer-requests)
[Source 2](https://duffel.com/docs/api/overview/test-mode)

