#!/usr/bin/env python3
"""Rebuild flightopt/data/us_airports.csv from OurAirports and airportsdata CSV downloads.

Usage: python tools/build_airports.py OURAIRPORTS_CSV AIRPORTSDATA_CSV OUT_CSV
Equivalent CLI: flightopt init --ourairports X --airportsdata Y
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from flightopt.airports import build_airport_file  # noqa: E402

if __name__ == '__main__':
    if len(sys.argv) != 4:
        raise SystemExit(__doc__)
    print(f'{build_airport_file(*sys.argv[1:])} airports written to {sys.argv[3]}')
