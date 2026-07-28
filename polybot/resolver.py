"""Check logged snapshots against what actually happened.

For every scanned event whose target date has passed, this pulls the
observed max temperature, finds the bracket that actually won, and reports:
  - calibration (Brier score) of the model's probabilities
  - simulated paper P&L if we had bought "Yes" at the logged market_price on
    every bracket flagged as an edge (no real money, no real orders)

This is the honesty check for the whole pilot: run the scanner for a few
weeks first, then run this to see if the "edges" it found were real.
"""

import argparse
import sys
from datetime import date, datetime

from . import storage
from .config import CITIES, EDGE_THRESHOLD
from .forecast import fetch_actual_max_temp


def resolve_all(conn, threshold: float) -> dict:
    """Resolve every finished-but-unresolved market and return a summary
    dict. Never prints -- callers (CLI or web dashboard) decide how to
    present it."""
    rows = storage.unresolved_event_slugs(conn)
    today = date.today()

    total_brier = 0.0
    n_brier = 0
    paper_staked = 0.0
    paper_return = 0.0
    n_bets = 0
    resolved = []
    skipped = []

    for event_slug, city, target_date_str in rows:
        target_date = datetime.strptime(target_date_str, "%Y-%m-%d").date()
        if target_date >= today:
            continue  # market hasn't finished yet

        cfg = CITIES.get(city)
        if cfg is None:
            continue

        actual = fetch_actual_max_temp(
            cfg["lat"], cfg["lon"], cfg["timezone"], target_date_str, cfg["unit"]
        )
        if actual is None:
            skipped.append(f"{city} {target_date_str}: no observation yet")
            continue

        cur = conn.execute(
            """SELECT bracket_label, low, high, market_price, model_probability, edge
               FROM snapshots WHERE event_slug = ? ORDER BY id""",
            (event_slug,),
        )
        snapshot_rows = cur.fetchall()

        winning_label = None
        for label, low, high, market_price, model_prob, edge in snapshot_rows:
            in_bracket = low <= actual < high
            outcome = 1.0 if in_bracket else 0.0
            total_brier += (model_prob - outcome) ** 2
            n_brier += 1
            if in_bracket:
                winning_label = label

            if abs(edge) >= threshold:
                # Paper-trade the direction the edge pointed: buy "Yes" if
                # our model thought it more likely than the market did,
                # buy "No" (i.e. bet against) otherwise.
                n_bets += 1
                if edge > 0:
                    paper_staked += market_price
                    paper_return += 1.0 if in_bracket else 0.0
                else:
                    paper_staked += (1 - market_price)
                    paper_return += 1.0 if not in_bracket else 0.0

        storage.save_resolution(conn, event_slug, city, target_date_str, actual, winning_label)
        resolved.append({
            "city": city,
            "target_date": target_date_str,
            "actual": actual,
            "winning_bracket": winning_label,
        })

    return {
        "resolved": resolved,
        "skipped": skipped,
        "brier_score": (total_brier / n_brier) if n_brier else None,
        "n_brier": n_brier,
        "n_bets": n_bets,
        "paper_staked": paper_staked,
        "paper_return": paper_return,
        "paper_pnl": paper_return - paper_staked,
        "paper_roi": ((paper_return - paper_staked) / paper_staked) if paper_staked else None,
        "threshold": threshold,
    }


def print_summary(summary: dict):
    for r in summary["resolved"]:
        print(f"[{r['city']} {r['target_date']}] actual={r['actual']}, "
              f"winning bracket={r['winning_bracket']}")
    for s in summary["skipped"]:
        print(s, file=sys.stderr)

    print("\n--- Summary ---")
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
