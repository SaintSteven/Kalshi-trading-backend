"""Transparent recommendation-confidence scoring.

This is a research/QC score, not a probability that the bet will win.  It is
intended to distinguish stable inputs from fragile ones while the system is
being paper tested and backtested.
"""
from statistics import mean, pstdev

from projection_inputs import PitcherModelInput

QUALITY_BASE = {"HIGH": 90, "MEDIUM": 72, "LOW": 52}


def _clamp(value: float, low: int = 1, high: int = 99) -> int:
    return int(round(max(low, min(high, value))))


def build_confidence(d: PitcherModelInput) -> dict:
    reasons: list[str] = []
    risk_flags: list[str] = []

    skill = float(QUALITY_BASE[d.data_quality.pitcher_skill])
    lineup = float(QUALITY_BASE[d.data_quality.lineup])
    workload = float(QUALITY_BASE[d.data_quality.workload])
    recent_change = float(QUALITY_BASE[d.data_quality.recent_change])

    # Skill sample reliability.
    if d.season_batters_faced >= 400:
        skill += 4
        reasons.append("Strong current-season pitcher sample.")
    elif d.season_batters_faced < 200:
        skill -= 10
        risk_flags.append("Limited current-season pitcher sample.")

    if d.career_batters_faced >= 1000:
        skill += 3
    elif d.career_batters_faced < 300:
        skill -= 5
        risk_flags.append("Limited career sample.")

    # Lineup certainty.
    if d.lineup_confirmed:
        lineup += 6
        reasons.append("Opponent lineup confirmed.")
    else:
        lineup -= 14
        risk_flags.append("Opponent lineup is projected, not confirmed.")

    # Workload/leash certainty.
    if not d.starter_confirmed:
        workload -= 30
        risk_flags.append("Starting assignment is not confirmed.")
    else:
        reasons.append("Starting assignment confirmed.")

    role_penalties = {
        "NORMAL": 0,
        "LIMITED": 18,
        "OPENER": 28,
        "BULK": 16,
        "RETURNING_FROM_INJURY": 24,
    }
    role_penalty = role_penalties.get(d.starter_role, 10)
    workload -= role_penalty
    if role_penalty:
        risk_flags.append(f"Non-standard starter role: {d.starter_role}.")

    workload_range = max(0, d.workload_ceiling - d.workload_floor)
    if workload_range <= 6:
        workload += 7
        reasons.append("Narrow projected workload range.")
    elif workload_range >= 12:
        workload -= 12
        risk_flags.append("Wide projected workload range.")

    # Recent pitch-count stability is a direct, explainable uncertainty input.
    stability = 55.0
    pitch_count_cv = None
    if len(d.recent_pitch_counts) >= 3:
        avg_pc = mean(d.recent_pitch_counts)
        sd_pc = pstdev(d.recent_pitch_counts)
        pitch_count_cv = sd_pc / avg_pc if avg_pc > 0 else 1.0
        stability = 92 - min(45, pitch_count_cv * 180)
        if pitch_count_cv <= 0.08:
            workload += 7
            reasons.append("Recent pitch counts are very stable.")
        elif pitch_count_cv >= 0.20:
            workload -= 10
            risk_flags.append("Recent pitch counts are volatile.")
    elif d.recent_pitch_counts:
        stability = 62
        workload -= 4
        risk_flags.append("Only a small recent pitch-count sample is available.")
    else:
        stability = 42
        workload -= 12
        risk_flags.append("No recent pitch-count history is available.")

    # Recent-change inputs should not add confidence unless supported by good data.
    if abs(d.velocity_change_mph) >= 1.0 or abs(d.whiff_rate_change) >= 0.04:
        if d.data_quality.recent_change == "HIGH":
            recent_change += 4
            reasons.append("Recent skill-change signal has high-quality support.")
        else:
            recent_change -= 10
            risk_flags.append("Material recent-change signal has limited support.")

    skill = _clamp(skill)
    lineup = _clamp(lineup)
    workload = _clamp(workload)
    recent_change = _clamp(recent_change)
    stability = _clamp(stability)

    # The weights emphasize pitcher skill and workload.  Stability is separated
    # from workload so users can see why two similar means receive different QC.
    base_score = (
        skill * 0.30
        + lineup * 0.20
        + workload * 0.28
        + recent_change * 0.07
        + stability * 0.15
    )

    # Multiple risk flags compound fragility, but the penalty is deliberately
    # capped so one missing optional input cannot zero out a recommendation.
    uncertainty_penalty = min(16, max(0, len(risk_flags) - 1) * 2)
    overall = _clamp(base_score - uncertainty_penalty)

    if overall >= 80:
        tier = "HIGH"
        action = "FULL PAPER STAKE"
    elif overall >= 68:
        tier = "MEDIUM"
        action = "REDUCED PAPER STAKE"
    else:
        tier = "LOW"
        action = "WATCH / PASS"

    return {
        "version": "confidence-v2",
        "overall": overall,
        "tier": tier,
        "recommended_action": action,
        "pitcher_skill": skill,
        "lineup": lineup,
        "workload": workload,
        "recent_change": recent_change,
        "workload_stability": stability,
        "workload_range_bf": workload_range,
        "pitch_count_cv": None if pitch_count_cv is None else round(pitch_count_cv, 3),
        "uncertainty_penalty": uncertainty_penalty,
        "reasons": reasons,
        "risk_flags": risk_flags,
    }
