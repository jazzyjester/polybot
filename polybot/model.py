"""Turn an ensemble forecast into per-bracket probabilities and compare
against Polymarket's implied prices."""


def bracket_probability(members: list[float], low: float, high: float) -> float:
    if not members:
        return 0.0
    hits = sum(1 for v in members if low <= v < high)
    return hits / len(members)


def score_brackets(brackets: list[dict], members: list[float]) -> list[dict]:
    """Attach model_probability and edge to each bracket dict."""
    scored = []
    for b in brackets:
        prob = bracket_probability(members, b["low"], b["high"])
        scored.append(
            {
                **b,
                "model_probability": prob,
                "edge": prob - b["market_price"],
                "n_members": len(members),
            }
        )
    return scored
