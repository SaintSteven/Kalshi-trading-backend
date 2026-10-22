"""Bridge contract tests; never hit the live exchange while running unit tests."""
import json

import pytest
from issue_quote_bridge import InvalidRequest, parse_request, run


def issue(quotes, user="SaintSteven", title="[pricing-v3] test request"):
    return {
        "user": {"login": user},
        "title": title,
        "body": json.dumps({"version": 1, "quotes": quotes}),
    }


def kalshi(budget="1.50", side="YES"):
    return {"venue": "kalshi", "contract": "KXNFLGAME-26OCT22NECHI-CHI",
            "side": side, "budget_usd": budget}


def poly(budget="1.00", side="YES"):
    return {"venue": "polymarket-us", "contract": "nfl-ne-chi-2026-10-22-chi",
            "side": side, "budget_usd": budget}


def test_parses_exact_contracts_and_small_stakes():
    rows = parse_request(issue([kalshi(), poly()]))
    assert len(rows) == 2
    assert rows[0]["budget_usd"] == "1.50"
    assert rows[1]["venue"] == "polymarket-us"


def test_kalshi_no_is_allowed_but_poly_no_is_blocked():
    assert parse_request(issue([kalshi(side="NO")]))[0]["side"] == "NO"
    with pytest.raises(InvalidRequest):
        parse_request(issue([poly(side="NO")]))


def test_owner_only():
    with pytest.raises(InvalidRequest):
        parse_request(issue([kalshi()], user="nonowner"))


def test_prefix_only():
    with pytest.raises(InvalidRequest):
        parse_request(issue([kalshi()], title="Other issue"))


@pytest.mark.parametrize("amount", ["0", "-1", "20.01", "nan", "inf", "oops", None])
def test_budget_validation(amount):
    with pytest.raises(InvalidRequest):
        parse_request(issue([kalshi(budget=amount)]))


def test_no_external_urls_or_injection():
    with pytest.raises(InvalidRequest):
        parse_request(issue([{**kalshi(), "contract": "https://example.com/" }]))
    with pytest.raises(InvalidRequest):
        parse_request(issue([{**kalshi(), "contract": "NECHI;rm -rf /" }]))


def test_request_count_limited():
    with pytest.raises(InvalidRequest):
        parse_request(issue([kalshi()] * 17))


def test_invalid_json_fails_closed():
    bad = issue([kalshi()])
    bad["body"] = "oops"
    with pytest.raises(InvalidRequest):
        parse_request(bad)


def test_run_outputs_no_trade_and_preserves_order(monkeypatch):
    def fake(leg):
        return {**leg, "status": "BOOK_SNAPSHOT", "pricing": {"fee_verified": False}}
    monkeypatch.setattr("issue_quote_bridge.get_quote", fake)
    response = run(issue([kalshi(), poly()]))
    assert response["trade_executed"] is False
    assert response["settlement_equivalence_verified"] is False
    assert [row["venue"] for row in response["results"]] == ["kalshi", "polymarket-us"]
    assert all(row["pricing"]["fee_verified"] is False for row in response["results"])


def test_run_never_contacts_exchange_for_invalid_request(monkeypatch):
    monkeypatch.setattr(
        "issue_quote_bridge.get_quote",
        lambda _: (_ for _ in ()).throw(AssertionError("should not call provider")),
    )
    assert run(issue([kalshi()], user="other"))["request_status"] == "INVALID_REQUEST"
