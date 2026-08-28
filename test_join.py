"""
Step 3 checkpoint script.

Takes one live poll of the realtime feed and joins it against the static
schedule database to show real route names, stop names, and how late (or
early) each one is - in minutes, not raw seconds.

This is the core piece of logic the full poller will run every 65 seconds.

Run it with:
    python test_join.py
"""

import os
import sqlite3
import sys

import requests
from dotenv import load_dotenv
from google.transit import gtfs_realtime_pb2

load_dotenv()

API_KEY = os.environ.get("NTA_API_KEY")
TRIP_UPDATES_URL = "https://api.nationaltransport.ie/gtfsr/v2/TripUpdates"
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "punctuality.sqlite3")


def fetch_live_updates():
    response = requests.get(TRIP_UPDATES_URL, headers={"x-api-key": API_KEY}, timeout=15)
    response.raise_for_status()
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.ParseFromString(response.content)
    return feed


def lookup_route_name(conn, route_id):
    row = conn.execute(
        "SELECT route_short_name, route_long_name FROM routes WHERE route_id = ?",
        (route_id,),
    ).fetchone()
    if not row:
        return route_id  # fall back to the raw ID if we can't find a name
    short_name, long_name = row
    return short_name or long_name or route_id


def lookup_stop_name(conn, stop_id):
    row = conn.execute(
        "SELECT stop_name FROM stops WHERE stop_id = ?", (stop_id,)
    ).fetchone()
    return row[0] if row else stop_id


def lookup_scheduled_time(conn, trip_id, stop_id):
    """Returns the scheduled arrival time (as a string like '14:32:00') for
    this trip at this stop, straight from the timetable."""
    row = conn.execute(
        "SELECT arrival_time FROM stop_times WHERE trip_id = ? AND stop_id = ?",
        (trip_id, stop_id),
    ).fetchone()
    return row[0] if row else None


def main():
    if not os.path.exists(DB_PATH):
        print("ERROR: No static database found. Run setup_static_data.py first.")
        sys.exit(1)

    conn = sqlite3.connect(DB_PATH)

    print("Fetching one live poll...")
    feed = fetch_live_updates()
    print(f"Got {len(feed.entity)} trip updates. Showing the first 5 with details:\n")

    shown = 0
    for entity in feed.entity:
        if shown >= 5:
            break

        trip_update = entity.trip_update
        trip_id = trip_update.trip.trip_id
        route_id = trip_update.trip.route_id
        route_name = lookup_route_name(conn, route_id)

        # Each trip_update can cover multiple upcoming stops - just show
        # the first one here as a sample
        if len(trip_update.stop_time_update) == 0:
            continue

        stop_time_update = trip_update.stop_time_update[0]
        stop_id = stop_time_update.stop_id
        stop_name = lookup_stop_name(conn, stop_id)
        scheduled_time = lookup_scheduled_time(conn, trip_id, stop_id)

        # The realtime feed reports delay in seconds (positive = late,
        # negative = early). It may be on "arrival" or "departure".
        if stop_time_update.HasField("arrival"):
            delay_seconds = stop_time_update.arrival.delay
        elif stop_time_update.HasField("departure"):
            delay_seconds = stop_time_update.departure.delay
        else:
            delay_seconds = None

        print(f"Route: {route_name}")
        print(f"Stop:  {stop_name}")
        print(f"Scheduled time: {scheduled_time or 'not found in schedule'}")

        if delay_seconds is not None:
            delay_minutes = delay_seconds / 60
            status = "late" if delay_minutes > 0 else "early" if delay_minutes < 0 else "on time"
            print(f"Delay: {delay_minutes:+.1f} minutes ({status})")
        else:
            print("Delay: not reported for this stop")

        print("-" * 40)
        shown += 1

    conn.close()

    print()
    print("If route names, stop names, and delays all look sensible above,")
    print("the join logic works and we're ready to build the full poller")
    print("that runs this every 65 seconds and logs the results over time.")


if __name__ == "__main__":
    main()