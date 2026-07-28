"""Pilot scanner: for each configured city, fetch today's (or tomorrow's)
Polymarket temperature market, fetch an Open-Meteo ensemble forecast for the
same date, score every bracket, log the snapshot, and print anything that
looks like a real mispricing.

This never places a trade. It only observes and logs, so we can check later
(via resolver.py) whether the "edges" it flags would actually have paid off.
"""

import argparse
import sys
from datetime import date, timedelta

from . import storage
from .config import CITIES, EDGE_THRESHOLD
from .forecast import fetch_ensemble_max_temps
from .model import score_brackets
from .polymarket import MarketNotFound, fetch_event, parse_brackets


def event_slug_for(city_slug: str, target_date: date) -> str:
    month = target_date.strftime("%B").lower()
    return f"highest-temperature-in-{city_slug}-on-{month}-{target_date.day}-{target_date.year}"


def already_decided(brackets: list[dict], threshold: float = 0.95) -> bool:
    """True if some bracket is already priced near-certain. For same-day
    markets in timezones far ahead of us, the day's actual high can already
    be known by the time we scan -- the market has priced in reality, and
    comparing that to a forecast is meaningless, not an edge."""
    return any(b["market_price"] >= threshold for b in brackets)


def find_event(city_slug: str, start: date, days_ahead: int = 3):
    """Try `start`, then the next `days_ahead` days, and return the first
    open event whose outcome doesn't already look locked in."""
    for offset in range(days_ahead + 1):
        d = start + timedelta(days=offset)
        slug = event_slug_for(city_slug, d)
        try:
            event = fetch_event(slug)
        except MarketNotFound:
            continue
        if event.get("closed", False):
            continue
        if already_decided(parse_brackets(event)):
            continue
        return event, d
    return None, None


def scan_city(city_name: str, cfg: dict, conn) -> list[dict]:
    event, target_date = find_event(cfg["slug"], date.today())
    if event is None:
        print(f"[{city_name}] no open market found near today", file=sys.stderr)
        return []

    target_date_str = target_date.isoformat()
    brackets = parse_brackets(event)
    open_brackets = [b for b in brackets if not b["closed"]]

    members = fetch_ensemble_max_temps(
        cfg["lat"], cfg["lon"], cfg["timezone"], target_date_str, cfg["unit"]
    )
    scored = score_brackets(open_brackets, members)
    storage.save_snapshot(conn, city_name, event["slug"], target_date_str, scored)
    return scored


def main():
    parser = argparse.ArgumentParser(description="Scan weather markets for mispricings")
    parser.add_argument("--city", help="only scan this city (default: all configured)")
    args = parser.parse_args()

    cities = {args.city: CITIES[args.city]} if args.city else CITIES
    conn = storage.get_connection()

    for city_name, cfg in cities.items():
        try:
            scored = scan_city(city_name, cfg, conn)
        except Exception as exc:
            print(f"[{city_name}] ERROR: {exc}", file=sys.stderr)
            continue

        if not scored:
            continue

        print(f"\n=== {city_name} ({scored[0]['n_members']} ensemble members) ===")
        print(f"{'bracket':<16}{'market':>8}{'model':>8}{'edge':>8}{'vol':>10}")
        for b in sorted(scored, key=lambda x: -abs(x["edge"])):
            flag = "  <-- EDGE" if abs(b["edge"]) >= EDGE_THRESHOLD else ""
            print(
                f"{b['label']:<16}{b['market_price']:>8.3f}"
                f"{b['model_probability']:>8.3f}{b['edge']:>+8.3f}"
                f"{b['volume']:>10.0f}{flag}"
            )

    conn.close()


if __name__ == "__main__":
    main()
