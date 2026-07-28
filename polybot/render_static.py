"""Render a static dashboard page (for GitHub Pages) from whatever is
currently in the database. Run this after scanner.py in the CI workflow.

Unlike web.py, there's no live server here -- this writes a plain HTML
snapshot to docs/index.html that GitHub Pages serves as-is. The page still
auto-refreshes the browser tab, but new *content* only shows up once the
next scheduled workflow run regenerates and commits this file.
"""

import os
from datetime import datetime, timezone

from jinja2 import Environment, FileSystemLoader

from . import storage
from .config import EDGE_THRESHOLD
from .resolver import resolve_all

TEMPLATE_DIR = os.path.join(os.path.dirname(__file__), "templates")
OUTPUT_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "docs", "index.html")
REPO_URL = os.environ.get("POLYBOT_REPO_URL", "")
PAGE_REFRESH_SECONDS = 300  # static page: no point refreshing faster than CI updates it


def render():
    conn = storage.get_connection()
    results = storage.latest_results(conn)
    accuracy = resolve_all(conn, EDGE_THRESHOLD)
    last_scan_str = storage.latest_scan_time(conn)
    conn.close()

    last_scan = datetime.fromisoformat(last_scan_str) if last_scan_str else None

    env = Environment(loader=FileSystemLoader(TEMPLATE_DIR))
    template = env.get_template("dashboard.html")
    html = template.render(
        results=results,
        errors={},
        accuracy=accuracy,
        last_scan=last_scan,
        next_scan_in=None,
        scanning=False,
        edge_threshold=EDGE_THRESHOLD,
        page_refresh_seconds=PAGE_REFRESH_SECONDS,
        static=True,
        repo_url=REPO_URL,
    )

    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, "w") as f:
        f.write(html)
    print(f"wrote {OUTPUT_PATH} ({len(html)} bytes)")


if __name__ == "__main__":
    render()
