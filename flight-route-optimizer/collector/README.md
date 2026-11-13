# U.S. flight data starter

Prepared November 13, 2026 for a local tool that suggests passenger flight routes and travel times across the United States.

This package contains a researched catalog of 39 data sources, 23 download jobs, a Python downloader, a SQLite schema, two importers, and offline tests. It is a data collection starter, not a finished AI application.

**No upstream flight, airport, fare, or weather files are included.** This workspace returned a network-policy denial for the four download checks. Provider documentation and published download links were researched separately. Run the collection commands on your computer with normal internet access. See `COLLECTION_STATUS.json` for the exact boundary.

Read `FINDINGS.md` for the recommended data stack, gaps and current release dates. Read `SOURCE_CATALOG.md` for the full source list; `catalog.json` has the same source metadata for code.

## Requirements

- Python 3.11 or newer. The scripts use the standard library; no AI service, paid API or account is needed for the included download jobs.
- Internet access to the selected provider hosts.
- A current system IANA time-zone database for optional airport time-zone validation. On systems without it, install Python's `tzdata` package separately or use a system that supplies it. Missing zones remain visibly unresolved.
- Free disk space for the selected files. Start with reference data and one month of flight history before collecting years of records.

Commands below use `python`. Some computers use `python3` or `py` instead. Open a terminal in this extracted folder.

## 1. Inspect the download plan

```sh
python flight_data.py plan
python flight_data.py plan --group all
```

The default selects 10 reference files. Other groups are `weather` (6 caches), `live` (2 feeds), `history` (one August 2026 reporting-carrier ZIP), and `fares` (4 July 2026 DB1C tables).

The plan is pinned to the releases identified on November 13, 2026 where dated URLs exist. Rolling files and live feeds return the values available when you run the script; they cannot recreate November 13 conditions later.

## 2. Collect public reference data

```sh
python flight_data.py fetch --group reference --out data
```

Each successful file is saved under `data/raw/` with a SHA-256 prefix. `data/current.json` maps job IDs to the newest successful local file. `data/downloads.jsonl` records successes and failures. Failed requests do not replace good files. No archive is extracted automatically.

The downloader uses HTTPS, validates response types, applies byte limits, streams files, and makes one request at a time. Defaults are 512 MiB per file and 2 GiB of successful saved files per run. Failed transfers can also use bandwidth before being rejected. An oversized file is reported as a failure. Raising a limit does not guarantee that your disk has space.

Use only one downloader process against a given output directory at a time.

## 3. Collect weather and airport disruptions

```sh
python flight_data.py fetch --group weather live --out data --user-agent "MyFlightProject/1.0"
```

These commands take a single snapshot. They do not create a scheduled task. For a deployed tool, add refresh jobs according to each provider's cache rules and the source timestamps. A downloaded file is not automatically fresh: check observation, issue and valid times inside the data. There is no weather or FAA-status parser in this starter yet.

## 4. Collect flight performance and historical fares

```sh
python flight_data.py fetch --group history --out data
python flight_data.py plan --group fares
python flight_data.py fetch --group fares --out data --max-file-mb 2048 --max-total-mb 8192
```

The fare command permits up to 8 GiB in one run; actual sizes were not measured here. Review the plan and available disk space first. The four DB1C file links were present on their official download pages. No DB1C parser is included: read their current definitions before joining or aggregating them.

For additional months of reporting-carrier history:

```sh
python flight_data.py plan-ontime --start 2024-01 --end 2026-08 --out ontime_jobs.json
python flight_data.py fetch --jobs ontime_jobs.json --group history --out data
```

This creates monthly URLs from the provider's published naming pattern. Each month's HTTP response still needs to succeed; the script reports missing or moved files. It caps the supported end month at the verified August 2026 release. Update the catalog and release check before extending that cutoff.

## 5. Build a small local database

Find the saved file paths in `data/current.json`. Supply those paths to the importers:

```sh
python flight_data.py import-airports --airports data/raw/HASH-airports.csv --timezones data/raw/OTHERHASH-airports.csv
python flight_data.py import-ontime --file data/raw/HASH-ontime_2026_08.zip
python flight_data.py route-stats --origin ORD --destination LAX
```

Replace `HASH` paths with actual downloaded paths. Use the `oa_airports` entry for `--airports` and `airport_timezones` for `--timezones`.

The airport importer keeps open facilities in `US`, `PR`, `VI`, `GU`, `MP`, `AS`, and `UM`; those codes remain available for separate filtering. `--states-only` restricts the country code to `US`. General-aviation airports remain in the table. `scheduled_service` is a helpful flag, not proof of current bookable service. The importer never invents a time zone when joins disagree.

The flight importer accepts **one complete national monthly reporting-carrier export**. Re-importing a month replaces that month's prior records in a transaction. Do not import a carrier-filtered or airport-filtered file into this database: it would replace the complete month with that subset. Keep filtered experiments in a different database with `--db`.

`route-stats` reports observed historical flights, cancellation rate and arrival-delay statistics. Delay metrics exclude cancelled and diverted flights; the number with usable delay labels is reported separately. No recommendation, future availability, connection guarantee, or price prediction is produced.

## 6. Add the important manual exports

1. Open the BTS T-100 Domestic Segment (All Carriers) source from `SOURCE_CATALOG.md`. Select the required year/months and all relevant fields. Export the file and record its release date. Use the service-class lookup to select scheduled passenger service. Keep cargo and other service classes separate.
2. Export BTS airport history and carrier lookup tables. Join stable DOT identifiers, retaining effective dates.
3. Select GHCNh station/year files after mapping airport coordinates and station IDs. Retain weather quality flags. The full global archive is unnecessary for an initial flight tool.
4. Export DOT EAS and FAA airport-boardings tables to check small-airport coverage.
5. Add the relevant DB1C definitions alongside the four data tables. A DB1C Segment file alone omits coupons with no intermediate via point; read `FINDINGS.md` before building a nonstop graph.

These sources are cataloged but do not yet have importers in this starter. Keep originals and checksums; add separate tables rather than forcing different record types into `flight_history`.

## Test the supplied code

```sh
python -m unittest -v
```

Tests use clearly synthetic rows and mocked responses. They check byte limits, partial/HTML response rejection, airport filtering, missing time zones, month replacement and transaction rollback. They do not establish that external hosts are reachable or that current source files still match their published schemas.

## Files

| File | Purpose |
| --- | --- |
| `FINDINGS.md` | Recommended data stack, gaps and development guidance |
| `SOURCE_CATALOG.md` | Source descriptions and official links |
| `catalog.json` | Machine-readable source metadata |
| `download_jobs.json` | Reviewed public file URLs, grouped by use |
| `flight_data.py` | Plan, download, import, and historical summary commands |
| `schema.sql` | Local database tables |
| `test_flight_data.py` | Offline code tests with synthetic inputs |
| `COLLECTION_STATUS.json` | Research and collection status |
| `VALIDATION.md` | Checks performed and their limits |

Code and documentation in this package may be used and modified for your project. Source data keeps the provider's terms. The downloader does not create accounts, accept agreements, purchase data, or collect private passenger records.
