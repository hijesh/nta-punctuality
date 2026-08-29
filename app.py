"""
The web backend for the live departures PWA.

Exposes:
  GET /api/stops?q=searchterm         -> list of matching stops
  GET /api/departures/<stop_id>       -> live departures board for that stop
  GET /api/walk-time/<stop_id>        -> walking time (minutes) from the
                                          browser's current location to
                                          that stop, via OpenRouteService
                                          ?lat=<your lat>&lon=<your lon>

And serves the frontend (HTML/JS/manifest) from the web/ folder.

Run it with:
    python app.py

Then open http://localhost:5000 in a browser on the same computer.
"""

import os

from dotenv import load_dotenv
from flask import Flask, jsonify, request, send_from_directory

import departures_lib as lib
import ors_lib

load_dotenv()

API_KEY = os.environ.get("NTA_API_KEY")
ORS_API_KEY = os.environ.get("ORS_API_KEY")

app = Flask(__name__, static_folder="web", static_url_path="")


@app.route("/")
def index():
    return send_from_directory("web", "index.html")


@app.route("/api/stops")
def api_search_stops():
    query = request.args.get("q", "").strip()
    if len(query) < 2:
        return jsonify({"error": "Type at least 2 characters to search."}), 400

    conn = lib.get_connection()
    try:
        results = lib.search_stops(conn, query)
    finally:
        conn.close()

    return jsonify({"results": results})


@app.route("/api/departures/<stop_id>")
def api_departures(stop_id):
    if not API_KEY:
        return jsonify({"error": "Server is missing NTA_API_KEY."}), 500

    conn = lib.get_connection()
    try:
        board = lib.get_live_board(conn, stop_id, API_KEY)
        stop_info = get_stop_info(conn, stop_id)
    finally:
        conn.close()

    board["stop_id"] = stop_id
    board["stop_name"] = stop_info["stop_name"] if stop_info else stop_id
    board["stop_direction"] = stop_info["direction"] if stop_info else None

    return jsonify(board)


@app.route("/api/walk-time/<stop_id>")
def api_walk_time(stop_id):
    if not ORS_API_KEY:
        return jsonify({"error": "Server is missing ORS_API_KEY."}), 500

    try:
        from_lat = float(request.args.get("lat"))
        from_lon = float(request.args.get("lon"))
    except (TypeError, ValueError):
        return jsonify({"error": "lat and lon query parameters are required."}), 400

    conn = lib.get_connection()
    try:
        stop_info = get_stop_info(conn, stop_id)
    finally:
        conn.close()

    if not stop_info or stop_info["lat"] is None:
        return jsonify({"error": "Stop not found or missing coordinates."}), 404

    minutes = ors_lib.get_walk_time_minutes(
        ORS_API_KEY, from_lat, from_lon,
        float(stop_info["lat"]), float(stop_info["lon"]),
    )

    if minutes is None:
        return jsonify({"error": "Could not calculate a walking route."}), 502

    return jsonify({"stop_id": stop_id, "walk_minutes": round(minutes, 1)})


def get_stop_info(conn, stop_id):
    row = conn.execute(
        "SELECT stop_name, stop_lat, stop_lon FROM stops WHERE stop_id = ?",
        (stop_id,),
    ).fetchone()
    if not row:
        return None
    direction = lib.get_stop_direction(conn, stop_id)
    return {"stop_name": row[0], "lat": row[1], "lon": row[2], "direction": direction}


if __name__ == "__main__":
    if not os.path.exists(lib.DB_PATH):
        print("ERROR: No database found. Run setup_static_data.py first.")
    else:
        print("Starting server. Open http://localhost:5000 in your browser.")
        app.run(host="0.0.0.0", port=5000, debug=True)
