"""Geocoding, routing, and pothole lookup along a route.

All SQL in this module is built by our own code from validated numbers. The
language model never writes it, so nothing here can be steered by a prompt.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import requests

from .tools import CARTO_URL, TIMEOUT_SECONDS

CENSUS_URL = "https://geocoding.geo.census.gov/geocoder/locations/onelineaddress"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
OSRM_URL = "https://router.project-osrm.org/route/v1/driving"
USER_AGENT = "philly-pothole-agent/0.1 (hackathon project)"

# Rough bounding box around Philadelphia: (min_lat, max_lat, min_lon, max_lon)
PHILLY_BOUNDS = (39.85, 40.15, -75.30, -74.94)
MAX_ADDRESS_CHARS = 200
MAX_ROUTE_POINTS = 1500
MIN_BUFFER_M, MAX_BUFFER_M, DEFAULT_BUFFER_M = 10, 100, 30
LOOKBACK_DAYS = 365
METERS_PER_MILE = 1609.344


class RoutingError(ValueError):
    """A problem the user can fix, such as an address we could not find."""


@dataclass
class Route:
    route_id: str
    miles: float
    minutes: float
    coordinates: list[list[float]]  # [lon, lat] pairs, GeoJSON order
    potholes: list[dict[str, Any]] | None = field(default=None)

    def summary(self) -> dict[str, Any]:
        """Small description that is safe to hand to the model (no geometry)."""
        return {"route_id": self.route_id, "miles": self.miles, "minutes": self.minutes}


def in_philadelphia(lat: float, lon: float) -> bool:
    min_lat, max_lat, min_lon, max_lon = PHILLY_BOUNDS
    return min_lat <= lat <= max_lat and min_lon <= lon <= max_lon


def _clean_address(address: str) -> str:
    if not isinstance(address, str) or not address.strip():
        raise RoutingError("Enter an address.")
    address = " ".join(address.split())[:MAX_ADDRESS_CHARS]
    if "philadelphia" not in address.lower() and "phila" not in address.lower():
        address += ", Philadelphia, PA"
    return address


def _geocode_census(address: str) -> dict[str, Any] | None:
    params = {"address": address, "benchmark": "Public_AR_Current", "format": "json"}
    response = requests.get(CENSUS_URL, params=params, timeout=TIMEOUT_SECONDS)
    matches = response.json().get("result", {}).get("addressMatches", [])
    if not matches:
        return None
    best = matches[0]
    return {
        "lat": float(best["coordinates"]["y"]),
        "lon": float(best["coordinates"]["x"]),
        "matched_address": best["matchedAddress"],
        "source": "US Census geocoder",
    }


def _geocode_nominatim(address: str) -> dict[str, Any] | None:
    params = {"q": address, "format": "json", "limit": 1, "countrycodes": "us"}
    headers = {"User-Agent": USER_AGENT}
    response = requests.get(NOMINATIM_URL, params=params, headers=headers, timeout=TIMEOUT_SECONDS)
    results = response.json()
    if not results:
        return None
    best = results[0]
    return {
        "lat": float(best["lat"]),
        "lon": float(best["lon"]),
        "matched_address": best["display_name"],
        "source": "OpenStreetMap Nominatim",
    }


def geocode(address: str) -> dict[str, Any]:
    """Turn an address or place name into coordinates inside Philadelphia."""
    cleaned = _clean_address(address)
    for lookup in (_geocode_census, _geocode_nominatim):
        try:
            found = lookup(cleaned)
        except (requests.RequestException, ValueError, KeyError):
            found = None
        if found and in_philadelphia(found["lat"], found["lon"]):
            return found
    raise RoutingError(
        f"Could not find '{address}' in Philadelphia. Try a street address ('1500 Market St'), "
        "an intersection ('Broad St & Oregon Ave'), or a landmark ('30th Street Station')."
    )


def _check_point(lat: float, lon: float) -> tuple[float, float]:
    lat, lon = float(lat), float(lon)
    if not in_philadelphia(lat, lon):
        raise RoutingError("This tool only covers trips inside Philadelphia.")
    return lat, lon


def fetch_routes(start_lat: float, start_lon: float, end_lat: float, end_lon: float) -> list[Route]:
    """Ask OSRM for the main driving route plus alternatives."""
    start_lat, start_lon = _check_point(start_lat, start_lon)
    end_lat, end_lon = _check_point(end_lat, end_lon)
    url = f"{OSRM_URL}/{start_lon:.6f},{start_lat:.6f};{end_lon:.6f},{end_lat:.6f}"
    params = {"overview": "full", "geometries": "geojson", "alternatives": "3"}
    response = requests.get(
        url, params=params, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT_SECONDS
    )
    payload = response.json()
    if payload.get("code") != "Ok" or not payload.get("routes"):
        raise RoutingError("No driving route found between those two places.")
    routes = []
    for index, raw in enumerate(payload["routes"]):
        routes.append(
            Route(
                route_id="ABCD"[index] if index < 4 else str(index),
                miles=round(raw["distance"] / METERS_PER_MILE, 1),
                minutes=round(raw["duration"] / 60),
                coordinates=raw["geometry"]["coordinates"],
            )
        )
    return routes


def _thin(coordinates: list[list[float]]) -> list[list[float]]:
    """Keep very long routes under MAX_ROUTE_POINTS so the query stays small."""
    if len(coordinates) <= MAX_ROUTE_POINTS:
        return coordinates
    stride = len(coordinates) // MAX_ROUTE_POINTS + 1
    thinned = coordinates[::stride]
    if thinned[-1] != coordinates[-1]:
        thinned.append(coordinates[-1])
    return thinned


def build_pothole_sql(coordinates: list[list[float]], buffer_m: int = DEFAULT_BUFFER_M) -> str:
    """Build the PostGIS query. Every interpolated value is a validated number."""
    if len(coordinates) < 2:
        raise RoutingError("A route needs at least two points.")
    buffer_m = max(MIN_BUFFER_M, min(MAX_BUFFER_M, int(buffer_m)))
    safe = [[round(float(lon), 5), round(float(lat), 5)] for lon, lat in _thin(coordinates)]
    line_json = json.dumps({"type": "LineString", "coordinates": safe}, separators=(",", ":"))
    line = f"ST_SetSRID(ST_GeomFromGeoJSON('{line_json}'), 4326)"
    return (
        "SELECT service_request_id, address, requested_datetime, lat, lon, "
        f"ROUND(ST_Distance(the_geom::geography, {line}::geography)::numeric, 1) AS meters_from_route, "
        f"ROUND(ST_LineLocatePoint({line}, the_geom)::numeric, 4) AS position "
        "FROM public_cases_fc "
        "WHERE service_name = 'Street Defect' AND status = 'Open' "
        f"AND requested_datetime >= NOW() - INTERVAL '{LOOKBACK_DAYS} days' "
        "AND the_geom IS NOT NULL "
        f"AND ST_DWithin(the_geom::geography, {line}::geography, {buffer_m}) "
        "ORDER BY position LIMIT 300"
    )


def build_bbox_sql(south: float, west: float, north: float, east: float, limit: int = 1500) -> str:
    """Open-report query for a map viewport. Every interpolated value is a validated number,
    clamped to the Philadelphia bounding box."""
    min_lat, max_lat, min_lon, max_lon = PHILLY_BOUNDS
    south = max(min_lat, min(max_lat, float(south)))
    north = max(min_lat, min(max_lat, float(north)))
    west = max(min_lon, min(max_lon, float(west)))
    east = max(min_lon, min(max_lon, float(east)))
    if south > north or west > east:
        raise RoutingError("Bounding box is inverted.")
    limit = max(1, min(2000, int(limit)))
    return (
        "SELECT service_request_id, address, requested_datetime, lat, lon "
        "FROM public_cases_fc "
        "WHERE service_name = 'Street Defect' AND status = 'Open' "
        f"AND requested_datetime >= NOW() - INTERVAL '{LOOKBACK_DAYS} days' "
        "AND the_geom IS NOT NULL "
        f"AND lat BETWEEN {round(south, 5)} AND {round(north, 5)} "
        f"AND lon BETWEEN {round(west, 5)} AND {round(east, 5)} "
        f"ORDER BY requested_datetime DESC LIMIT {limit}"
    )


def potholes_in_bbox(south: float, west: float, north: float, east: float) -> list[dict[str, Any]]:
    """All open 'Street Defect' reports inside a map viewport, newest first."""
    sql = build_bbox_sql(south, west, north, east)
    response = requests.post(CARTO_URL, data={"q": sql}, timeout=TIMEOUT_SECONDS)
    payload = response.json()
    if response.status_code != 200 or "error" in payload:
        raise RuntimeError(f"City data error: {payload.get('error', response.text[:200])}")
    now = datetime.now(timezone.utc)
    potholes = []
    for row in payload.get("rows", []):
        reported = datetime.fromisoformat(row["requested_datetime"].replace("Z", "+00:00"))
        potholes.append(
            {
                "id": row["service_request_id"],
                "address": row["address"] or "Unknown address",
                "lat": row["lat"],
                "lon": row["lon"],
                "reported": reported.date().isoformat(),
                "days_open": (now - reported).days,
            }
        )
    return potholes


def potholes_along(route: Route, buffer_m: int = DEFAULT_BUFFER_M) -> list[dict[str, Any]]:
    """Open 'Street Defect' reports within buffer_m meters of the route, in driving order."""
    sql = build_pothole_sql(route.coordinates, buffer_m)
    response = requests.post(CARTO_URL, data={"q": sql}, timeout=TIMEOUT_SECONDS)
    payload = response.json()
    if response.status_code != 200 or "error" in payload:
        raise RuntimeError(f"City data error: {payload.get('error', response.text[:200])}")
    now = datetime.now(timezone.utc)
    potholes = []
    for row in payload.get("rows", []):
        reported = datetime.fromisoformat(row["requested_datetime"].replace("Z", "+00:00"))
        potholes.append(
            {
                "id": row["service_request_id"],
                "address": row["address"] or "Unknown address",
                "lat": row["lat"],
                "lon": row["lon"],
                "reported": reported.date().isoformat(),
                "days_open": (now - reported).days,
                "meters_from_route": row["meters_from_route"],
                "mile_marker": round(float(row["position"]) * route.miles, 1),
            }
        )
    return potholes
