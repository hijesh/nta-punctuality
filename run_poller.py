"""
Standalone punctuality poller (for running locally/manually).

This does the same polling that now also runs automatically as a
background thread inside app.py on Render - this script exists for
local testing/inspection without needing the whole web server running.

Run it with:
    python run_poller.py
"""

import os
import sys
import time
from datetime import datetime

import requests
from dotenv import load_dotenv

import departures_lib as lib

load_dotenv()

API_KEY = os.environ.get("NTA_API_KEY")

# NTA fair usage policy: 1 call per 60 seconds per token. We use 65s for
# a safety margin against clock drift and network latency.
POLL_INTERVAL_SECONDS = 65


def main():
    if not API_KEY:
        print("ERROR: NTA_API_KEY not found in .env file.")
        sys.exit(1)

    if not os.path.exists(lib.DB_PATH):
        print("ERROR: No static database found. Run setup_static_data.py first.")
        sys.exit(1)

    conn = lib.get_connection()

    print("Poller started. Polling every", POLL_INTERVAL_SECONDS, "seconds.")
    print("Leave this window open. Press Ctrl+C to stop.\n")

    poll_count = 0
    try:
        while True:
            start = time.time()
            try:
                rows_logged = lib.poll_and_log_punctuality(conn, API_KEY)
                poll_count += 1
                print(
                    f"[{datetime.now(lib.IE_TZ).strftime('%H:%M:%S')}] "
                    f"Poll #{poll_count}: logged {rows_logged} stop updates."
                )
            except requests.exceptions.RequestException as e:
                print(f"[{datetime.now(lib.IE_TZ).strftime('%H:%M:%S')}] "
                      f"WARNING: poll failed ({e}). Will retry next cycle.")

            elapsed = time.time() - start
            sleep_time = max(0, POLL_INTERVAL_SECONDS - elapsed)
            time.sleep(sleep_time)

    except KeyboardInterrupt:
        print("\nStopped by user. Your logged data is saved in:")
        print(f"  {lib.DB_PATH}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
