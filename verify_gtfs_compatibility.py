"""
Verification script - run this BEFORE switching to GTFS_All.zip.

Checks whether the two files use consistent trip_id/route_id values for
the overlapping operators (the ones your live GTFS-Realtime feed actually
tracks). If they match, switching is safe. If they don't, switching would
silently break live tracking for routes that currently work fine.

Run it with:
    python verify_gtfs_compatibility.py
"""

import io
import zipfile

import pandas as pd
import requests

REALTIME_URL = "https://www.transportforireland.ie/transitData/Data/GTFS_Realtime.zip"
ALL_URL = "https://www.transportforireland.ie/transitData/Data/GTFS_All.zip"


def load_trips(url):
    print(f"Downloading {url} ...")
    response = requests.get(url, timeout=120)
    response.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(response.content)) as z:
        with z.open("trips.txt") as f:
            df = pd.read_csv(f, dtype=str)
        with z.open("routes.txt") as f:
            routes_df = pd.read_csv(f, dtype=str)
        with z.open("stops.txt") as f:
            stops_df = pd.read_csv(f, dtype=str)
    return df, routes_df, stops_df


def main():
    realtime_trips, realtime_routes, realtime_stops = load_trips(REALTIME_URL)
    all_trips, all_routes, all_stops = load_trips(ALL_URL)

    print()
    print("=" * 60)
    print(f"GTFS_Realtime.zip: {len(realtime_trips):,} trips, "
          f"{len(realtime_routes):,} routes, {len(realtime_stops):,} stops")
    print(f"GTFS_All.zip:      {len(all_trips):,} trips, "
          f"{len(all_routes):,} routes, {len(all_stops):,} stops")
    print("=" * 60)

    # Take a sample of trip_ids from the realtime file and check whether
    # the EXACT SAME trip_ids exist in the all-operators file
    sample_trip_ids = set(realtime_trips["trip_id"].dropna().sample(
        min(181488, len(realtime_trips)), random_state=1
    ))
    all_trip_ids = set(all_trips["trip_id"].dropna())

    matched = sample_trip_ids & all_trip_ids
    match_pct = 100 * len(matched) / len(sample_trip_ids)

    print()
    print(f"Sample check: {len(matched)}/{len(sample_trip_ids)} "
          f"({match_pct:.0f}%) of GTFS_Realtime trip_ids also found "
          f"identically in GTFS_All")

    if match_pct > 95:
        print()
        print("LIKELY SAFE: trip_ids are consistent between the two files.")
        print("Switching to GTFS_All.zip should preserve live tracking for")
        print("routes that currently work, while adding more stops/routes")
        print("from operators without real-time data (shown as 'Scheduled' only).")
    else:
        print()
        print("WARNING: trip_ids do NOT match well between the two files.")
        print("Switching to GTFS_All.zip as-is would likely break live")
        print("tracking for routes that currently work correctly. Do not")
        print("switch without further investigation.")


if __name__ == "__main__":
    main()
