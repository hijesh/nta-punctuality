"""
Step 1 checkpoint script.

This does ONE thing: calls the NTA GTFS-Realtime TripUpdates feed once,
decodes it, and prints a short summary. If this works, your API key and
setup are correct and we can move on to building the real poller.

Run it with:
    python test_connection.py
"""

import os
import sys

import requests
from dotenv import load_dotenv
from google.transit import gtfs_realtime_pb2

# Load the NTA_API_KEY value out of the .env file in this same folder
load_dotenv()

API_KEY = os.environ.get("NTA_API_KEY")
URL = "https://api.nationaltransport.ie/gtfsr/v2/TripUpdates"

def main():
    if not API_KEY:
        print("ERROR: NTA_API_KEY not found.")
        print("Check that your .env file exists in this folder and contains:")
        print("    NTA_API_KEY=your-key-here")
        sys.exit(1)

    print("Calling the NTA TripUpdates feed...")

    response = requests.get(URL, headers={"x-api-key": API_KEY}, timeout=15)

    if response.status_code != 200:
        print(f"ERROR: Got HTTP {response.status_code} back from the API.")
        print("Response body:", response.text[:500])
        print()
        print("Common causes:")
        print(" - Key not subscribed to the GTFS-Realtime product yet")
        print(" - Typo or extra space in the .env file")
        sys.exit(1)

    # The response body is protobuf (a compact binary format) - decode it
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.ParseFromString(response.content)

    print(f"Success! Received {len(feed.entity)} trip updates.")
    print()
    print("Here are the first 5:")
    print("-" * 60)

    for entity in feed.entity[:5]:
        trip_update = entity.trip_update
        trip_id = trip_update.trip.trip_id
        route_id = trip_update.trip.route_id
        num_stops = len(trip_update.stop_time_update)
        print(f"Trip {trip_id}  (route {route_id})  -  {num_stops} stop updates")

    print("-" * 60)
    print()
    print("Your setup works. Next step: we'll download the static schedule")
    print("data so we can turn these trip/stop IDs into real route and")
    print("stop names, and compare against the scheduled times.")


if __name__ == "__main__":
    main()