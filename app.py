"""
The web backend for the live departures PWA.

Exposes:
  GET /api/stops?q=searchterm         -> list of matching stops
  GET /api/departures/<stop_id>       -> live departures board for that stop
  GET /api/walk-time/<stop_id>        -> walking time (minutes) from the
                                          browser's current location to
                                          that stop, via OpenRouteService
                                          ?lat=<your lat>&lon=<your lon>
  GET /stats                          -> password-protected usage stats page

And serves the frontend (HTML/JS/manifest) from the web/ folder.

Run it with:
    python app.py

Then open http://localhost:5000 in a browser on the same computer.
"""

import functools
import html
import os
import time
import uuid

from dotenv import load_dotenv
from flask import Flask, jsonify, request, send_from_directory, make_response, Response

import departures_lib as lib
import ors_lib

load_dotenv()

API_KEY = os.environ.get("NTA_API_KEY")
ORS_API_KEY = os.environ.get("ORS_API_KEY")
STATS_USERNAME = os.environ.get("STATS_USERNAME", "admin")
STATS_PASSWORD = os.environ.get("STATS_PASSWORD")

app = Flask(__name__, static_folder="web", static_url_path="")

VISITOR_COOKIE_NAME = "visitor_id"

# Avoids logging a "stop_view" every single 30-second auto-refresh from an
# open tab - only counts a fresh view if this visitor hasn't looked at this
# stop in the last few minutes.
_recent_stop_views = {}
_STOP_VIEW_DEDUPE_SECONDS = 5 * 60


def get_or_create_visitor_id():
    visitor_id = request.cookies.get(VISITOR_COOKIE_NAME)
    is_new = visitor_id is None
    if is_new:
        visitor_id = str(uuid.uuid4())
    return visitor_id, is_new


@app.route("/")
def index():
    visitor_id, is_new = get_or_create_visitor_id()
    lib.log_event("page_view", visitor_id=visitor_id)

    response = make_response(send_from_directory("web", "index.html"))
    if is_new:
        # One year, anonymous random ID only - no personal data stored
        response.set_cookie(VISITOR_COOKIE_NAME, visitor_id,
                             max_age=365 * 24 * 60 * 60, httponly=True, samesite="Lax")
    return response


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


@app.route("/api/nearby-stops")
def api_nearby_stops():
    try:
        lat = float(request.args.get("lat"))
        lon = float(request.args.get("lon"))
    except (TypeError, ValueError):
        return jsonify({"error": "lat and lon query parameters are required."}), 400

    conn = lib.get_connection()
    try:
        results = lib.get_nearby_stops(conn, lat, lon)
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
    board["stop_code"] = stop_info["stop_code"] if stop_info else None
    board["stop_lat"] = stop_info["lat"] if stop_info else None
    board["stop_lon"] = stop_info["lon"] if stop_info else None

    maybe_log_stop_view(stop_id, board["stop_name"])

    return jsonify(board)


def maybe_log_stop_view(stop_id, stop_name):
    visitor_id, _ = get_or_create_visitor_id()
    key = (visitor_id, stop_id)
    now = time.time()

    last_seen = _recent_stop_views.get(key)
    if last_seen and (now - last_seen) < _STOP_VIEW_DEDUPE_SECONDS:
        return  # this visitor already "viewed" this stop very recently

    _recent_stop_views[key] = now
    lib.log_event("stop_view", stop_id=stop_id, stop_name=stop_name, visitor_id=visitor_id)


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


@app.route("/api/feedback", methods=["POST"])
def api_feedback():
    data = request.get_json(silent=True) or {}
    message = (data.get("message") or "").strip()

    if not message:
        return jsonify({"error": "Message can't be empty."}), 400
    if len(message) > 2000:
        message = message[:2000]

    stop_id = data.get("stop_id") or None
    stop_name = data.get("stop_name") or None
    visitor_id, _ = get_or_create_visitor_id()

    lib.save_feedback(message, visitor_id, stop_id=stop_id, stop_name=stop_name)
    return jsonify({"ok": True})


def get_stop_info(conn, stop_id):
    columns = "stop_name, stop_lat, stop_lon"
    if lib.has_stop_code(conn):
        columns += ", stop_code"

    row = conn.execute(
        f"SELECT {columns} FROM stops WHERE stop_id = ?", (stop_id,)
    ).fetchone()
    if not row:
        return None

    direction = lib.get_stop_direction(conn, stop_id)
    info = {"stop_name": row[0], "lat": row[1], "lon": row[2], "direction": direction}
    info["stop_code"] = row[3] if len(row) > 3 else None
    return info


# --- Password-protected stats page ---------------------------------------

def requires_stats_auth(view_func):
    @functools.wraps(view_func)
    def wrapper(*args, **kwargs):
        auth = request.authorization
        if not STATS_PASSWORD:
            return "Server is missing STATS_PASSWORD.", 500
        if not auth or auth.username != STATS_USERNAME or auth.password != STATS_PASSWORD:
            return Response(
                "Login required.", 401,
                {"WWW-Authenticate": 'Basic realm="Usage Stats"'},
            )
        return view_func(*args, **kwargs)
    return wrapper


@app.route("/stats")
@requires_stats_auth
def stats_page():
    stats = lib.get_stats_summary()

    top_stops_rows = "".join(
        f"<tr><td>{s['stop_name']}</td><td>{s['stop_id']}</td>"
        f"<td>{s['views']}</td><td>{s['visitors']}</td></tr>"
        for s in stats["top_stops"]
    ) or "<tr><td colspan='4'>No stop views logged yet.</td></tr>"

    daily_rows = "".join(
        f"<tr><td>{d['day']}</td><td>{d['views']}</td></tr>"
        for d in stats["last_7_days_views"]
    ) or "<tr><td colspan='2'>No page views logged yet.</td></tr>"

    feedback_items = lib.get_recent_feedback()
    feedback_html = "".join(
        f"<div class='feedback-item'><div class='feedback-meta'>{f['created_at']}"
        f"{' &middot; ' + f['stop_name'] if f['stop_name'] else ''}</div>"
        f"<div class='feedback-msg'>{html.escape(f['message'])}</div></div>"
        for f in feedback_items
    ) or "<p style='color:#6b7686;'>No feedback submitted yet.</p>"

    import json
    stop_locations_json = json.dumps(stats["stop_locations"])

    page_html = f"""
    <!DOCTYPE html>
    <html>
    <head>
      <title>Usage Stats</title>
      <meta name="viewport" content="width=device-width, initial-scale=1" />
      <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
      <style>
        body {{ background:#0a0e14; color:#e7ebf0; font-family: system-ui, sans-serif; padding: 1.5rem; }}
        h1 {{ font-size: 1.3rem; }}
        .cards {{ display:flex; gap:1rem; flex-wrap:wrap; margin: 1.2rem 0; }}
        .card {{ background:#11161f; border:1px solid #1c2430; border-radius:10px; padding:1rem 1.4rem; }}
        .card .num {{ font-size:1.6rem; font-weight:700; color:#ffb020; }}
        .card .label {{ font-size:0.8rem; color:#6b7686; }}
        table {{ border-collapse: collapse; width:100%; margin-top:0.5rem; }}
        th, td {{ text-align:left; padding:0.5rem 0.7rem; border-bottom:1px solid #1c2430; font-size:0.9rem; }}
        th {{ color:#6b7686; font-weight:600; font-size:0.75rem; text-transform:uppercase; }}
        h2 {{ font-size: 1rem; margin-top: 2rem; }}
        #usageMap {{ height: 420px; border-radius: 10px; margin-top: 0.75rem; border: 1px solid #1c2430; }}
        .feedback-item {{ background:#11161f; border:1px solid #1c2430; border-radius:8px; padding:0.7rem 0.9rem; margin-bottom:0.6rem; }}
        .feedback-meta {{ font-size:0.72rem; color:#6b7686; margin-bottom:0.3rem; }}
        .feedback-msg {{ font-size:0.9rem; white-space:pre-wrap; }}
      </style>
    </head>
    <body>
      <h1>App usage stats</h1>
      <div class="cards">
        <div class="card"><div class="num">{stats['total_page_views']}</div><div class="label">Page views (all time)</div></div>
        <div class="card"><div class="num">{stats['unique_visitors']}</div><div class="label">Unique visitors (all time)</div></div>
        <div class="card"><div class="num">{stats['total_stop_views']}</div><div class="label">Stop lookups (all time)</div></div>
      </div>

      <h2>Where usage is happening</h2>
      <div id="usageMap"></div>

      <h2>Most-viewed stops</h2>
      <table>
        <tr><th>Stop name</th><th>Stop ID</th><th>Views</th><th>Unique visitors</th></tr>
        {top_stops_rows}
      </table>

      <h2>Page views - last 7 days</h2>
      <table>
        <tr><th>Day</th><th>Views</th></tr>
        {daily_rows}
      </table>

      <h2>Feedback from testers</h2>
      {feedback_html}

      <p style="color:#6b7686; font-size:0.78rem; margin-top:2rem;">
        Note: on free hosting, this data resets whenever the server restarts.
        The map shows which stops people look up, not individual visitors' locations.
      </p>

      <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
      <script>
        const stopLocations = {stop_locations_json};

        if (stopLocations.length > 0) {{
          const map = L.map('usageMap');
          L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{
            attribution: '&copy; OpenStreetMap contributors',
            maxZoom: 18,
          }}).addTo(map);

          const maxViews = Math.max(...stopLocations.map(s => s.views));
          const bounds = [];

          stopLocations.forEach(s => {{
            const radius = 6 + (s.views / maxViews) * 20;
            const circle = L.circleMarker([s.lat, s.lon], {{
              radius: radius,
              color: '#ffb020',
              fillColor: '#ffb020',
              fillOpacity: 0.45,
              weight: 1,
            }}).addTo(map);
            circle.bindPopup(`<strong>${{s.stop_name}}</strong><br/>${{s.views}} view(s)`);
            bounds.push([s.lat, s.lon]);
          }});

          map.fitBounds(bounds, {{ padding: [30, 30] }});
        }} else {{
          document.getElementById('usageMap').innerHTML =
            '<p style="padding:1rem; color:#6b7686;">No stop views logged yet.</p>';
        }}
      </script>
    </body>
    </html>
    """
    return Response(page_html, mimetype="text/html")


if __name__ == "__main__":
    if not os.path.exists(lib.DB_PATH):
        print("ERROR: No database found. Run setup_static_data.py first.")
    else:
        print("Starting server. Open http://localhost:5000 in your browser.")
        app.run(host="0.0.0.0", port=5000, debug=True)
