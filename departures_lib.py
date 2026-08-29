"""
Shared logic used by both the command-line tool and the web backend.

This is the same logic from live_departures.py and find_stop.py, just
pulled into reusable functions instead of being duplicated.
"""

import os
import sqlite3
import time
from datetime import date, datetime
from zoneinfo import ZoneInfo

import requests
from google.transit import gtfs_realtime_pb2

# Ireland's timezone - handles the IST (UTC+1, summer) / GMT (UTC+0, winter)
# switch automatically. We use this instead of the server's system clock,
# because cloud servers (like Render) run in UTC regardless of where the
# app's users actually are.
IE_TZ = ZoneInfo("Europe/Dublin")

TRIP_UPDATES_URL = "https://api.nationaltransport.ie/gtfsr/v2/TripUpdates"
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "punctuality.sqlite3")

WEEKDAY_COLUMNS = ["monday", "tuesday", "wednesday", "thursday",
                   "friday", "saturday", "sunday"]

# --- Shared live-feed cache -----------------------------------------------
# NTA's fair usage policy caps each API token at 1 call per 60 seconds.
# Once this app has more than one visitor, each visitor loading a board
# must NOT trigger its own API call - they all have to share one. This
# simple in-memory cache does that: whoever asks first after 60 seconds
# have passed triggers the real call; everyone else in that window gets
# the same cached result.
_live_cache = {"delays": {}, "fetched_at": 0}
_CACHE_TTL_SECONDS = 60


def get_cached_live_delays(api_key: str):
    """Returns the delays dict, only calling the real API if the cached
    copy is older than the TTL. Shared across all requests/users."""
    now = time.time()
    if now - _live_cache["fetched_at"] < _CACHE_TTL_SECONDS:
        return _live_cache["delays"]

    delays = fetch_live_delays(api_key)
    _live_cache["delays"] = delays
    _live_cache["fetched_at"] = now
    return delays


def get_connection():
    return sqlite3.connect(DB_PATH)


def search_stops(conn, search_term: str, limit: int = 20):
    rows = conn.execute(
        """
        SELECT stop_id, stop_name, stop_lat, stop_lon
        FROM stops
        WHERE stop_name LIKE ?
        ORDER BY stop_name
        LIMIT ?
        """,
        (f"%{search_term}%", limit),
    ).fetchall()
    return [
        {"stop_id": r[0], "stop_name": r[1], "lat": r[2], "lon": r[3]}
        for r in rows
    ]


def gtfs_time_to_seconds(time_str: str) -> int:
    h, m, s = (int(part) for part in time_str.split(":"))
    return h * 3600 + m * 60 + s


def get_active_service_ids(conn, target_date: date) -> set:
    date_str = target_date.strftime("%Y%m%d")
    weekday_col = WEEKDAY_COLUMNS[target_date.weekday()]

    rows = conn.execute(
        f"""
        SELECT service_id FROM calendar
        WHERE {weekday_col} = '1'
          AND start_date <= ?
          AND end_date >= ?
        """,
        (date_str, date_str),
    ).fetchall()
    service_ids = {row[0] for row in rows}

    exception_rows = conn.execute(
        "SELECT service_id, exception_type FROM calendar_dates WHERE date = ?",
        (date_str,),
    ).fetchall()

    for service_id, exception_type in exception_rows:
        if exception_type == "1":
            service_ids.add(service_id)
        elif exception_type == "2":
            service_ids.discard(service_id)

    return service_ids


def get_scheduled_departures(conn, stop_id: str, service_ids: set):
    if not service_ids:
        return []

    placeholders = ",".join("?" for _ in service_ids)
    query = f"""
        SELECT
            st.trip_id,
            st.arrival_time,
            t.route_id,
            t.trip_headsign,
            r.route_short_name,
            r.route_long_name
        FROM stop_times st
        JOIN trips t ON t.trip_id = st.trip_id
        JOIN routes r ON r.route_id = t.route_id
        WHERE st.stop_id = ?
          AND t.service_id IN ({placeholders})
    """
    return conn.execute(query, (stop_id, *service_ids)).fetchall()


def fetch_live_delays(api_key: str):
    response = requests.get(TRIP_UPDATES_URL, headers={"x-api-key": api_key}, timeout=15)
    response.raise_for_status()

    feed = gtfs_realtime_pb2.FeedMessage()
    feed.ParseFromString(response.content)

    delays = {}
    for entity in feed.entity:
        trip_id = entity.trip_update.trip.trip_id
        for stu in entity.trip_update.stop_time_update:
            if stu.HasField("arrival"):
                delays[(trip_id, stu.stop_id)] = stu.arrival.delay
            elif stu.HasField("departure"):
                delays[(trip_id, stu.stop_id)] = stu.departure.delay
    return delays


def get_live_board(conn, stop_id: str, api_key: str, lookahead_minutes: int = 90):
    """Returns a ready-to-serialize list of upcoming departures for a stop,
    combining today's schedule with live delay data."""
    today = datetime.now(IE_TZ).date()
    now = datetime.now(IE_TZ)
    now_seconds = now.hour * 3600 + now.minute * 60 + now.second

    service_ids = get_active_service_ids(conn, today)
    scheduled = get_scheduled_departures(conn, stop_id, service_ids)

    try:
        live_delays = get_cached_live_delays(api_key)
        live_data_available = True
    except requests.exceptions.RequestException:
        live_delays = {}
        live_data_available = False

    lookahead_seconds = lookahead_minutes * 60
    upcoming = []

    for trip_id, arrival_time, route_id, headsign, short_name, long_name in scheduled:
        scheduled_seconds = gtfs_time_to_seconds(arrival_time)

        if scheduled_seconds < now_seconds:
            continue
        if scheduled_seconds > now_seconds + lookahead_seconds:
            continue

        delay_seconds = live_delays.get((trip_id, stop_id))
        expected_seconds = scheduled_seconds + (delay_seconds or 0)
        route_display = short_name or long_name or route_id

        sched_h, sched_m = divmod(scheduled_seconds // 60, 60)

        upcoming.append({
            "route": route_display,
            "headsign": headsign or "",
            "scheduled_time": f"{sched_h:02d}:{sched_m:02d}",
            "due_in_minutes": round((expected_seconds - now_seconds) / 60),
            "delay_minutes": round(delay_seconds / 60) if delay_seconds is not None else None,
            "has_live_data": delay_seconds is not None,
        })

    upcoming.sort(key=lambda d: d["due_in_minutes"])

    return {
        "upcoming": upcoming,
        "live_data_available": live_data_available,
    }
