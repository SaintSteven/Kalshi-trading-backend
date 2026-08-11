from datetime import datetime

from historical_market_poc import _game_start_from_ticker, _last_candle_at_or_before, _to_cents


def test_game_start_from_kxmlbks_ticker():
    dt = _game_start_from_ticker("KXMLBKS-26AUG102138TEXLAA-LAARDETMERS48-6")
    assert dt is not None
    assert dt.year == 2026 and dt.month == 8 and dt.day == 10
    assert dt.hour == 21 and dt.minute == 38


def test_price_conversion_and_no_lookahead_candle_selection():
    candles = [
        {"end_period_ts": 100, "yes_ask": {"close": "0.33"}},
        {"end_period_ts": 120, "yes_ask": {"close": "0.35"}},
        {"end_period_ts": 140, "yes_ask": {"close": "0.38"}},
    ]
    chosen = _last_candle_at_or_before(candles, 125)
    assert chosen["end_period_ts"] == 120
    assert _to_cents(chosen["yes_ask"]["close"]) == 35
