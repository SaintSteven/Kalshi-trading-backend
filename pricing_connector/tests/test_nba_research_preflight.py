from nba_research_preflight import nba_quote, nba_preflight


def request(rung=11, sport="NBA", signals=2):
    return {
        "sport": sport, "budget_usd": "1.00",
        "candidate": {"player": "Donovan Clingan", "stat": "rebounds",
                      "direction": "UNDER", "threshold": 10.5,
                      "supporting_independent_signals": signals},
        "market": {"venue": "kalshi", "contract": "KXNBAREB-26OCT27PORLAL-PORDCLINGAN23-12",
                   "player": "Donovan Clingan", "stat": "rebounds",
                   "yes_threshold": rung, "side": "NO", "rules_verified": True},
    }


def test_nba_rejects_wrong_rung_without_quote():
    called = []
    result = nba_quote(request(12), lambda x: called.append(x))
    assert not result["quote_allowed"]
    assert called == []


def test_nba_allows_exact_rung_to_read_only_quote():
    called = []
    def stub(leg):
        called.append(leg)
        return {"status": "BOOK_SNAPSHOT"}
    result = nba_quote(request(11), stub)
    assert result["status"] == "SNAPSHOT_NOT_FIRM"
    assert result["firm_executable_quote"] is False
    assert len(called) == 1


def test_other_sports_cannot_invoke_nba_quote():
    called = []
    result = nba_quote(request(11, sport="NFL"), lambda x: called.append(x))
    assert result["status"] == "OUT_OF_SCOPE"
    assert called == []


def test_single_signal_blocks_quote():
    assert nba_preflight(request(11, signals=1))["quote_allowed"] is False

def test_live_snapshot_fixture_stays_unreserved():
    sample = {"status": "BOOK_SNAPSHOT", "pricing": {
        "whole_contracts_priced": 3, "estimated_total_usd": "0.9800",
        "estimated_fees_usd": "0.05", "complete_depth": True,
        "firm_executable_quote": False, "fee_verified": False,
    }, "trade_executed": False}
    output = nba_quote(request(11), lambda leg: sample)
    assert output["status"] == "SNAPSHOT_NOT_FIRM"
    assert output["quote"]["pricing"]["whole_contracts_priced"] == 3
    assert output["firm_executable_quote"] is False
    assert output["trade_executed"] is False
