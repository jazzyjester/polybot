"""Local dashboard: run this once, leave the browser tab open.

A background thread re-scans every SCAN_INTERVAL_SECONDS and re-runs the
resolver against past markets. The page itself auto-refreshes on a timer
and just renders whatever the background thread last found -- it never
scans on page load, so leaving the tab open doesn't hammer the APIs.
"""

import threading
import time
from datetime import datetime, timedelta, timezone

from flask import Flask, jsonify, render_template, request

from . import storage
from .config import CITIES, EDGE_THRESHOLD
from .resolver import resolve_all
from .scanner import scan_city

app = Flask(__name__)

SCAN_INTERVAL_SECONDS = 30 * 60  # weather forecasts don't move faster than this
PAGE_REFRESH_SECONDS = 60        # how often the browser tab reloads itself
STALE_AFTER = timedelta(hours=3)  # generous vs. the scan cadence

STATE = {
    "last_scan": None,
    "next_scan_at": None,
    "results": {},
    "errors": {},
    "accuracy": None,
    "scanning": False,
}
STATE_LOCK = threading.Lock()


def _run_cycle():
    with STATE_LOCK:
        if STATE["scanning"]:
            return
        STATE["scanning"] = True

    conn = storage.get_connection()
    results, errors = {}, {}
    for city_name, cfg in CITIES.items():
        try:
            results[city_name] = scan_city(city_name, cfg, conn)
        except Exception as exc:
            errors[city_name] = str(exc)

    try:
        accuracy = resolve_all(conn, EDGE_THRESHOLD)
    except Exception as exc:
        accuracy = {"error": str(exc)}
    conn.close()

    with STATE_LOCK:
        STATE["results"] = results
        STATE["errors"] = errors
        STATE["accuracy"] = accuracy
        STATE["last_scan"] = datetime.now(timezone.utc)
        STATE["next_scan_at"] = time.time() + SCAN_INTERVAL_SECONDS
        STATE["scanning"] = False


def _background_loop():
    while True:
        _run_cycle()
        time.sleep(SCAN_INTERVAL_SECONDS)


@app.route("/")
def dashboard():
    with STATE_LOCK:
        snapshot = {
            "results": STATE["results"],
            "errors": STATE["errors"],
            "accuracy": STATE["accuracy"],
            "last_scan": STATE["last_scan"],
            "next_scan_at": STATE["next_scan_at"],
            "scanning": STATE["scanning"],
        }

    next_scan_in = None
    if snapshot["next_scan_at"]:
        next_scan_in = max(0, int(snapshot["next_scan_at"] - time.time()))

    stale = []
    if snapshot["last_scan"]:
        conn = storage.get_connection()
        scan_times = storage.latest_scan_time_per_city(conn)
        conn.close()
        stale = storage.stale_cities(scan_times, CITIES.keys(), datetime.now(timezone.utc), STALE_AFTER)

    return render_template(
        "dashboard.html",
        results=snapshot["results"],
        errors=snapshot["errors"],
        accuracy=snapshot["accuracy"],
        last_scan=snapshot["last_scan"],
        next_scan_in=next_scan_in,
        scanning=snapshot["scanning"],
        edge_threshold=EDGE_THRESHOLD,
        page_refresh_seconds=PAGE_REFRESH_SECONDS,
        static=False,
        stale_cities=stale,
    )


@app.route("/scan-now", methods=["POST"])
def scan_now():
    threading.Thread(target=_run_cycle, daemon=True).start()
    return jsonify({"started": True})


def main():
    threading.Thread(target=_background_loop, daemon=True).start()
    app.run(host="127.0.0.1", port=8765, debug=False)


if __name__ == "__main__":
    main()
