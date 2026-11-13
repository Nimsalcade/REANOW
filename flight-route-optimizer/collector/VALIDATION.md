# Validation record

Date: November 13, 2026.

- 15 offline Python tests passed with Python 3.12.14.
- Catalog structure checked: 39 unique source entries; 23 download jobs; every job points to a catalog source; all download URLs use HTTPS.
- Default plan contains 10 reference jobs. The full plan contains 6 weather jobs, 2 live jobs, 1 history job and 4 fare jobs in addition to the reference jobs.
- Download tests covered successful file hashes, size limits, incomplete bodies, HTML error pages, denied requests, long retry instructions, HTTPS requirements, and preservation of the previous good download index after failure.
- Import tests covered revised-month replacement, repeated-month imports without duplicate totals, rollback after invalid rows, rejection of mixed-month files, cancellation/delay denominators, territory filtering, closed airports and missing time zones.
- The final ZIP was checked for readable entries and included-file hashes. Temporary files, bytecode, helper scripts and test databases are excluded.

The HTTP responses and flight/airport rows used in tests are synthetic. No real upstream bulk data was downloaded. Live reachability, actual source-file schemas and model accuracy were not validated. Four direct source checks received HTTP 403 responses from workspace network policy. The public source descriptions were researched through provider documentation and download pages.

The project includes no trained model or future-flight feed. The empty `future_flights` schema is an integration point, not populated schedule data. Additional importers are required for T-100, DB1C, weather and disruption messages.
