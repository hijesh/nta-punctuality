"""
The web backend for the live departures PWA.

Exposes two simple JSON endpoints:
  GET /api/stops?q=searchterm     -> list of matching stops
  GET /api/departures/<stop_id>   -> live departures board for that stop

And serves the frontend (HTML/JS/manifest) from the web/ folder.

Run it with:
    python app.py

Then open http://localhost:5000 in a browser on the same computer.
"""

import os

from dotenv import load_dotenv
from flask import Flask, jsonify, request, send_from_directory

import departures_lib as lib

load_dotenv()

API_KEY = os.environ.get("NTA_API_KEY")

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
    finally:
        conn.close()

    stop_row = conn_stop_name(stop_id)
    board["stop_id"] = stop_id
    board["stop_name"] = stop_row

    return jsonify(board)


def conn_stop_name(stop_id):
    conn = lib.get_connection()
    row = conn.execute(
        "SELECT stop_name FROM stops WHERE stop_id = ?", (stop_id,)
    ).fetchone()
    conn.close()
    return row[0] if row else stop_id


if __name__ == "__main__":
    if not os.path.exists(lib.DB_PATH):
        print("ERROR: No database found. Run setup_static_data.py first.")
    else:
        print("Starting server. Open http://localhost:5000 in your browser.")
        app.run(host="0.0.0.0", port=5000, debug=True)
