"""
Step 5: Punctuality report.

Reads back everything the poller has logged so far and produces a
summary: average delay per route, % on-time, and the worst performers.

Run it any time you want an updated report (even while the poller is
running in another window):
    python report.py
"""

import os
import sqlite3
import sys

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "punctuality.sqlite3")

# A trip counts as "on time" if it's within this many minutes either side
# of its scheduled time. 5 minutes is a common industry standard for buses.
ON_TIME_THRESHOLD_MINUTES = 5


def main():
    if not os.path.exists(DB_PATH):
        print("ERROR: No database found. Run setup_static_data.py and the poller first.")
        sys.exit(1)

    conn = sqlite3.connect(DB_PATH)

    total_rows = conn.execute(
        "SELECT COUNT(*) FROM punctuality_log WHERE delay_seconds IS NOT NULL"
    ).fetchone()[0]

    if total_rows == 0:
        print("No delay data logged yet. Let the poller run a bit longer.")
        return

    print(f"Punctuality report based on {total_rows:,} logged observations")
    print("=" * 65)

    # --- Overall on-time percentage -------------------------------------
    threshold_seconds = ON_TIME_THRESHOLD_MINUTES * 60
    on_time_count = conn.execute(
        """
        SELECT COUNT(*) FROM punctuality_log
        WHERE delay_seconds IS NOT NULL
          AND ABS(delay_seconds) <= ?
        """,
        (threshold_seconds,),
    ).fetchone()[0]

    on_time_pct = 100 * on_time_count / total_rows
    print(f"\nOverall on-time performance (within {ON_TIME_THRESHOLD_MINUTES} min): "
          f"{on_time_pct:.1f}%")

    # --- Average delay per route ----------------------------------------
    print("\nAverage delay by route (minutes, +ve = late):")
    print("-" * 65)
    rows = conn.execute(
        """
        SELECT route_name,
               AVG(delay_seconds) / 60.0 AS avg_delay_min,
               COUNT(*) AS observations
        FROM punctuality_log
        WHERE delay_seconds IS NOT NULL
        GROUP BY route_name
        HAVING observations >= 5
        ORDER BY avg_delay_min DESC
        LIMIT 15
        """
    ).fetchall()

    if not rows:
        print("  (not enough data yet per route - let the poller run longer)")
    else:
        for route_name, avg_delay_min, observations in rows:
            print(f"  Route {route_name:<10}  {avg_delay_min:+6.1f} min avg   "
                  f"({observations} observations)")

    # --- Worst individual stop delays seen --------------------------------
    print("\nWorst single delays observed:")
    print("-" * 65)
    rows = conn.execute(
        """
        SELECT route_name, stop_name, delay_seconds / 60.0 AS delay_min, polled_at
        FROM punctuality_log
        WHERE delay_seconds IS NOT NULL
        ORDER BY delay_seconds DESC
        LIMIT 5
        """
    ).fetchall()

    for route_name, stop_name, delay_min, polled_at in rows:
        print(f"  Route {route_name} at {stop_name}: {delay_min:+.1f} min "
              f"(seen at {polled_at})")

    conn.close()

    print()
    print("=" * 65)
    print("Tip: the longer the poller runs, the more reliable these numbers")
    print("get - especially the per-route averages, which need repeated")
    print("observations across different trips to mean much.")


if __name__ == "__main__":
    main()