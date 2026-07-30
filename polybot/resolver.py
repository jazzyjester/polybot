"""Check logged snapshots against what actually happened.

Two separate jobs, on purpose:
  - resolve_pending(): find finished-but-unresolved events, fetch the
    observed temperature, and record which bracket actually won.
  - compute_summary(): calibration (Brier score) and simulated paper P&L,
    computed over the FULL history of resolved markets, not just whatever
    was newly resolved in this run -- this is what makes the dashboard's
    "Track record" section an actual running total instead of resetting
    to near-zero every cycle.

This is the honesty check for the whole pilot: run the scanner for a few
weeks first, then check this to see if the "edges" it found were real.
"""

import argparse
import sys
from datetime import date, datetime

from . import storage
from .config import CITIES, EDGE_THRESHOLD
from .forecast import fetch_actual_max_temp


def _latest_snapshot_rows(conn, event_slug: str):
    """One row per bracket for this event: the most recent snapshot before
    resolution. Without this, a bracket scanned every 30 min over many hours
    would count many times for what is really one continuing opportunity."""
    cur = conn.execute(
        """SELECT s.bracket_label, s.low, s.high, s.market_price,
                  s.model_probability, s.edge
           FROM snapshots s
           INNER JOIN (
               SELECT bracket_label, MAX(id) AS max_id
               FROM snapshots WHERE event_slug = ?
               GROUP BY bracket_label
           ) latest ON s.bracket_label = latest.bracket_label
                    AND s.id = latest.max_id
           WHERE s.event_slug = ?
           ORDER BY s.id""",
        (event_slug, event_slug),
    )
    return cur.fetchall()


def _entry_snapshot_rows(conn, event_slug: str, threshold: float):
    """For each bracket in this event, the FIRST snapshot where the edge
    signal fired (|edge| >= threshold). This is the price a real trade
    would enter at -- the moment the opportunity was first spotted -- not
    whatever price happened to be logged right before the market closed
    (which is what _latest_snapshot_rows gives you, and is the wrong price
    to simulate a "buy" at)."""
    cur = conn.execute(
        """SELECT s.bracket_label, s.low, s.high, s.market_price,
                  s.model_probability, s.edge, s.ts
           FROM snapshots s
           INNER JOIN (
               SELECT bracket_label, MIN(id) AS entry_id
               FROM snapshots
               WHERE event_slug = ? AND ABS(edge) >= ?
               GROUP BY bracket_label
           ) entry ON s.bracket_label = entry.bracket_label
                   AND s.id = entry.entry_id
           WHERE s.event_slug = ?
           ORDER BY s.id""",
        (event_slug, threshold, event_slug),
    )
    return cur.fetchall()


def resolve_pending(conn) -> tuple[list[dict], list[str]]:
    """Resolve every finished-but-unresolved market. Returns
    (newly_resolved, skipped) -- both just describe this run's activity,
    not the cumulative history."""
    rows = storage.unresolved_event_slugs(conn)
    today = date.today()

    newly_resolved = []
    skipped = []

    for event_slug, city, target_date_str in rows:
        target_date = datetime.strptime(target_date_str, "%Y-%m-%d").date()
        if target_date >= today:
            continue  # market hasn't finished yet

        cfg = CITIES.get(city)
        if cfg is None:
            continue

        try:
            actual = fetch_actual_max_temp(
                cfg["lat"], cfg["lon"], cfg["timezone"], target_date_str, cfg["unit"]
            )
        except Exception as exc:
            # A single flaky network call must not take down the whole run
            # (and with it, that cycle's already-scanned data).
            skipped.append(f"{city} {target_date_str}: fetch failed ({exc})")
            continue

        if actual is None:
            skipped.append(f"{city} {target_date_str}: no observation yet")
            continue

        winning_label = None
        for label, low, high, *_ in _latest_snapshot_rows(conn, event_slug):
            if low <= actual < high:
                winning_label = label
                break

        storage.save_resolution(conn, event_slug, city, target_date_str, actual, winning_label)
        newly_resolved.append({
            "city": city,
            "target_date": target_date_str,
            "actual": actual,
            "winning_bracket": winning_label,
        })

    return newly_resolved, skipped


def per_event_records(conn, threshold: float) -> list[dict]:
    """One record per resolved event (city + day), each carrying its own
    Brier contribution and the individual paper bets placed that day. This
    is the single source of truth compute_summary() rolls up -- and what
    lets the dashboard show a day-by-day breakdown instead of just a
    lifetime total, so a bad run isn't hidden inside a good average."""
    records = []
    cur = conn.execute(
        "SELECT event_slug, city, target_date, actual_temp FROM resolutions "
        "ORDER BY target_date, city"
    )
    for event_slug, city, target_date, actual in cur.fetchall():
        if actual is None:
            continue

        # Brier score uses the freshest read on the model right before the
        # market closed -- that's the right question for "how good was our
        # forecast." Paper P&L below uses a different, earlier snapshot on
        # purpose: "would this have made money if traded" needs the price at
        # the moment the opportunity was first spotted, not the final one.
        brier_sum = 0.0
        brier_n = 0
        for label, low, high, market_price, model_prob, edge in _latest_snapshot_rows(conn, event_slug):
            in_bracket = low <= actual < high
            outcome = 1.0 if in_bracket else 0.0
            brier_sum += (model_prob - outcome) ** 2
            brier_n += 1

        bets = []
        for label, low, high, market_price, model_prob, edge, ts in _entry_snapshot_rows(conn, event_slug, threshold):
            in_bracket = low <= actual < high
            if edge > 0:
                stake = market_price
                returned = 1.0 if in_bracket else 0.0
            else:
                stake = 1 - market_price
                returned = 1.0 if not in_bracket else 0.0
            bets.append({
                "label": label,
                "entry_time": datetime.fromisoformat(ts),
                "edge": edge,
                "market_price": market_price,
                "model_probability": model_prob,
                "stake": stake,
                "returned": returned,
                "pnl": returned - stake,
            })

        day_staked = sum(b["stake"] for b in bets)
        day_return = sum(b["returned"] for b in bets)
        records.append({
            "event_slug": event_slug,
            "city": city,
            "target_date": target_date,
            "actual": actual,
            "brier": (brier_sum / brier_n) if brier_n else None,
            "n_brackets": brier_n,
            "bets": bets,
            "day_staked": day_staked,
            "day_return": day_return,
            "day_pnl": day_return - day_staked,
        })
    return records


def render_pnl_chart_svg(records: list[dict], width: int = 900, height: int = 160, pad: int = 24) -> str:
    """Tiny dependency-free line chart of cumulative paper P&L across
    resolved days. Plain inline SVG on purpose -- no JS charting library to
    fetch from a CDN that might be unreachable (or gone) a month from now."""
    if not records:
        return ""

    cum = []
    total = 0.0
    for r in records:
        total += r["day_pnl"]
        cum.append(total)

    n = len(cum)
    y_min = min(0.0, min(cum))
    y_max = max(0.0, max(cum))
    if y_max == y_min:
        y_max += 1.0

    def x_of(i):
        return pad + (i / max(n - 1, 1)) * (width - 2 * pad)

    def y_of(v):
        return height - pad - ((v - y_min) / (y_max - y_min)) * (height - 2 * pad)

    points = " ".join(f"{x_of(i):.1f},{y_of(v):.1f}" for i, v in enumerate(cum))
    zero_y = y_of(0.0)
    dots = "".join(
        f'<circle cx="{x_of(i):.1f}" cy="{y_of(v):.1f}" r="3.5" '
        f'fill="{"#4ade80" if v >= 0 else "#f87171"}">'
        f'<title>{records[i]["city"]} {records[i]["target_date"]}: cumulative P&amp;L {v:+.2f}</title>'
        f"</circle>"
        for i, v in enumerate(cum)
    )
    return (
        f'<svg viewBox="0 0 {width} {height}" width="100%" height="{height}" '
        f'preserveAspectRatio="none" role="img" aria-label="Cumulative paper P&amp;L by resolved day">'
        f'<line x1="{pad}" y1="{zero_y:.1f}" x2="{width - pad}" y2="{zero_y:.1f}" '
        f'stroke="#2a2e37" stroke-dasharray="4,3"/>'
        f'<polyline points="{points}" fill="none" stroke="#3b82f6" stroke-width="2"/>'
        f"{dots}"
        f"</svg>"
    )


def per_city_summary(records: list[dict]) -> list[dict]:
    """Roll per_event_records up by city, so a city with a real edge (or a
    bad one) isn't hidden inside a single blended average. Sorted best ROI
    first -- purely for readability, not a claim that the ranking is
    reliable yet (each city typically has very few resolved days so far)."""
    by_city: dict[str, dict] = {}
    for r in records:
        c = by_city.setdefault(r["city"], {
            "city": r["city"], "n_resolved": 0,
            "brier_sum": 0.0, "n_brier": 0,
            "n_bets": 0, "staked": 0.0, "returned": 0.0,
        })
        c["n_resolved"] += 1
        if r["brier"] is not None:
            c["brier_sum"] += r["brier"] * r["n_brackets"]
            c["n_brier"] += r["n_brackets"]
        c["n_bets"] += len(r["bets"])
        c["staked"] += r["day_staked"]
        c["returned"] += r["day_return"]

    summaries = []
    for c in by_city.values():
        pnl = c["returned"] - c["staked"]
        summaries.append({
            "city": c["city"],
            "n_resolved": c["n_resolved"],
            "brier_score": (c["brier_sum"] / c["n_brier"]) if c["n_brier"] else None,
            "n_bets": c["n_bets"],
            "staked": c["staked"],
            "returned": c["returned"],
            "pnl": pnl,
            "roi": (pnl / c["staked"]) if c["staked"] else None,
        })
    summaries.sort(key=lambda s: s["roi"] if s["roi"] is not None else float("-inf"), reverse=True)
    return summaries


def compute_summary(conn, threshold: float) -> dict:
    """Brier score and paper P&L over every market resolved so far, ever,
    plus a day-by-day breakdown for transparency."""
    records = per_event_records(conn, threshold)

    total_brier = sum(r["brier"] * r["n_brackets"] for r in records if r["brier"] is not None)
    n_brier = sum(r["n_brackets"] for r in records)
    paper_staked = sum(r["day_staked"] for r in records)
    paper_return = sum(r["day_return"] for r in records)
    n_bets = sum(len(r["bets"]) for r in records)

    return {
        "brier_score": (total_brier / n_brier) if n_brier else None,
        "n_brier": n_brier,
        "n_bets": n_bets,
        "paper_staked": paper_staked,
        "paper_return": paper_return,
        "paper_pnl": paper_return - paper_staked,
        "paper_roi": ((paper_return - paper_staked) / paper_staked) if paper_staked else None,
        "threshold": threshold,
        "total_resolved": len(records),
        "daily": records,
        "by_city": per_city_summary(records),
        "pnl_chart_svg": render_pnl_chart_svg(records),
    }


def resolve_all(conn, threshold: float) -> dict:
    """Resolve anything newly finished, then return the cumulative summary
    across all resolved markets to date. This is what callers should use."""
    newly_resolved, skipped = resolve_pending(conn)
    summary = compute_summary(conn, threshold)
    summary["resolved"] = newly_resolved
    summary["skipped"] = skipped
    return summary


def print_summary(summary: dict):
    for r in summary["resolved"]:
        print(f"[{r['city']} {r['target_date']}] actual={r['actual']}, "
              f"winning bracket={r['winning_bracket']}")
    for s in summary["skipped"]:
        print(s, file=sys.stderr)

    print("\n--- Summary (cumulative, all resolved markets) ---")
    if summary["n_brier"]:
        print(f"Brier score over {summary['n_brier']} bracket outcomes: "
              f"{summary['brier_score']:.4f}  "
              f"(0 = perfect, 0.25 = coin-flip-ish, lower is better)")
    else:
        print("No resolved markets to score yet.")

    if summary["n_bets"]:
        print(f"Paper trades at |edge| >= {summary['threshold']}: {summary['n_bets']} bets, "
              f"staked {summary['paper_staked']:.2f}, returned {summary['paper_return']:.2f}, "
              f"P&L {summary['paper_pnl']:+.2f} ({summary['paper_roi']:+.1%} ROI) -- NO REAL MONEY")
    else:
        print(f"No paper trades met the |edge| >= {summary['threshold']} threshold yet.")


def main():
    parser = argparse.ArgumentParser(description="Resolve past scans against actual outcomes")
    parser.add_argument("--threshold", type=float, default=EDGE_THRESHOLD,
                         help="minimum |edge| to count as a paper trade")
    args = parser.parse_args()

    conn = storage.get_connection()
    summary = resolve_all(conn, args.threshold)
    conn.close()
    print_summary(summary)


if __name__ == "__main__":
    main()
