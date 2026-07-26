from market_collector import _extract_player, _extract_threshold, _to_cents, evaluate_tradability

def test_to_cents_dollar_value():
    assert _to_cents(0.58) == 58

def test_to_cents_legacy_value():
    assert _to_cents(58) == 58

def test_extract_title_fields():
    title = "Tarik Skubal: 6+ strikeouts"
    assert _extract_player(title) == "Tarik Skubal"
    assert _extract_threshold(title) == "6+"

def test_extreme_market_is_not_tradable():
    tradable, reasons = evaluate_tradability(1, 100)
    assert tradable is False and reasons

def test_reasonable_two_sided_market_is_tradable():
    tradable, reasons = evaluate_tradability(50, 55)
    assert tradable is True and reasons == []

def test_excessive_combined_ask_is_not_tradable():
    tradable, reasons = evaluate_tradability(67, 97)
    assert tradable is False
    assert any("Combined asks" in reason for reason in reasons)


def test_kalshi_ticker_date_accepts_swagger_placeholders():
    from market_collector import kalshi_ticker_date

    assert len(kalshi_ticker_date("string")) == 7
    assert len(kalshi_ticker_date("null")) == 7
    assert len(kalshi_ticker_date(None)) == 7


def test_kalshi_ticker_date_rejects_bad_date():
    import pytest
    from market_collector import kalshi_ticker_date

    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        kalshi_ticker_date("July 26, 2026")
