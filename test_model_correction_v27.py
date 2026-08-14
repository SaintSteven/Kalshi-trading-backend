from model_correction_v27 import apply_v27_reliability_correction
from historical_trading_models import HistoricalTradingBacktestRequest


def test_v27_global_reliability_correction_matches_preregistered_july_fit():
    # July diagnostic mean: 44.2% predicted, 28.9% actual.
    corrected = apply_v27_reliability_correction(0.442)
    assert abs(corrected - 0.2888) < 0.001


def test_v27_correction_never_increases_probability_and_request_opt_in_defaults_off():
    for p in [0.01, 0.10, 0.30, 0.50, 0.75, 0.99]:
        assert 0 <= apply_v27_reliability_correction(p) <= p
    req = HistoricalTradingBacktestRequest(start_date="2026-06-01", end_date="2026-06-30")
    assert req.compare_v27_candidate is False
    req2 = req.model_copy(update={"compare_v27_candidate": True})
    assert req2.compare_v27_candidate is True
