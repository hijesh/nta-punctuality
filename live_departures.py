"""
Live departures board for a single stop.

Combines:
  - the static schedule (which trips are due at this stop today, and when)
  - the live realtime feed (how delayed each of those trips actually is)

to produce a real "next buses from this stop" board.

Run it with:
    python live_departures.py <stop_id>

Example:
    python live_departures.py 7050B630091
"""

import os
import sqlite3
import sys
from datetime import datetime, date, timedelta

import requests
from dotenv import load_dotenv
from google.transit import gtfs_realtime_pb2

load_dotenv()

API_KEY = os.environ.get("NTA_API_KEY")
TRIP_UPDATES_URL = "https://api.nationaltransport.ie/gtfsr/v2/TripUpdates"
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "punctuality.sqlite3")

# How far ahead to show departures
LOOKAHEAD_MINUTES = 90

WEEKDAY_COLUMNS = ["monday", "tuesday", "wednesday", "thursday",
                   "friday", "saturday", "sunday"]


def gtfs_time_to_seconds(time_str: str) -> int:
    """GTFS times can exceed 24:00:00 (e.g. 25:10:00 for a bus that departs
    just after midnight but still belongs to 'yesterday's' service day).
    Convert 'HH:MM:SS' into total seconds since midnight, however large."""
    h, m, s = (int(part) for part in time_str.split(":"))
    return h * 3600 + m * 60 + s


def get_active_service_ids(conn, target_date: date) -> set:
    """Works out which service_ids actually run on the given date, using
    calendar.txt (regular weekly pattern) plus calendar_dates.txt
    (exceptions - added or removed single-day services)."""
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

    # Apply exceptions: type 1 = service added for this date,
    # type 2 = service removed for this date
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
    """Returns every scheduled stop at this stop_id today, for trips whose
    service_id is in the active set, with route info attached."""
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


def fetch_live_delays():
    """Fetches one live poll and returns a dict mapping
    (trip_id, stop_id) -> delay_in_seconds for every stop update reported."""
    response = requests.get(TRIP_UPDATES_URL, headers={"x-api-key": API_KEY}, timeout=15)
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


def main():
    if len(sys.argv) < 2:
        print("Usage: python live_departures.py <stop_id>")
        print("Tip: use find_stop.py <name> to look up a stop_id first.")
        sys.exit(1)

    stop_id = sys.argv[1]

    if not API_KEY:
        print("ERROR: NTA_API_KEY not found in .env file.")
        sys.exit(1)

    if not os.path.exists(DB_PATH):
        print("ERROR: No database found. Run setup_static_data.py first.")
        sys.exit(1)

    conn = sqlite3.connect(DB_PATH)

    today = date.today()
    now = datetime.now()
    now_seconds = now.hour * 3600 + now.minute * 60 + now.second

    service_ids = get_active_service_ids(conn, today)
    scheduled = get_scheduled_departures(conn, stop_id, service_ids)

    stop_name_row = conn.execute(
        "SELECT stop_name FROM stops WHERE stop_id = ?", (stop_id,)
    ).fetchone()
    stop_name = stop_name_row[0] if stop_name_row else stop_id

    print(f"Live departures for: {stop_name} ({stop_id})")
    print(f"Now: {now.strftime('%H:%M')}   Showing next {LOOKAHEAD_MINUTES} minutes")
    print("=" * 70)

    # Fetch live delays once and reuse for all matching trips at this stop
    print("Checking live feed for delays...")
    try:
        live_delays = fetch_live_delays()
    except requests.exceptions.RequestException as e:
        print(f"WARNING: couldn't reach the live feed ({e}). Showing schedule only.\n")
        live_delays = {}

    # Build the list of upcoming departures within the lookahead window
    upcoming = []
    lookahead_seconds = LOOKAHEAD_MINUTES * 60

    for trip_id, arrival_time, route_id, headsign, short_name, long_name in scheduled:
        scheduled_seconds = gtfs_time_to_seconds(arrival_time)

        if scheduled_seconds < now_seconds:
            continue  # already departed
        if scheduled_seconds > now_seconds + lookahead_seconds:
            continue  # too far in the future for this board

        delay_seconds = live_delays.get((trip_id, stop_id))
        expected_seconds = scheduled_seconds + (delay_seconds or 0)

        route_display = short_name or long_name or route_id

        upcoming.append({
            "route": route_display,
            "headsign": headsign or "",
            "scheduled_seconds": scheduled_seconds,
            "expected_seconds": expected_seconds,
            "delay_seconds": delay_seconds,
        })

    upcoming.sort(key=lambda d: d["expected_seconds"])

    conn.close()

    if not upcoming:
        print("No departures scheduled in this window.")
        return

    for dep in upcoming:
        sched_h, sched_m = divmod(dep["scheduled_seconds"] // 60, 60)
        due_in_min = round((dep["expected_seconds"] - now_seconds) / 60)

        if dep["delay_seconds"] is None:
            live_note = "(scheduled - no live data yet)"
        else:
            delay_min = dep["delay_seconds"] / 60
            if abs(delay_min) < 1:
                live_note = "(live - on time)"
            else:
                status = "late" if delay_min > 0 else "early"
                live_note = f"(live - {delay_min:+.0f} min {status})"

        print(f"  {sched_h:02d}:{sched_m:02d}  Route {dep['route']:<8} "
              f"-> {dep['headsign']:<25} due in {due_in_min:>3} min  {live_note}")

    print()
    print("Tip: re-run this any time for an updated board. To watch it")
    print("refresh automatically, we can wrap this in a loop next.")


if __name__ == "__main__":
    main()