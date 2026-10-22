from decimal import Decimal as D
import asyncio
import httpx
import pytest
from fastapi.testclient import TestClient
from quote_bridge.pricing import BadBook, normalized_ask_levels, fill_for_contracts, fill_for_budget, evaluate_price
from quote_bridge.main import QuoteRequest, quote_body, app, _market_open

def test_kalshi_yes_asks_and_depth():
    p={"orderbook_fp":{"yes_dollars":[["0.55","10"]],"no_dollars":[["0.43","2"],["0.40","3"]]}}
    levels=normalized_ask_levels("kalshi","YES",p)
    assert levels==[(D("0.57"),D("2")),(D("0.60"),D("3"))]
    x=fill_for_contracts(levels,D("3"),D("0"))
    assert x["full_depth"] and D(x["raw_cost_usd"])==D("1.74")
    assert D(x["raw_vwap"])==D("0.58")

def test_kalshi_no_asks_from_yes_bids():
    p={"orderbook_fp":{"yes_dollars":[["0.32","5"]],"no_dollars":[]}}
    assert normalized_ask_levels("kalshi","NO",p)==[(D("0.68"),D("5"))]

def test_poly_us_offer_not_bid():
    p={"marketData":{"offers":[{"px":{"value":"0.49","currency":"USD"},"qty":"2"}],
                     "bids":[{"px":{"value":"0.10"},"qty":"999"}]}}
    assert normalized_ask_levels("polymarket_us","YES",p)==[(D("0.49"),D("2"))]
    with pytest.raises(BadBook):
        normalized_ask_levels("polymarket_us","NO",p)

def test_insufficient_depth_blocks_edge_claim():
    r=fill_for_contracts([(D("0.5"),D("1"))],D("2"),D("0.07"))
    assert not r["full_depth"]
    assert evaluate_price(r,D("0.7"),D("0.6"))["price_within_ceiling"] is None

def test_budget_respects_fees_and_whole_contracts():
    x=fill_for_budget([(D("0.5"),D("20"))],D("1.52"),D("0.07"))
    assert x["affordable_contracts"]==2
    assert x["fee_status"]=="estimated"

def test_best_ask_not_vwap():
    r=fill_for_contracts([(D("0.4"),D("1")),(D("0.7"),D("2"))],D("3"),D("0"))
    assert r["best_ask"]=="0.400000" and r["raw_vwap"]=="0.600000"
    assert evaluate_price(r,D("0.65"),D("0.5"))["price_within_ceiling"] is False

def test_health_distinguishes_server_from_exchange():
    with TestClient(app) as c:
        r=c.get("/health")
        assert r.status_code==200 and r.json()["live_exchange_verified"] is False

def test_url_injection_rejected():
    with TestClient(app) as c:
        r=c.post("/v1/quote",json={"venue":"kalshi","market_id":"https://bad.test","contracts":2})
        assert r.status_code==422

def test_closed_market_not_tradable():
    assert _market_open("kalshi",{"market":{"status":"settled"}},{})[0] is False
    assert _market_open("polymarket_us",{},{"marketData":{"state":"MARKET_STATE_CLOSED"}})[0] is False

def test_kalshi_api_mock():
    def mock(req):
        if req.url.path.endswith("/orderbook"):
            return httpx.Response(200,json={"orderbook_fp":{"yes_dollars":[],"no_dollars":[["0.45","4.00"]]}})
        return httpx.Response(200,json={"market":{"status":"active","title":"Exact market"}})
    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(mock)) as c:
            return await quote_body(QuoteRequest(venue="kalshi",market_id="KXTEST-26OCT22-Y",contracts="2"),c)
    r=asyncio.run(go())
    assert r["status"]=="BOOK_VERIFIED_FEES_ESTIMATED"
    assert r["fill"]["best_ask"]=="0.550000"
    assert r["valuation"]["research_qualifies"] is False

def test_polymarket_us_api_mock():
    def mock(req):
        if req.url.path.endswith("/book"):
            return httpx.Response(200,json={"marketData":{"state":"MARKET_STATE_OPEN",
                "offers":[{"px":{"value":"0.57","currency":"USD"},"qty":"10"}],"transactTime":None}})
        return httpx.Response(200,json={"market":{"slug":"sample","name":"Test"}})
    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(mock)) as c:
            return await quote_body(QuoteRequest(venue="polymarket_us",market_id="sample",contracts="2"),c)
    r=asyncio.run(go())
    assert r["status"]=="BOOK_VERIFIED_FEES_ESTIMATED"
    assert r["fill"]["best_ask"]=="0.570000"
