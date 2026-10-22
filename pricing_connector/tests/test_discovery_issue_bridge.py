"""Validate the owner-limited discovery bridge without network calls."""
import json
import pytest
from discovery_issue_bridge import InvalidDiscovery, parse_discovery, run_discovery


def make(discover, user="SaintSteven"):
    return {"user": {"login": user}, "title": "[discovery-v3] NFL",
            "body": json.dumps({"version": 1, "discover": discover})}


def test_series_discovery():
    q = parse_discovery(make([{"venue":"kalshi","series_ticker":"KXNFLGAME"}]))
    assert q[0]["series_ticker"]=="KXNFLGAME"


def test_poly_discovery_pagination():
    q = parse_discovery(make([{"venue":"polymarket-us","offset":100,"limit":50,
                               "keywords":["Bears","Patriots"]}]))
    assert q[0]["offset"]==100
    assert q[0]["keywords"]==["bears","patriots"]


def test_reject_unapproved_owner():
    with pytest.raises(InvalidDiscovery):
        parse_discovery(make([{"venue":"kalshi","series_ticker":"KXNFLGAME"}],user="intruder"))


@pytest.mark.parametrize("bad", ["https://example.com", "BAD/..", "KXNFLGAME;exit"])
def test_series_ticker_validated(bad):
    with pytest.raises(InvalidDiscovery):
        parse_discovery(make([{"venue":"kalshi","series_ticker":bad}]))


def test_reject_invalid_pagination():
    with pytest.raises(InvalidDiscovery):
        parse_discovery(make([{"venue":"polymarket-us","offset":-1}]))


def test_discovery_never_trades(monkeypatch):
    def fake(leg):
        return {"venue":leg["venue"],"status":"DISCOVERY_SNAPSHOT","markets":[]}
    monkeypatch.setattr("discovery_issue_bridge.fetch_one",fake)
    result=run_discovery(make([{"venue":"kalshi","series_ticker":"KXNFLGAME"}]))
    assert result["trade_executed"] is False
    assert result["discovery_only"] is True
