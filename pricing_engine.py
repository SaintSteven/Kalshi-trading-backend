from models import PaperRecommendation
from calibration_engine import (
    CALIBRATION_FACTOR,
    CALIBRATION_METHOD,
    calibrate_selected_side_probability,
)
from model_correction_v27 import apply_v27_reliability_correction, V27_METHOD


def evaluate_market(m, p, min_edge, pricing_policy="v26_baseline"):
    if not p or p["status"] != "READY":
        return PaperRecommendation(
            ticker=m.ticker, player=m.player, threshold=m.threshold,
            away_team=m.away_team, away_team_name=m.away_team_name,
            home_team=m.home_team, home_team_name=m.home_team_name,
            matchup=m.matchup, game_start_time=m.game_start_time,
            game_start_display=m.game_start_display, game_status=m.game_status,
            side="NONE", market_price_cents=None, fair_probability=None,
            calibrated_fair_probability=None, calibration_method=CALIBRATION_METHOD,
            calibration_factor=CALIBRATION_FACTOR, calibrated_edge_points=None,
            uncalibrated_adjusted_edge_points=None, raw_edge_points=None,
            adjusted_edge_points=None,
            projected_strikeouts=None if not p else p["projected_strikeouts"],
            baseline_k_pct=None if not p else p["baseline_k_pct"],
            adjusted_k_pct=None if not p else p["adjusted_k_pct"],
            expected_batters_faced=None if not p else p["expected_batters_faced"],
            workload_floor=None if not p else p["workload_floor"],
            workload_ceiling=None if not p else p["workload_ceiling"],
            confidence={} if not p else p["confidence"], decision="INSUFFICIENT DATA",
            suggested_stake=0, reasons=["No validated projection available."],
            warnings=[] if not p else p["warnings"],
        )

    raw_yes_fair = p["ladder_probabilities"].get(m.threshold)
    if raw_yes_fair is None:
        side = "NONE"; price = fair = calibrated_fair = raw = calibrated_edge = adjusted = raw_adjusted = None
        decision = "PASS"; reasons = ["No simulated probability for this ladder."]
    else:
        raw_no_fair = 1.0 - raw_yes_fair

        # v2.5.1: choose the trade side from the independent RAW model first.
        # Calibration is not allowed to flip the side or create an edge that the
        # independent model did not already see.
        yes_raw_edge = raw_yes_fair * 100.0 - m.yes_ask_cents if m.yes_ask_cents is not None else -999.0
        no_raw_edge = raw_no_fair * 100.0 - m.no_ask_cents if m.no_ask_cents is not None else -999.0

        if yes_raw_edge >= no_raw_edge:
            side = "YES"; price = m.yes_ask_cents; fair = raw_yes_fair; raw = yes_raw_edge
        else:
            side = "NO"; price = m.no_ask_cents; fair = raw_no_fair; raw = no_raw_edge

        # Conservative calibration is applied only after the raw side is fixed.
        # It can only leave the selected-side probability unchanged or reduce it.
        calibrated_fair = calibrate_selected_side_probability(fair)
        active_calibration_method = CALIBRATION_METHOD
        if pricing_policy == "v27_reliability_candidate":
            calibrated_fair = apply_v27_reliability_correction(calibrated_fair)
            active_calibration_method = V27_METHOD
        calibrated_edge = calibrated_fair * 100.0 - price if price is not None else None

        confidence_score = p["confidence"].get("overall", 0)
        raw_adjusted = raw * confidence_score / 100.0 if raw is not None else None
        adjusted = calibrated_edge * confidence_score / 100.0 if calibrated_edge is not None else None

        # RAW-EDGE GATE: calibration may refine/reduce an edge, never manufacture
        # one. A non-positive raw confidence-adjusted edge can never become a
        # MODEL EDGE recommendation regardless of the calibrated value.
        if raw_adjusted is None or raw_adjusted <= 0:
            decision = "PASS" if raw is not None and raw <= 0 else "WATCH"
        elif calibrated_edge is None or calibrated_edge < 0:
            decision = "PASS"
        elif adjusted >= min_edge and confidence_score >= 68:
            decision = "MODEL EDGE"
        else:
            decision = "WATCH"

        reasons = [
            f"Best side {side} from the raw independent model.",
            f"Raw fair {fair*100:.1f}% calibrated to {calibrated_fair*100:.1f}% ({active_calibration_method}).",
            f"Raw market edge {raw:.1f} pts; calibrated market edge {calibrated_edge:.1f} pts.",
            f"Raw confidence-adjusted edge {raw_adjusted:.1f} pts; calibrated adjusted edge {adjusted:.1f} pts.",
            f"Confidence {confidence_score}/100 ({p['confidence'].get('tier', 'UNRATED')}).",
        ]
        if raw_adjusted <= 0:
            reasons.append("RAW-EDGE GATE: the independent model did not show a positive adjusted edge, so calibration cannot create a deployable recommendation.")
        elif adjusted >= min_edge and confidence_score < 68:
            reasons.append("Calibrated edge cleared the numeric threshold, but confidence QC held it to WATCH.")

    return PaperRecommendation(
        ticker=m.ticker, player=m.player, threshold=m.threshold,
        away_team=m.away_team, away_team_name=m.away_team_name,
        home_team=m.home_team, home_team_name=m.home_team_name,
        matchup=m.matchup, game_start_time=m.game_start_time,
        game_start_display=m.game_start_display, game_status=m.game_status,
        side=side, market_price_cents=price,
        fair_probability=None if fair is None else round(fair, 4),
        calibrated_fair_probability=None if calibrated_fair is None else round(calibrated_fair, 4),
        calibration_method=(V27_METHOD if pricing_policy == "v27_reliability_candidate" else CALIBRATION_METHOD), calibration_factor=CALIBRATION_FACTOR,
        calibrated_edge_points=None if calibrated_edge is None else round(calibrated_edge, 1),
        uncalibrated_adjusted_edge_points=None if raw_adjusted is None else round(raw_adjusted, 1),
        raw_edge_points=None if raw is None else round(raw, 1),
        adjusted_edge_points=None if adjusted is None else round(adjusted, 1),
        projected_strikeouts=p["projected_strikeouts"], baseline_k_pct=p["baseline_k_pct"],
        adjusted_k_pct=p["adjusted_k_pct"], expected_batters_faced=p["expected_batters_faced"],
        workload_floor=p["workload_floor"], workload_ceiling=p["workload_ceiling"],
        confidence=p["confidence"], decision=decision, suggested_stake=0,
        reasons=reasons, warnings=p["warnings"],
    )
