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
_live_cache = {"data": {"stops": {}, "cancelled_trips": set()}, "fetched_at": 0}
_CACHE_TTL_SECONDS = 60


def get_cached_live_delays(api_key: str):
    """Returns the live data dict, only calling the real API if the cached
    copy is older than the TTL. Shared across all requests/users."""
    now = time.time()
    if now - _live_cache["fetched_at"] < _CACHE_TTL_SECONDS:
        return _live_cache["data"]

    data = fetch_live_delays(api_key)
    _live_cache["data"] = data
    _live_cache["fetched_at"] = now
    return data


def get_connection():
    return sqlite3.connect(DB_PATH)


_stops_columns_cache = None


def _get_stops_columns(conn):
    """Returns the set of column names actually present in the stops
    table. Cached per-process since the schema only changes when
    setup_static_data.py rebuilds the database (i.e. on deploy)."""
    global _stops_columns_cache
    if _stops_columns_cache is None:
        rows = conn.execute("PRAGMA table_info(stops)").fetchall()
        _stops_columns_cache = {row[1] for row in rows}  # row[1] = column name
    return _stops_columns_cache


def has_stop_code(conn) -> bool:
    """stop_code is an optional GTFS field - the short number printed on
    the physical sign at the stop (different from stop_id, which is an
    internal identifier). Not every agency populates it, so we check
    before relying on it."""
    return "stop_code" in _get_stops_columns(conn)


# --- Usage analytics --------------------------------------------------
# Kept in a SEPARATE database file from the schedule data, because
# setup_static_data.py wipes and rebuilds punctuality.sqlite3 on every
# deploy - we don't want that to erase your usage history too.
ANALYTICS_DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "analytics.sqlite3")


def get_analytics_connection():
    conn = sqlite3.connect(ANALYTICS_DB_PATH)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS app_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type TEXT NOT NULL,
            stop_id TEXT,
            stop_name TEXT,
            visitor_id TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_events_type ON app_events(event_type)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_events_stop ON app_events(stop_id)")

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS feedback (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            message TEXT NOT NULL,
            stop_id TEXT,
            stop_name TEXT,
            visitor_id TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.commit()
    return conn


def save_feedback(message, visitor_id, stop_id=None, stop_name=None):
    conn = get_analytics_connection()
    try:
        conn.execute(
            """
            INSERT INTO feedback (message, stop_id, stop_name, visitor_id, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (message, stop_id, stop_name, visitor_id,
             datetime.now(IE_TZ).isoformat(timespec="seconds")),
        )
        conn.commit()
    finally:
        conn.close()


def get_recent_feedback(limit=50):
    conn = get_analytics_connection()
    try:
        rows = conn.execute(
            """
            SELECT message, stop_name, created_at
            FROM feedback
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    finally:
        conn.close()
    return [{"message": r[0], "stop_name": r[1], "created_at": r[2]} for r in rows]


def log_event(event_type, stop_id=None, stop_name=None, visitor_id=None):
    conn = get_analytics_connection()
    try:
        conn.execute(
            """
            INSERT INTO app_events (event_type, stop_id, stop_name, visitor_id, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (event_type, stop_id, stop_name, visitor_id,
             datetime.now(IE_TZ).isoformat(timespec="seconds")),
        )
        conn.commit()
    finally:
        conn.close()


def get_stats_summary():
    """Returns a dict of headline usage numbers, the most-viewed stops,
    and a list of stop locations (for mapping) with their view counts."""
    conn = get_analytics_connection()
    try:
        total_page_views = conn.execute(
            "SELECT COUNT(*) FROM app_events WHERE event_type = 'page_view'"
        ).fetchone()[0]

        unique_visitors = conn.execute(
            "SELECT COUNT(DISTINCT visitor_id) FROM app_events"
        ).fetchone()[0]

        total_stop_views = conn.execute(
            "SELECT COUNT(*) FROM app_events WHERE event_type = 'stop_view'"
        ).fetchone()[0]

        top_stops = conn.execute(
            """
            SELECT stop_name, stop_id, COUNT(*) AS views, COUNT(DISTINCT visitor_id) AS visitors
            FROM app_events
            WHERE event_type = 'stop_view'
            GROUP BY stop_id
            ORDER BY views DESC
            LIMIT 15
            """
        ).fetchall()

        # Every distinct stop ever viewed, for the map (capped generously)
        all_stop_counts = conn.execute(
            """
            SELECT stop_id, stop_name, COUNT(*) AS views
            FROM app_events
            WHERE event_type = 'stop_view'
            GROUP BY stop_id
            ORDER BY views DESC
            LIMIT 300
            """
        ).fetchall()

        last_7_days_views = conn.execute(
            """
            SELECT substr(created_at, 1, 10) AS day, COUNT(*) AS views
            FROM app_events
            WHERE event_type = 'page_view'
              AND created_at >= datetime('now', '-7 days')
            GROUP BY day
            ORDER BY day
            """
        ).fetchall()

    finally:
        conn.close()

    # Cross-reference with the schedule database to get each stop's
    # coordinates for plotting on the map - this reuses data already
    # collected for the app's core feature (stop lookup), nothing new.
    stop_locations = []
    if all_stop_counts:
        schedule_conn = get_connection()
        try:
            for stop_id, stop_name, views in all_stop_counts:
                row = schedule_conn.execute(
                    "SELECT stop_lat, stop_lon FROM stops WHERE stop_id = ?", (stop_id,)
                ).fetchone()
                if row and row[0] is not None:
                    stop_locations.append({
                        "stop_id": stop_id, "stop_name": stop_name,
                        "lat": float(row[0]), "lon": float(row[1]), "views": views,
                    })
        finally:
            schedule_conn.close()

    return {
        "total_page_views": total_page_views,
        "unique_visitors": unique_visitors,
        "total_stop_views": total_stop_views,
        "top_stops": [
            {"stop_name": r[0], "stop_id": r[1], "views": r[2], "visitors": r[3]}
            for r in top_stops
        ],
        "stop_locations": stop_locations,
        "last_7_days_views": [{"day": r[0], "views": r[1]} for r in last_7_days_views],
    }



def search_stops(conn, search_term: str, limit: int = 20):
    if has_stop_code(conn):
        rows = conn.execute(
            """
            SELECT stop_id, stop_name, stop_lat, stop_lon, stop_code
            FROM stops
            WHERE stop_name LIKE ? OR stop_code LIKE ?
            ORDER BY stop_name
            LIMIT ?
            """,
            (f"%{search_term}%", f"%{search_term}%", limit),
        ).fetchall()
        return [
            {
                "stop_id": r[0], "stop_name": r[1], "lat": r[2], "lon": r[3],
                "stop_code": r[4],
                "direction": get_stop_direction(conn, r[0]),
            }
            for r in rows
        ]

    # Fallback for schedule data that doesn't include stop_code at all
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
        {
            "stop_id": r[0], "stop_name": r[1], "lat": r[2], "lon": r[3],
            "stop_code": None,
            "direction": get_stop_direction(conn, r[0]),
        }
        for r in rows
    ]


def get_stop_direction(conn, stop_id: str):
    """Returns the most common destination (trip_headsign) served by this
    stop_id - e.g. 'Sligo'. Two stop_ids at the same physical location
    (opposite sides of the road) usually serve opposite directions, so
    this is what actually distinguishes them for a rider, not the ID."""
    row = conn.execute(
        """
        SELECT t.trip_headsign, COUNT(*) AS n
        FROM stop_times st
        JOIN trips t ON t.trip_id = st.trip_id
        WHERE st.stop_id = ? AND t.trip_headsign IS NOT NULL AND t.trip_headsign != ''
        GROUP BY t.trip_headsign
        ORDER BY n DESC
        LIMIT 1
        """,
        (stop_id,),
    ).fetchone()
    return row[0] if row else None


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
    """Returns a dict keyed by (trip_id, stop_id) with delay/cancellation
    info for every stop update in the current live feed, plus a set of
    trip_ids that are entirely cancelled (which may report no individual
    stop updates at all, so they need tracking separately)."""
    response = requests.get(TRIP_UPDATES_URL, headers={"x-api-key": api_key}, timeout=15)
    response.raise_for_status()

    feed = gtfs_realtime_pb2.FeedMessage()
    feed.ParseFromString(response.content)

    CANCELED = gtfs_realtime_pb2.TripDescriptor.CANCELED
    SKIPPED = gtfs_realtime_pb2.TripUpdate.StopTimeUpdate.SKIPPED

    stop_info = {}
    cancelled_trip_ids = set()

    for entity in feed.entity:
        trip_id = entity.trip_update.trip.trip_id
        trip_cancelled = entity.trip_update.trip.schedule_relationship == CANCELED

        if trip_cancelled:
            cancelled_trip_ids.add(trip_id)

        for stu in entity.trip_update.stop_time_update:
            stop_skipped = stu.schedule_relationship == SKIPPED

            if stu.HasField("arrival"):
                delay = stu.arrival.delay
            elif stu.HasField("departure"):
                delay = stu.departure.delay
            else:
                delay = None

            stop_info[(trip_id, stu.stop_id)] = {
                "delay": delay,
                "cancelled": trip_cancelled or stop_skipped,
            }

    return {"stops": stop_info, "cancelled_trips": cancelled_trip_ids}


def get_live_board(conn, stop_id: str, api_key: str, lookahead_minutes: int = 90):
    """Returns a ready-to-serialize list of upcoming departures for a stop,
    combining today's schedule with live delay and cancellation data."""
    today = datetime.now(IE_TZ).date()
    now = datetime.now(IE_TZ)
    now_seconds = now.hour * 3600 + now.minute * 60 + now.second

    service_ids = get_active_service_ids(conn, today)
    scheduled = get_scheduled_departures(conn, stop_id, service_ids)

    try:
        live_data = get_cached_live_delays(api_key)
        live_data_available = True
    except requests.exceptions.RequestException:
        live_data = {"stops": {}, "cancelled_trips": set()}
        live_data_available = False

    stop_info = live_data["stops"]
    cancelled_trip_ids = live_data["cancelled_trips"]

    lookahead_seconds = lookahead_minutes * 60
    upcoming = []

    for trip_id, arrival_time, route_id, headsign, short_name, long_name in scheduled:
        scheduled_seconds = gtfs_time_to_seconds(arrival_time)

        if scheduled_seconds < now_seconds:
            continue
        if scheduled_seconds > now_seconds + lookahead_seconds:
            continue

        info = stop_info.get((trip_id, stop_id))
        delay_seconds = info["delay"] if info else None
        is_cancelled = (trip_id in cancelled_trip_ids) or (info["cancelled"] if info else False)

        expected_seconds = scheduled_seconds + (delay_seconds or 0)
        route_display = short_name or long_name or route_id

        sched_h, sched_m = divmod(scheduled_seconds // 60, 60)

        upcoming.append({
            "route": route_display,
            "headsign": headsign or "",
            "scheduled_time": f"{sched_h:02d}:{sched_m:02d}",
            "due_in_minutes": round((expected_seconds - now_seconds) / 60),
            "delay_minutes": round(delay_seconds / 60) if delay_seconds is not None else None,
            "has_live_data": delay_seconds is not None or is_cancelled,
            "cancelled": is_cancelled,
        })

    # Cancelled services float to the top under their scheduled slot rather
    # than by "due in" time, since that number is meaningless for them
    upcoming.sort(key=lambda d: d["due_in_minutes"])

    return {
        "upcoming": upcoming,
        "live_data_available": live_data_available,
    }
