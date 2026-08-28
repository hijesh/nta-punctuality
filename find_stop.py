"""
Find a stop's ID by searching its name.

The live departures board needs a stop_id, but you think in stop names.
This searches the static data for any stop name containing your search
term (case-insensitive, partial match).

Run it with:
    python find_stop.py Letterbreen
"""

import os
import sqlite3
import sys

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "punctuality.sqlite3")


def main():
    if len(sys.argv) < 2:
        print("Usage: python find_stop.py <search term>")
        print('Example: python find_stop.py "Letterbreen"')
        sys.exit(1)

    search_term = " ".join(sys.argv[1:])

    if not os.path.exists(DB_PATH):
        print("ERROR: No database found. Run setup_static_data.py first.")
        sys.exit(1)

    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        """
        SELECT stop_id, stop_name, stop_lat, stop_lon
        FROM stops
        WHERE stop_name LIKE ?
        ORDER BY stop_name
        LIMIT 25
        """,
        (f"%{search_term}%",),
    ).fetchall()
    conn.close()

    if not rows:
        print(f"No stops found matching '{search_term}'.")
        return

    print(f"Found {len(rows)} matching stop(s):\n")
    for stop_id, stop_name, lat, lon in rows:
        print(f"  stop_id: {stop_id:<12}  {stop_name}  ({lat}, {lon})")

    print()
    print("Copy the stop_id you want and use it with:")
    print("    python live_departures.py <stop_id>")


if __name__ == "__main__":
    main()