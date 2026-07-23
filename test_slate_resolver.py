from market_collector import extract_ticker_date_token, resolve_slate_token

def m(ticker): return {"ticker":ticker}

def test_extract():
    assert extract_ticker_date_token("KXMLBKS-26JUL231840LADPHI-7")=="26JUL23"

def test_shift(monkeypatch):
    monkeypatch.setattr("market_collector.kalshi_ticker_date",lambda _:"26JUL23")
    assert resolve_slate_token([m("KXMLBKS-26JUL241840LADPHI-7")])=="26JUL24"

def test_explicit_date_does_not_shift():
    assert resolve_slate_token([m("KXMLBKS-26JUL241840LADPHI-7")],"2026-07-23")=="26JUL23"
