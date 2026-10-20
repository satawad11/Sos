"""Small geo helpers (no external services: coordinates never leave the server except in LINE links the staff open)."""
from __future__ import annotations
from math import asin, cos, radians, sin, sqrt


def valid_latlon(lat, lon) -> bool:
    if isinstance(lat, bool) or isinstance(lon, bool) or not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)):
        return False
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return False
    return not (abs(lat) < 0.001 and abs(lon) < 0.001)          # (0,0) "null island" = a GPS that has no fix


def distance_m(lat1, lon1, lat2, lon2) -> float:
    """Great-circle distance in metres (haversine)."""
    p1, p2 = radians(lat1), radians(lat2)
    a = sin((p2 - p1) / 2) ** 2 + cos(p1) * cos(p2) * sin(radians(lon2 - lon1) / 2) ** 2
    return 2 * 6371008.8 * asin(sqrt(a))


def maps_url(lat: float, lon: float) -> str:
    return f"https://maps.google.com/?q={lat:.6f},{lon:.6f}"
