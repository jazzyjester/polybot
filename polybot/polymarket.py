"""Read-only client for Polymarket's public Gamma API."""

import json
import re

import requests

from .config import GAMMA_EVENTS_URL

_RANGE_RE = re.compile(r"^(-?\d+)\s*-\s*(-?\d+)\s*°[CF]$")
_SINGLE_RE = re.compile(r"^(-?\d+)\s*°[CF]$")
_BELOW_RE = re.compile(r"^(-?\d+)\s*°[CF]\s*or below$")
_ABOVE_RE = re.compile(r"^(-?\d+)\s*°[CF]\s*or higher$")


class MarketNotFound(Exception):
    pass


def fetch_event(slug: str) -> dict:
    resp = requests.get(GAMMA_EVENTS_URL, params={"slug": slug}, timeout=15)
    resp.raise_for_status()
    events = resp.json()
    if not events:
        raise MarketNotFound(slug)
    return events[0]


def parse_bracket_bounds(group_item_title: str) -> tuple[float, float]:
    """Convert a bracket label like '70-71°F' or '22°C' into (low, high)
    half-open bounds around the integer reading, e.g. '22°C' -> (21.5, 22.5).
    """
    m = _RANGE_RE.match(group_item_title)
    if m:
        lo, hi = int(m.group(1)), int(m.group(2))
        return (lo - 0.5, hi + 0.5)

    m = _BELOW_RE.match(group_item_title)
    if m:
        hi = int(m.group(1))
        return (float("-inf"), hi + 0.5)

    m = _ABOVE_RE.match(group_item_title)
    if m:
        lo = int(m.group(1))
        return (lo - 0.5, float("inf"))

    m = _SINGLE_RE.match(group_item_title)
    if m:
        v = int(m.group(1))
        return (v - 0.5, v + 0.5)

    raise ValueError(f"Unrecognized bracket label: {group_item_title!r}")


def parse_brackets(event: dict) -> list[dict]:
    """Extract {label, low, high, market_price, volume, slug} per bracket."""
    brackets = []
    for m in event["markets"]:
        label = m["groupItemTitle"]
        low, high = parse_bracket_bounds(label)
        outcomes = json.loads(m["outcomes"])
        prices = json.loads(m["outcomePrices"])
        yes_idx = outcomes.index("Yes")
        brackets.append(
            {
                "label": label,
                "low": low,
                "high": high,
                "market_price": float(prices[yes_idx]),
                "volume": float(m.get("volume", 0) or 0),
                "market_slug": m["slug"],
                "closed": m.get("closed", False),
            }
        )
    return brackets
