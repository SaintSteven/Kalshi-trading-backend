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
