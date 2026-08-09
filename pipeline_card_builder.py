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


def _conviction_units(rec):
    """Raw conviction units before calibration guardrails or bankroll limits."""
    if rec.decision != "MODEL EDGE" or rec.adjusted_edge_points is None:
        return 0.0
    confidence = rec.confidence.get("overall", 0)
    edge = rec.adjusted_edge_points
    if confidence >= 90 and edge >= 10:
        return 2.0
    if confidence >= 85 and edge >= 7.5:
        return 1.5
    if confidence >= 80 and edge >= 5:
        return 1.0
    if confidence >= 75 and edge >= 5:
        return 0.5
    return 0.0


def _research_reason(rec):
    if rec.decision != "MODEL EDGE":
        return None
    # Existing ladder-specific calibration failure.
    if rec.side == "YES" and rec.threshold == "4+":
        return "4+ YES CALIBRATION GUARDRAIL"
    # v2.5: prospective data showed very large claimed edges were not more reliable.
    # Keep them visible and settle them, but do not deploy capital while audited.
    if rec.uncalibrated_adjusted_edge_points is not None and rec.uncalibrated_adjusted_edge_points >= 15.0:
        return "EXTREME DISAGREEMENT — 15+ RAW ADJUSTED EDGE"
    return None


def _is_research_only(rec):
    return _research_reason(rec) is not None


def _paper_stake(rec, request, remaining_daily_budget):
    conviction_units = _conviction_units(rec)
    research_reason = _research_reason(rec)
    research_only = research_reason is not None
    research_units = conviction_units if research_only else 0.0
    research_stake = round(research_units * request.max_bet, 2)

    units = 0.0 if research_only else conviction_units
    unlimited = round(units * request.max_bet, 2)
    stake = round(min(unlimited, remaining_daily_budget), 2)
    if research_only:
        status = f"RESEARCH ONLY — {research_reason}"
    elif units <= 0:
        status = "NO QUALIFYING STAKE"
    elif stake >= unlimited:
        status = "FULL MODEL STAKE"
    elif stake > 0:
        status = "PARTIALLY CAPPED BY DAILY BUDGET"
    else:
        status = "QUALIFIED — DAILY BUDGET EXHAUSTED"
    return units, unlimited, stake, status, research_only, research_units, research_stake, research_reason


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
        units, unlimited_stake, stake, stake_status, research_only, research_units, research_stake, research_reason = _paper_stake(rec, request, remaining)
        remaining = round(max(0.0, remaining - stake), 2)

        reasons = list(rec.reasons)
        confidence_reasons = rec.confidence.get("reasons", [])
        risk_flags = rec.confidence.get("risk_flags", [])
        if confidence_reasons:
            reasons.append("Confidence positives: " + "; ".join(confidence_reasons[:3]))
        if risk_flags:
            reasons.append("Confidence risks: " + "; ".join(risk_flags[:3]))
        if rec.decision == "MODEL EDGE":
            if research_only:
                reasons.append(
                    f"{research_reason}: RESEARCH ONLY. Raw conviction {research_units:.1f} units "
                    f"(${research_stake:.2f} hypothetical research stake) is still tracked, but deployable model "
                    f"units and paper stake are $0.00 while this cohort is audited."
                )
            else:
                reasons.append(
                    f"Best ladder selected for this pitcher; model rating {units:.1f} units; "
                    f"uncapped stake ${unlimited_stake:.2f}; paper stake ${stake:.2f}. "
                    f"{stake_status}."
                )
        else:
            reasons.append("Best available ladder for this pitcher; no paper stake.")

        sized.append(
            rec.model_copy(
                update={
                    "model_units": units,
                    "unlimited_bankroll_stake": unlimited_stake,
                    "research_only": research_only,
                    "research_units": research_units,
                    "research_stake": research_stake,
                    "research_reason": research_reason,
                    "suggested_stake": stake,
                    "stake_status": stake_status,
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
