from types import SimpleNamespace

from models import Market, PaperCardRequest
from pipeline_card_builder import build_card_from_pipeline
from pricing_engine import evaluate_market


def projection():
    return {
        "status": "READY",
        "projected_strikeouts": 5.3,
        "baseline_k_pct": 0.23,
        "adjusted_k_pct": 0.24,
        "expected_batters_faced": 23.6,
        "workload_floor": 16,
        "workload_ceiling": 27,
        "confidence": {"overall": 75},
        "warnings": [],
        "ladder_probabilities": {
            "4": 0.80,
            "5": 0.627,
            "6": 0.44,
        },
    }


def market(threshold, ask):
    return Market(
        ticker=f"TEST-{threshold}",
        title="Test",
        player="Test Pitcher",
        threshold=threshold,
        yes_ask_cents=ask,
        no_ask_cents=100 - ask,
        tradable=True,
    )


def test_fair_probability_is_decimal():
    rec = evaluate_market(market("5", 41), projection(), 5)
    assert rec.fair_probability == 0.627


def test_only_best_ladder_per_pitcher_and_stake_is_sized():
    pipeline = SimpleNamespace(
        projections={"test pitcher": projection()}
    )
    request = PaperCardRequest(
        bankroll=100,
        already_committed_today=0,
        max_bet=1,
        minimum_edge_points=5,
    )
    recs, matched = build_card_from_pipeline(
        [market("4", 59), market("5", 41), market("6", 24)],
        request,
        pipeline,
    )
    assert matched == 3
    assert len(recs) == 1
    assert recs[0].threshold == "5"
    assert recs[0].suggested_stake == 1.0
