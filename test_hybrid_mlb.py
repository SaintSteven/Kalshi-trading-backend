from hybrid_mlb import (
    CLVRecord,
    DiscoverySignal,
    HybridCandidateRequest,
    QCCheck,
    evaluate_candidate,
    summarize_clv,
)


def strong_game_candidate(price=51):
    return HybridCandidateRequest(
        market_type="GAME",
        selection="Guardians F5 ML",
        kalshi_price_cents=price,
        model_fair_probability=0.57,
        external_market_probability=0.56,
        signals=[
            DiscoverySignal(source="Model A", kind="MODEL", independence_group="model-a"),
            DiscoverySignal(source="Capper B", kind="HANDICAPPER", independence_group="capper-b"),
            DiscoverySignal(source="Sharp close", kind="SHARP_MARKET", independence_group="market"),
            DiscoverySignal(source="Projection C", kind="PROJECTION", independence_group="projection-c"),
        ],
        qc_checks=[QCCheck(label="starter", status="PASS"), QCCheck(label="lineup", status="PASS")],
    )


def test_grade_a_clean_price_is_buy():
    result = evaluate_candidate(strong_game_candidate())
    assert result.discovery_grade == "A"
    assert result.qc_status == "CLEAN"
    assert result.decision == "BUY"
    assert result.maximum_entry_cents == 51


def test_qc_failure_is_veto():
    request = strong_game_candidate(price=45)
    request.qc_checks.append(QCCheck(label="pitcher scratched", status="FAIL"))
    result = evaluate_candidate(request)
    assert result.decision == "PASS"
    assert result.qc_status == "FAIL"


def test_single_source_requires_extra_edge_and_pending_qc_cannot_buy():
    result = evaluate_candidate(HybridCandidateRequest(
        market_type="STRIKEOUT",
        selection="Pitcher over 5.5 Ks",
        kalshi_price_cents=45,
        model_fair_probability=0.60,
        signals=[DiscoverySignal(source="Our K model", kind="MODEL")],
        qc_checks=[QCCheck(label="umpire", status="PENDING")],
    ))
    assert result.discovery_grade == "C"
    assert result.required_edge_points == 7
    assert result.decision == "WATCH"


def test_clv_summary_groups_sources():
    result = summarize_clv([
        CLVRecord(candidate_id="a", market_type="GAME", discovery_grade="A", source_names=["X"], entry_price_cents=51, close_price_cents=55, stake=1, profit_loss=0.8),
        CLVRecord(candidate_id="b", market_type="STRIKEOUT", discovery_grade="B", source_names=["X", "Y"], entry_price_cents=48, close_price_cents=47, stake=1, profit_loss=-1),
    ])
    assert result["overall"]["average_clv_cents"] == 1.5
    assert result["by_source"]["X"]["bets"] == 2
