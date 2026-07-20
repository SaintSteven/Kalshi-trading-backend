from app.engines.market_collector import _extract_player, _extract_threshold, _to_cents

def test_to_cents_dollar_value():
    assert _to_cents(0.58) == 58

def test_to_cents_legacy_value():
    assert _to_cents(58) == 58

def test_extract_title_fields():
    title = "Tarik Skubal: 6+ strikeouts"
    assert _extract_player(title) == "Tarik Skubal"
    assert _extract_threshold(title) == "6+"
