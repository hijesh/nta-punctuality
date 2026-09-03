"""
Step 2: Download the static GTFS schedule and load it into a local
SQLite database (a single file, no server needed).

The static feed contains the timetable: which trips exist, which stops
they visit, at what scheduled time, and what routes they belong to.
The realtime feed only gives IDs and delays - this is what turns those
IDs into human-readable names and schedules.

Run it with:
    python setup_static_data.py

Re-run it any time you want to refresh the schedule (e.g. weekly) -
it will re-download and rebuild the database from scratch.
"""

import os
import sqlite3
import zipfile
import io

import requests
import pandas as pd

# Reuses the exact same DATA_DIR/DB_PATH as the rest of the app (departures_lib.py),
# so this script and the running server always agree on where the database
# lives - whether that's the local "data" folder or a mounted Render disk.
from departures_lib import DATA_DIR, DB_PATH

STATIC_GTFS_URL = "https://www.transportforireland.ie/transitData/Data/GTFS_Realtime.zip"

# The GTFS spec defines these standard files inside the zip. calendar.txt
# and calendar_dates.txt tell us which trips run on which days (weekday vs
# weekend vs public-holiday exceptions) - needed to build an accurate
# "departures today" board, not just a static timetable. agency.txt tells
# us which company/mode operates each route (Dublin Bus, Luas, Irish Rail,
# etc.) - needed for the operator icons.
NEEDED_FILES = [
    "routes.txt", "trips.txt", "stops.txt", "stop_times.txt",
    "calendar.txt", "calendar_dates.txt", "agency.txt",
]


def download_static_gtfs() -> bytes:
    print(f"Downloading static schedule from:\n  {STATIC_GTFS_URL}")
    response = requests.get(STATIC_GTFS_URL, timeout=120)
    response.raise_for_status()
    print(f"Downloaded {len(response.content) / 1_000_000:.1f} MB.")
    return response.content


def load_into_sqlite(zip_bytes: bytes):
    os.makedirs(DATA_DIR, exist_ok=True)

    # Wipe any previous database so we always start fresh
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)

    conn = sqlite3.connect(DB_PATH)

    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
        available = z.namelist()

        for filename in NEEDED_FILES:
            if filename not in available:
                print(f"WARNING: {filename} not found in the zip - skipping.")
                continue

            print(f"Loading {filename} ...")
            with z.open(filename) as f:
                df = pd.read_csv(f, dtype=str)  # keep everything as text -
                                                 # IDs and times shouldn't be
                                                 # treated as numbers

            table_name = filename.replace(".txt", "")
            df.to_sql(table_name, conn, if_exists="replace", index=False)
            print(f"  -> {len(df):,} rows loaded into table '{table_name}'")

    # Indexes make our later lookups (by trip_id, stop_id) fast instead of
    # scanning the whole table every time
    cur = conn.cursor()
    cur.execute("CREATE INDEX IF NOT EXISTS idx_stop_times_trip ON stop_times(trip_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_stop_times_stop ON stop_times(stop_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_trips_trip ON trips(trip_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_stops_stop ON stops(stop_id)")
    conn.commit()
    conn.close()

    print()
    print(f"Done. Static schedule database ready at:\n  {DB_PATH}")


def main():
    zip_bytes = download_static_gtfs()
    load_into_sqlite(zip_bytes)


if __name__ == "__main__":
    main()
