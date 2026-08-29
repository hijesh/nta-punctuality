"""
Step 4: The real poller.

Runs continuously, checking the live feed every 65 seconds (safely above
NTA's 1-call-per-60-seconds limit), and logs every trip/stop delay it sees
into the database. Over days and weeks, this log IS your punctuality
history - you'll be able to ask things like "how often is route 16 late
by more than 5 minutes?".

Leave this running in a Command Prompt window (or set it up to run in
the background later). Press Ctrl+C to stop it cleanly.

Run it with:
    python run_poller.py
"""

import os
import sqlite3
import sys
import time
from datetime import datetime, date
from zoneinfo import ZoneInfo

import requests
from dotenv import load_dotenv
from google.transit import gtfs_realtime_pb2

load_dotenv()

API_KEY = os.environ.get("NTA_API_KEY")
TRIP_UPDATES_URL = "https://api.nationaltransport.ie/gtfsr/v2/TripUpdates"
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "punctuality.sqlite3")

# NTA fair usage policy: 1 call per 60 seconds per token. We use 65s for
# a safety margin against clock drift and network latency.
POLL_INTERVAL_SECONDS = 65

# Ireland's timezone - see note in departures_lib.py for why this matters.
IE_TZ = ZoneInfo("Europe/Dublin")


def ensure_log_table(conn):
    """Creates the table that stores every observation, if it doesn't
    already exist. This table only ever grows - it's your history."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS punctuality_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            polled_at TEXT NOT NULL,
            service_date TEXT NOT NULL,
            trip_id TEXT NOT NULL,
            route_id TEXT,
            route_name TEXT,
            stop_id TEXT,
            stop_name TEXT,
            scheduled_time TEXT,
            delay_seconds INTEGER
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_log_route_date "
        "ON punctuality_log(route_id, service_date)"
    )
    conn.commit()


def lookup_route_name(conn, route_id):
    row = conn.execute(
        "SELECT route_short_name, route_long_name FROM routes WHERE route_id = ?",
        (route_id,),
    ).fetchone()
    if not row:
        return route_id
    short_name, long_name = row
    return short_name or long_name or route_id


def lookup_stop_name(conn, stop_id):
    row = conn.execute(
        "SELECT stop_name FROM stops WHERE stop_id = ?", (stop_id,)
    ).fetchone()
    return row[0] if row else stop_id


def lookup_scheduled_time(conn, trip_id, stop_id):
    row = conn.execute(
        "SELECT arrival_time FROM stop_times WHERE trip_id = ? AND stop_id = ?",
        (trip_id, stop_id),
    ).fetchone()
    return row[0] if row else None


def poll_once(conn):
    """Does one fetch-decode-join-store cycle. Returns how many rows
    were logged."""
    response = requests.get(TRIP_UPDATES_URL, headers={"x-api-key": API_KEY}, timeout=15)
    response.raise_for_status()

    feed = gtfs_realtime_pb2.FeedMessage()
    feed.ParseFromString(response.content)

    polled_at = datetime.now(IE_TZ).isoformat(timespec="seconds")
    service_date = datetime.now(IE_TZ).date().isoformat()

    rows_logged = 0
    cur = conn.cursor()

    for entity in feed.entity:
        trip_update = entity.trip_update
        trip_id = trip_update.trip.trip_id
        route_id = trip_update.trip.route_id
        route_name = lookup_route_name(conn, route_id)

        # A single trip update can report on several upcoming stops -
        # log all of them, not just the first
        for stop_time_update in trip_update.stop_time_update:
            stop_id = stop_time_update.stop_id
            stop_name = lookup_stop_name(conn, stop_id)
            scheduled_time = lookup_scheduled_time(conn, trip_id, stop_id)

            if stop_time_update.HasField("arrival"):
                delay_seconds = stop_time_update.arrival.delay
            elif stop_time_update.HasField("departure"):
                delay_seconds = stop_time_update.departure.delay
            else:
                delay_seconds = None

            cur.execute(
                """
                INSERT INTO punctuality_log
                    (polled_at, service_date, trip_id, route_id, route_name,
                     stop_id, stop_name, scheduled_time, delay_seconds)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    polled_at, service_date, trip_id, route_id, route_name,
                    stop_id, stop_name, scheduled_time, delay_seconds,
                ),
            )
            rows_logged += 1

    conn.commit()
    return rows_logged


def main():
    if not API_KEY:
        print("ERROR: NTA_API_KEY not found in .env file.")
        sys.exit(1)

    if not os.path.exists(DB_PATH):
        print("ERROR: No static database found. Run setup_static_data.py first.")
        sys.exit(1)

    conn = sqlite3.connect(DB_PATH)
    ensure_log_table(conn)

    print("Poller started. Polling every", POLL_INTERVAL_SECONDS, "seconds.")
    print("Leave this window open. Press Ctrl+C to stop.\n")

    poll_count = 0
    try:
        while True:
            start = time.time()
            try:
                rows_logged = poll_once(conn)
                poll_count += 1
                print(
                    f"[{datetime.now(IE_TZ).strftime('%H:%M:%S')}] "
                    f"Poll #{poll_count}: logged {rows_logged} stop updates."
                )
            except requests.exceptions.RequestException as e:
                # Network hiccups happen - log it and keep going rather
                # than crashing the whole poller
                print(f"[{datetime.now(IE_TZ).strftime('%H:%M:%S')}] "
                      f"WARNING: poll failed ({e}). Will retry next cycle.")

            elapsed = time.time() - start
            sleep_time = max(0, POLL_INTERVAL_SECONDS - elapsed)
            time.sleep(sleep_time)

    except KeyboardInterrupt:
        print("\nStopped by user. Your logged data is saved in:")
        print(f"  {DB_PATH}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
