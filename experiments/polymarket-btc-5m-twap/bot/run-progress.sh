#!/usr/bin/env bash
# Re-run the live strategy sweep every 20 minutes; keep a timestamped copy of each report.
cd "$(dirname "$0")"
mkdir -p data/progress
while true; do
  python3 -I progress.py 20 > /dev/null 2>> data/progress.err && cp data/progress.md "data/progress/$(date -u +%Y%m%dT%H%MZ).md"
  sleep 1200
done
