"""SQLite logging for scan snapshots and resolution outcomes."""

import os
import sqlite3
from datetime import datetime, timezone

from .config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    city TEXT NOT NULL,
    event_slug TEXT NOT NULL,
    market_slug TEXT NOT NULL,
    target_date TEXT NOT NULL,
    bracket_label TEXT NOT NULL,
    low REAL,
    high REAL,
    market_price REAL NOT NULL,
    model_probability REAL NOT NULL,
    edge REAL NOT NULL,
    volume REAL,
    n_members INTEGER
);

CREATE TABLE IF NOT EXISTS resolutions (
    event_slug TEXT PRIMARY KEY,
    city TEXT NOT NULL,
    target_date TEXT NOT NULL,
    actual_temp REAL,
    winning_bracket TEXT,
    resolved_ts TEXT NOT NULL
);
"""


def get_connection() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(SCHEMA)
    return conn


def save_snapshot(conn: sqlite3.Connection, city: str, event_slug: str,
                   target_date: str, scored_brackets: list[dict]) -> None:
    ts = datetime.now(timezone.utc).isoformat()
    rows = [
        (
            ts, city, event_slug, b["market_slug"], target_date, b["label"],
            b["low"], b["high"], b["market_price"], b["model_probability"],
            b["edge"], b["volume"], b["n_members"],
        )
        for b in scored_brackets
    ]
    conn.executemany(
        """INSERT INTO snapshots
           (ts, city, event_slug, market_slug, target_date, bracket_label,
            low, high, market_price, model_probability, edge, volume, n_members)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        rows,
    )
    conn.commit()


def save_resolution(conn: sqlite3.Connection, event_slug: str, city: str,
                     target_date: str, actual_temp: float | None,
                     winning_bracket: str | None) -> None:
    ts = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """INSERT OR REPLACE INTO resolutions
           (event_slug, city, target_date, actual_temp, winning_bracket, resolved_ts)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (event_slug, city, target_date, actual_temp, winning_bracket, ts),
    )
    conn.commit()


def latest_results(conn: sqlite3.Connection) -> dict[str, list[dict]]:
    """Most recent snapshot per city, shaped like scan_city()'s return value,
    for rendering a dashboard without needing a live scan in memory."""
    cur = conn.execute(
        """SELECT s.city, s.bracket_label, s.low, s.high, s.market_price,
                  s.model_probability, s.edge, s.volume, s.n_members, s.market_slug
           FROM snapshots s
           INNER JOIN (
               SELECT city, MAX(ts) AS max_ts FROM snapshots GROUP BY city
           ) latest ON s.city = latest.city AND s.ts = latest.max_ts
           ORDER BY s.city, s.edge DESC"""
    )
    results: dict[str, list[dict]] = {}
    for city, label, low, high, market_price, model_prob, edge, volume, n_members, market_slug in cur.fetchall():
        results.setdefault(city, []).append({
            "label": label, "low": low, "high": high,
            "market_price": market_price, "model_probability": model_prob,
            "edge": edge, "volume": volume, "n_members": n_members,
            "market_slug": market_slug,
        })
    return results


def latest_scan_time(conn: sqlite3.Connection) -> str | None:
    cur = conn.execute("SELECT MAX(ts) FROM snapshots")
    row = cur.fetchone()
    return row[0] if row else None


def unresolved_event_slugs(conn: sqlite3.Connection) -> list[tuple[str, str, str]]:
    """Distinct (event_slug, city, target_date) from snapshots not yet resolved."""
    cur = conn.execute(
        """SELECT DISTINCT s.event_slug, s.city, s.target_date
           FROM snapshots s
           LEFT JOIN resolutions r ON s.event_slug = r.event_slug
           WHERE r.event_slug IS NULL"""
    )
    return cur.fetchall()
