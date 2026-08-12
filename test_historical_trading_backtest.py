from historical_trading_backtest import _record_from_rec
from models import PaperRecommendation


def _rec(side="YES", price=25, threshold="6+"):
    return PaperRecommendation(
        ticker="TEST", player="Pitcher", threshold=threshold, side=side,
        market_price_cents=price, fair_probability=.4, calibrated_fair_probability=.4,
        raw_edge_points=15, adjusted_edge_points=12, projected_strikeouts=5.5,
        baseline_k_pct=.25, adjusted_k_pct=.25, expected_batters_faced=24,
        workload_floor=20, workload_ceiling=28, confidence={"overall": 85, "tier": "HIGH"},
        decision="MODEL EDGE", model_units=1, unlimited_bankroll_stake=1,
        suggested_stake=1, reasons=[], warnings=[]
    )


def test_record_preserves_point_in_time_price_and_outcome():
    r = _record_from_rec(_rec(), "2026-07-10", 7, 1.0, "2.6.5-frozen")
    assert r.entry_price_cents == 25
    assert r.actual_strikeouts == 7
    assert r.stake == 1.0
    assert r.model_version == "2.6.5-frozen"
