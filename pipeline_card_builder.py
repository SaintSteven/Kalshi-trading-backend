from pricing_engine import evaluate_market


DECISION_RANK = {
    "MODEL EDGE": 0,
    "WATCH": 1,
    "PASS": 2,
    "INSUFFICIENT DATA": 3,
}


def _threshold_number(value: str) -> int:
    digits = "".join(ch for ch in str(value) if ch.isdigit())
    return int(digits) if digits else 999


def _ranking_key(rec):
    return (
        DECISION_RANK.get(rec.decision, 99),
        -(rec.adjusted_edge_points if rec.adjusted_edge_points is not None else -999),
        -(rec.raw_edge_points if rec.raw_edge_points is not None else -999),
        -rec.confidence.get("overall", 0),
        _threshold_number(rec.threshold),
    )


def _paper_stake(rec, request, remaining_daily_budget):
    """Size a paper recommendation conservatively.

    Stakes are capped by the user's max bet and remaining daily budget.
    The sizing tiers intentionally remain simple while the model is being
    validated.
    """
    if rec.decision != "MODEL EDGE" or rec.adjusted_edge_points is None:
        return 0.0

    edge = rec.adjusted_edge_points
    confidence = rec.confidence.get("overall", 0)

    if edge >= 10 and confidence >= 70:
        multiplier = 1.0
    elif edge >= 7:
        multiplier = 0.75
    else:
        multiplier = 0.50

    stake = min(request.max_bet * multiplier, remaining_daily_budget)
    return round(max(0.0, stake), 2)


def build_card_from_pipeline(markets, request, pipeline):
    evaluated = [
        evaluate_market(
            market,
            pipeline.projections.get(market.player.strip().lower()),
            request.minimum_edge_points,
        )
        for market in markets
    ]

    # Surface only the strongest available ladder for each pitcher.
    # This prevents a single projection from creating several highly
    # correlated recommendations on 4+, 5+, 6+, etc.
    best_by_pitcher = {}
    for rec in evaluated:
        key = rec.player.strip().lower()
        current = best_by_pitcher.get(key)
        if current is None or _ranking_key(rec) < _ranking_key(current):
            best_by_pitcher[key] = rec

    recommendations = list(best_by_pitcher.values())
    recommendations.sort(
        key=lambda rec: (
            DECISION_RANK.get(rec.decision, 99),
            -(rec.adjusted_edge_points if rec.adjusted_edge_points is not None else -999),
            -rec.confidence.get("overall", 0),
            rec.player,
        )
    )

    # Keep the daily paper exposure modest during validation: no more than
    # 5% of bankroll, minus anything already committed today.
    daily_cap = max(0.0, request.bankroll * 0.05 - request.already_committed_today)
    remaining = daily_cap

    sized = []
    for rec in recommendations:
        stake = _paper_stake(rec, request, remaining)
        remaining = round(max(0.0, remaining - stake), 2)

        reasons = list(rec.reasons)
        if rec.decision == "MODEL EDGE":
            reasons.append(
                f"Best ladder selected for this pitcher; paper stake ${stake:.2f}."
            )
        else:
            reasons.append("Best available ladder for this pitcher; no paper stake.")

        sized.append(
            rec.model_copy(
                update={
                    "suggested_stake": stake,
                    "reasons": reasons,
                }
            )
        )

    matched = sum(
        1
        for market in markets
        if market.player.strip().lower() in pipeline.projections
    )
    return sized, matched
