"""Geodesy helpers."""
from __future__ import annotations

import math

EARTH_RADIUS_MI = 3958.7613


def great_circle_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_MI * math.asin(min(1.0, math.sqrt(a)))


def estimated_drive_minutes(miles: float) -> int:
    """Rough ground-access estimate: road distance ~1.25x straight line at 45 mph average.

    This is a labeled planning estimate, not a routing-engine result.
    """
    return int(round(miles * 1.25 / 45 * 60))
