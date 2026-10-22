"""Synthetic wire fixtures; live provider smoke tests required after deploy."""
from decimal import Decimal as D
import pytest
from engine import kalshi_levels, polymarket_us_levels, simulated_buy

def test_kalshi_yes_recovers_asks_from_opposite_bids():
    fixture={"orderbook_fp":{"yes_dollars":[["0.20","2"]],"no_dollars":[["0.40","1"],["0.45","2"]]}}
    levels=kalshi_levels(fixture,"YES")
    assert levels==[(D(".55"),D(2)),(D(".60"),D(1))]
    fill=simulated_buy(levels,3,D(".07"))
    assert fill["complete_depth"]
    assert D(fill["gross_cost_usd"])==D("1.70")
    assert D(fill["estimated_fees_usd"])==D(".06")
    assert fill["firm_executable_quote"] is False

def test_kalshi_no_reverse_prices():
    assert kalshi_levels({"orderbook_fp":{"yes_dollars":[[".75","3"]],"no_dollars":[]}},"NO")==[(D(".25"),D(3))]

def test_polymarket_us_yes_uses_offers():
    fixture={"marketData":{"state":"MARKET_STATE_OPEN","offers":[
        {"px":{"value":"0.56","currency":"USD"},"qty":"2"},
        {"px":{"value":"0.55","currency":"USD"},"qty":"3"}]}}
    levels=polymarket_us_levels(fixture,"YES")
    assert levels[0]==(D(".55"),D(3))
    with pytest.raises(ValueError):
        polymarket_us_levels(fixture,"NO")

def test_market_closed_and_missing_bids_fail():
    with pytest.raises(ValueError):
        polymarket_us_levels({"marketData":{"state":"MARKET_STATE_CLOSED","offers":[]}},"YES")
    with pytest.raises(ValueError):
        kalshi_levels({"orderbook":{}},"YES")

def test_insufficient_liquidity_disclosed():
    fill=simulated_buy([(D(".50"),D(1))],3,D(".07"))
    assert not fill["complete_depth"]
    assert fill["status"]=="INSUFFICIENT_DEPTH"

def test_empty_book_no_fake_quote():
    fill=simulated_buy([],1,D(".07"))
    assert fill["top_ask_dollars"] is None
    assert fill["estimated_all_in_average_dollars"] is None

def test_fee_estimated_never_verified():
    fill=simulated_buy([(D(".50"),D(20))],3,D(".0695"))
    assert fill["estimated_fees_usd"]=="0.06"
    assert not fill["fee_verified"]

def test_bad_quantity_fail():
    with pytest.raises(ValueError):
        simulated_buy([],0,D(".07"))


def test_dollar_stake_budget_includes_fees():
    from engine import simulated_budget
    # Three 50-cent contracts cost > $1.50 after fees, so buy only two.
    result=simulated_budget([(D(".50"),D(20))],D("1.50"),D(".07"))
    assert result["affordable_whole_contracts"]==2
    assert result["quote"]["complete_depth"]
    assert D(result["quote"]["estimated_total_usd"])<=D("1.50")
    assert result["status"]=="STAKED_BOOK_SNAPSHOT_FEES_ESTIMATED"

def test_too_small_dollar_stake_refuses_order():
    from engine import simulated_budget
    result=simulated_budget([(D(".50"),D(20))],D(".50"),D(".07"))
    assert result["affordable_whole_contracts"]==0
    assert result["quote"] is None

def test_budget_depth_does_not_oversell():
    from engine import simulated_budget
    result=simulated_budget([(D(".40"),D(1))],D("10"),D(".07"))
    assert result["affordable_whole_contracts"]==1
