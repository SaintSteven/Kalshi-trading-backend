from models import PaperRecommendation


def evaluate_market(m, p, min_edge):
    if not p or p["status"] != "READY":
        return PaperRecommendation(
            ticker=m.ticker,
            player=m.player,
            threshold=m.threshold,
            away_team=m.away_team,
            away_team_name=m.away_team_name,
            home_team=m.home_team,
            home_team_name=m.home_team_name,
            matchup=m.matchup,
            game_start_time=m.game_start_time,
            game_start_display=m.game_start_display,
            game_status=m.game_status,
            side="NONE",
            market_price_cents=None,
            fair_probability=None,
            raw_edge_points=None,
            adjusted_edge_points=None,
            projected_strikeouts=None if not p else p["projected_strikeouts"],
            baseline_k_pct=None if not p else p["baseline_k_pct"],
            adjusted_k_pct=None if not p else p["adjusted_k_pct"],
            expected_batters_faced=None if not p else p["expected_batters_faced"],
            workload_floor=None if not p else p["workload_floor"],
            workload_ceiling=None if not p else p["workload_ceiling"],
            confidence={} if not p else p["confidence"],
            decision="INSUFFICIENT DATA",
            suggested_stake=0,
            reasons=["No validated projection available."],
            warnings=[] if not p else p["warnings"],
        )

    yes_fair = p["ladder_probabilities"].get(m.threshold)
    if yes_fair is None:
        side = "NONE"
        price = fair = raw = adjusted = None
        decision = "PASS"
        reasons = ["No simulated probability for this ladder."]
    else:
        no_fair = 1.0 - yes_fair
        yes_edge = (
            yes_fair * 100.0 - m.yes_ask_cents
            if m.yes_ask_cents is not None
            else -999.0
        )
        no_edge = (
            no_fair * 100.0 - m.no_ask_cents
            if m.no_ask_cents is not None
            else -999.0
        )

        if yes_edge >= no_edge:
            side = "YES"
            price = m.yes_ask_cents
            fair = yes_fair
            raw = yes_edge
        else:
            side = "NO"
            price = m.no_ask_cents
            fair = no_fair
            raw = no_edge

        adjusted = raw * p["confidence"]["overall"] / 100.0
        confidence_score = p["confidence"].get("overall", 0)
        decision = (
            "PASS"
            if raw < 0
            else "MODEL EDGE"
            if adjusted >= min_edge and confidence_score >= 68
            else "WATCH"
        )
        reasons = [
            f"Best side {side}.",
            f"Raw edge {raw:.1f}.",
            f"Adjusted edge {adjusted:.1f}.",
            f"Confidence {p['confidence'].get('overall', 0)}/100 "
            f"({p['confidence'].get('tier', 'UNRATED')}).",
        ]
        if adjusted >= min_edge and confidence_score < 68:
            reasons.append("Edge cleared the numeric threshold, but confidence QC held it to WATCH.")

    return PaperRecommendation(
        ticker=m.ticker,
        player=m.player,
        threshold=m.threshold,
        away_team=m.away_team,
        away_team_name=m.away_team_name,
        home_team=m.home_team,
        home_team_name=m.home_team_name,
        matchup=m.matchup,
        game_start_time=m.game_start_time,
        game_start_display=m.game_start_display,
        game_status=m.game_status,
        side=side,
        market_price_cents=price,
        # API probabilities are decimals from 0.0 to 1.0.
        # The frontend converts this to a displayed percentage.
        fair_probability=None if fair is None else round(fair, 4),
        raw_edge_points=None if raw is None else round(raw, 1),
        adjusted_edge_points=None if adjusted is None else round(adjusted, 1),
        projected_strikeouts=p["projected_strikeouts"],
        baseline_k_pct=p["baseline_k_pct"],
        adjusted_k_pct=p["adjusted_k_pct"],
        expected_batters_faced=p["expected_batters_faced"],
        workload_floor=p["workload_floor"],
        workload_ceiling=p["workload_ceiling"],
        confidence=p["confidence"],
        decision=decision,
        suggested_stake=0,
        reasons=reasons,
        warnings=p["warnings"],
    )
