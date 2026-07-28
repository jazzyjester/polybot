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


def compute_summary(conn, threshold: float) -> dict:
    """Brier score and paper P&L over every market resolved so far, ever."""
    total_brier = 0.0
    n_brier = 0
    paper_staked = 0.0
    paper_return = 0.0
    n_bets = 0

    cur = conn.execute("SELECT event_slug, actual_temp FROM resolutions")
    for event_slug, actual in cur.fetchall():
        if actual is None:
            continue
        for label, low, high, market_price, model_prob, edge in _latest_snapshot_rows(conn, event_slug):
            in_bracket = low <= actual < high
            outcome = 1.0 if in_bracket else 0.0
            total_brier += (model_prob - outcome) ** 2
            n_brier += 1

            if abs(edge) >= threshold:
                n_bets += 1
                if edge > 0:
                    paper_staked += market_price
                    paper_return += 1.0 if in_bracket else 0.0
                else:
                    paper_staked += (1 - market_price)
                    paper_return += 1.0 if not in_bracket else 0.0

    return {
        "brier_score": (total_brier / n_brier) if n_brier else None,
        "n_brier": n_brier,
        "n_bets": n_bets,
        "paper_staked": paper_staked,
        "paper_return": paper_return,
        "paper_pnl": paper_return - paper_staked,
        "paper_roi": ((paper_return - paper_staked) / paper_staked) if paper_staked else None,
        "threshold": threshold,
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
