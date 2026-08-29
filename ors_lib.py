"""
Walking route/time lookup using OpenRouteService (openrouteservice.org).

Free tier: 2,000 requests/day, 40/minute - plenty for personal use, but
we cache results briefly anyway since a user's location and a stop's
location rarely change second to second.
"""

import time

import requests

ORS_URL = "https://api.openrouteservice.org/v2/directions/foot-walking"

# Cache walk times for a few minutes - keyed on rounded coordinates so
# minor GPS jitter doesn't trigger a fresh API call every time.
_walk_time_cache = {}
_CACHE_TTL_SECONDS = 5 * 60


def _cache_key(from_lat, from_lon, to_lat, to_lon):
    # Rounding to 4 decimal places is roughly ~11 metres of precision -
    # tight enough to be accurate, loose enough to absorb GPS noise.
    return (
        round(from_lat, 4), round(from_lon, 4),
        round(to_lat, 4), round(to_lon, 4),
    )


def get_walk_time_minutes(api_key, from_lat, from_lon, to_lat, to_lon):
    """Returns walking time in minutes (float) via a real street route,
    or None if the routing service couldn't be reached or found no route."""
    key = _cache_key(from_lat, from_lon, to_lat, to_lon)
    cached = _walk_time_cache.get(key)
    if cached and (time.time() - cached["fetched_at"]) < _CACHE_TTL_SECONDS:
        return cached["minutes"]

    try:
        response = requests.post(
            ORS_URL,
            headers={
                "Authorization": api_key,
                "Content-Type": "application/json",
            },
            json={
                # ORS wants [longitude, latitude] order - easy to mix up
                "coordinates": [[from_lon, from_lat], [to_lon, to_lat]]
            },
            timeout=10,
        )
        response.raise_for_status()
        data = response.json()
        duration_seconds = data["routes"][0]["summary"]["duration"]
        minutes = duration_seconds / 60.0
    except (requests.exceptions.RequestException, KeyError, IndexError):
        return None

    _walk_time_cache[key] = {"minutes": minutes, "fetched_at": time.time()}
    return minutes
