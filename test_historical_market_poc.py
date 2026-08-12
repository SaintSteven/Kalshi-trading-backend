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

from datetime import timezone
from historical_market_poc import _parse_iso, _target_route


def test_cutoff_routing_recent_and_historical():
    cutoff = _parse_iso("2026-05-15T00:00:00Z")
    assert cutoff is not None
    assert _target_route("2026-07-10", cutoff) == "recent"
    assert _target_route("2026-04-01", cutoff) == "historical"


def test_parse_iso_normalizes_zulu():
    dt = _parse_iso("2026-05-15T00:00:00Z")
    assert dt is not None
    assert dt.tzinfo is not None
    assert dt.utcoffset().total_seconds() == 0

from historical_market_poc import _close, _finalize_row


def test_current_fixed_point_close_dollars_is_supported():
    assert _close({"close_dollars": "0.5600"}) == "0.5600"
    assert _to_cents(_close({"close_dollars": "0.5600"})) == 56


def test_finalize_row_reconstructs_yes_and_no_asks_from_fixed_point_candle():
    market = {
        "ticker": "KXMLBKS-26AUG102138TEXLAA-LAARDETMERS48-6",
        "title": "Reid Detmers: 6+ strikeouts",
    }
    game_start = _game_start_from_ticker(market["ticker"])
    target_ts = int((game_start.timestamp()) - 2 * 3600)
    candles = [{
        "end_period_ts": target_ts,
        "yes_ask": {"close_dollars": "0.3300"},
        "yes_bid": {"close_dollars": "0.3100"},
        "price": {"close_dollars": "0.3200"},
        "volume_fp": "12.00",
        "open_interest_fp": "20.00",
    }]
    row = _finalize_row(
        market=market,
        historical=False,
        candles=candles,
        hours_before_first_pitch=2,
        retrieval_method="batch_recent",
    )
    assert row["yes_ask_cents"] == 33
    assert row["no_ask_cents"] == 69
    assert row["last_trade_cents"] == 32
    assert row["usable_entry_quote"] is True
    assert row["retrieval_method"] == "batch_recent"
