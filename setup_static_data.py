"""
Downloads the static GTFS schedule and loads it into a local SQLite
database (a single file, no server needed).

The static feed contains the timetable: which trips exist, which stops
they visit, at what scheduled time, and what routes they belong to.
The realtime feed only gives IDs and delays - this is what turns those
IDs into human-readable names and schedules.

IMPORTANT: this is no longer run as part of Render's build step. Render's
persistent disks aren't accessible during build (it runs on separate,
temporary compute) - only at runtime. app.py now calls the functions in
this file automatically at startup instead, when the disk is actually
available. This file can still be run manually/locally for testing:

    python setup_static_data.py
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

STATIC_GTFS_URL = "https://www.transportforireland.ie/transitData/Data/GTFS_All.zip"

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


# Rows processed at a time when loading each CSV into SQLite. Reading a
# whole file into one pandas DataFrame at once was fine for the smaller
# GTFS_Realtime.zip, but GTFS_All.zip's nationwide stop_times.txt has far
# more rows - loading it all in one go pushed memory past the server's
# 512MB limit and got the process killed (with no Python traceback, since
# it's an external kill, not a catchable exception). Processing in bounded
# chunks keeps peak memory usage roughly constant regardless of file size.
CHUNK_SIZE = 50_000


def load_into_sqlite(zip_bytes: bytes):
    os.makedirs(DATA_DIR, exist_ok=True)

    # Build into a temporary file, then atomically swap it into place at
    # the very end. This matters now that this can run while the app is
    # live serving requests (periodic refresh) - a half-built or briefly
    # deleted database file would otherwise risk breaking requests that
    # happen mid-rebuild. os.replace() is atomic on the filesystems Render
    # (and normal Linux/Mac/Windows) use.
    temp_path = DB_PATH + ".tmp"
    if os.path.exists(temp_path):
        os.remove(temp_path)

    conn = sqlite3.connect(temp_path)

    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
        available = z.namelist()

        for filename in NEEDED_FILES:
            if filename not in available:
                print(f"WARNING: {filename} not found in the zip - skipping.")
                continue

            table_name = filename.replace(".txt", "")
            print(f"Loading {filename} ...")

            total_rows = 0
            with z.open(filename) as f:
                # dtype=str keeps everything as text - IDs and times
                # shouldn't be treated as numbers. chunksize means pandas
                # hands us one manageable slice at a time instead of
                # parsing the entire file into memory up front.
                for i, chunk in enumerate(pd.read_csv(f, dtype=str, chunksize=CHUNK_SIZE)):
                    chunk.to_sql(
                        table_name, conn,
                        if_exists="replace" if i == 0 else "append",
                        index=False,
                    )
                    total_rows += len(chunk)

            print(f"  -> {total_rows:,} rows loaded into table '{table_name}'")

    # Indexes make our later lookups (by trip_id, stop_id) fast instead of
    # scanning the whole table every time
    cur = conn.cursor()
    cur.execute("CREATE INDEX IF NOT EXISTS idx_stop_times_trip ON stop_times(trip_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_stop_times_stop ON stop_times(stop_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_trips_trip ON trips(trip_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_stops_stop ON stops(stop_id)")
    conn.commit()
    conn.close()

    os.replace(temp_path, DB_PATH)

    print()
    print(f"Done. Static schedule database ready at:\n  {DB_PATH}")


def main():
    zip_bytes = download_static_gtfs()
    load_into_sqlite(zip_bytes)


if __name__ == "__main__":
    main()
