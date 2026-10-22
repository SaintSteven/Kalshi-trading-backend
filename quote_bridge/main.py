"""Research v3.1 read-only quote bridge: live depth, fee estimates, strict matching."""
from __future__ import annotations
import asyncio
import os
import re
from datetime import datetime, timezone
from decimal import Decimal
from typing import Literal

import httpx
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field, model_validator
from .pricing import BadBook, dec, dollars, evaluate_price, fill_for_budget, fill_for_contracts, normalized_ask_levels

KALSHI = "https://external-api.kalshi.com/trade-api/v2"
POLY_US = "https://gateway.polymarket.us"
FEE_COEFFICIENT = {
    "kalshi": os.getenv("KALSHI_ESTIMATED_TAKER_FEE_COEFF", "0.07"),
    "polymarket_us": os.getenv("POLY_US_ESTIMATED_TAKER_FEE_COEFF", "0.0695"),
}
IDENTIFIER = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,180}$")
app = FastAPI(title="Prediction Markets Read-Only Quote Bridge", version="0.1.0",
              description="Fresh order books and estimated fees. No trading, no automatic contract matching.")

class QuoteRequest(BaseModel):
    venue: Literal["kalshi", "polymarket_us"]
    market_id: str = Field(description="Exact Kalshi ticker or Polymarket US slug, never a URL")
    side: Literal["YES", "NO"] = "YES"
    contracts: Decimal | None = Field(default=None, gt=0, le=10000)
    budget_usd: Decimal | None = Field(default=None, gt=0, le=10000)
    fair_probability: Decimal | None = Field(default=None, ge=0, le=1)
    max_all_in_price: Decimal | None = Field(default=None, ge=0, le=1)

    @model_validator(mode="after")
    def validate_market(self):
        if (self.contracts is None) == (self.budget_usd is None):
            raise ValueError("Provide exactly one of contracts or budget_usd")
        if not IDENTIFIER.fullmatch(self.market_id):
            raise ValueError("market_id must be an exact ticker/slug (not a URL)")
        return self

class CompareRequest(BaseModel):
    kalshi: QuoteRequest
    polymarket_us: QuoteRequest
    canonical_contract: str = Field(min_length=15, max_length=500)
    settlement_equivalence_verified: bool = False
    verified_equivalence_evidence: str | None = None

@app.get("/")
async def root():
    return {"name":"Research v3.1 Quote Bridge","read_only":True,"version":app.version,"docs":"/docs"}

@app.get("/health")
async def health():
    return {"status":"ok","version":app.version,"read_only":True,
            "live_exchange_verified":False,"time_utc":datetime.now(timezone.utc).isoformat()}

async def _json(client, url, **kwargs):
    try:
        response = await client.get(url, timeout=12, **kwargs)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("Expected JSON object")
        return payload
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(502, f"Exchange request failed ({type(exc).__name__}); no live quote verified") from exc

def _market_open(venue, metadata, book):
    if venue == "kalshi":
        status = str((metadata.get("market") or {}).get("status") or "unknown").lower()
        return status in {"active","open"}, status
    state = str((book.get("marketData") or {}).get("state") or "unknown").upper()
    return state == "MARKET_STATE_OPEN", state

async def quote_body(q: QuoteRequest, client):
    fetched = datetime.now(timezone.utc)
    if q.venue == "kalshi":
        base = f"{KALSHI}/markets/{q.market_id}"
        urls = (base,base+"/orderbook")
    else:
        urls = (f"{POLY_US}/v1/market/slug/{q.market_id}",
                f"{POLY_US}/v1/markets/{q.market_id}/book")
    meta, book = await asyncio.gather(*(_json(client,u) for u in urls))
    try:
        levels = normalized_ask_levels(q.venue, q.side, book)
        coeff = dec(FEE_COEFFICIENT[q.venue]) if FEE_COEFFICIENT[q.venue] else None
        fill = (fill_for_contracts(levels,q.contracts,coeff) if q.contracts is not None
                else fill_for_budget(levels,q.budget_usd,coeff))
        value = evaluate_price(fill,q.fair_probability,q.max_all_in_price)
    except BadBook as exc:
        raise HTTPException(422,str(exc)) from exc
    market = meta.get("market") if q.venue=="kalshi" else meta.get("market",meta)
    if not isinstance(market,dict):
        market = {}
    open_now,state=_market_open(q.venue,meta,book)
    data=book.get("marketData") if q.venue=="polymarket_us" else None
    timestamp=data.get("transactTime") if isinstance(data,dict) else None
    age=None
    if timestamp:
        try:
            stamp=datetime.fromisoformat(str(timestamp).replace("Z","+00:00"))
            age=max(0,int((fetched-stamp).total_seconds()))
        except (TypeError,ValueError):
            pass
    if not open_now:
        status="MARKET_NOT_OPEN"
    elif age is not None and age>60:
        status="STALE_EXCHANGE_BOOK"
    elif not fill.get("full_depth"):
        status="INSUFFICIENT_DEPTH"
    else:
        status="BOOK_VERIFIED_FEES_ESTIMATED"
    return {
        "venue":q.venue,"market_id":q.market_id,"side":q.side,
        "status":status,"market_state":state,"read_only":True,
        "book_fetched_at_utc":fetched.isoformat(),
        "exchange_book_timestamp":timestamp,"exchange_book_age_seconds":age,
        "market_title":market.get("title") or market.get("name") or market.get("question"),
        "market_subtitle":market.get("yes_sub_title") or market.get("description"),
        "market_rules":market.get("rules_primary") or market.get("rules") or market.get("settlementRules"),
        "market_metadata":{k:market.get(k) for k in ("ticker","slug","event_ticker","status","strike_type","floor_strike","cap_strike","expiration_time") if k in market},
        "fill":fill,"valuation":value,"source_endpoints":list(urls),
        "limitations":[
            "Snapshot depth changes before a fill; no order was placed.",
            "Taker fees are estimated, market/account-specific fees could differ.",
            "Independent fair probability and exact settlement equivalence require research.",
            "Account accessibility and holdings are not verified.",
        ],
    }

@app.post("/v1/quote")
async def quote(q: QuoteRequest):
    async with httpx.AsyncClient(headers={"User-Agent":"ResearchV3-QuoteBridge/0.1"}) as client:
        return await quote_body(q,client)

@app.get("/v1/quote")
async def quote_get(venue: Literal["kalshi","polymarket_us"], market_id: str,
                    side: Literal["YES","NO"]="YES",
                    contracts: Decimal | None=None,budget_usd: Decimal | None=None,
                    fair_probability: Decimal | None=None,max_all_in_price: Decimal | None=None):
    """Read-only GET for inspection. POST preferred; GET may be cached by intermediaries."""
    q=QuoteRequest(venue=venue,market_id=market_id,side=side,contracts=contracts,
                   budget_usd=budget_usd,fair_probability=fair_probability,max_all_in_price=max_all_in_price)
    async with httpx.AsyncClient(headers={"User-Agent":"ResearchV3-QuoteBridge/0.1"}) as client:
        return await quote_body(q,client)

@app.post("/v1/compare")
async def compare(req: CompareRequest):
    a,b=req.kalshi,req.polymarket_us
    if a.venue!="kalshi" or b.venue!="polymarket_us":
        raise HTTPException(422,"Provide Kalshi and Polymarket US contracts")
    if a.side!=b.side or a.contracts!=b.contracts or a.budget_usd!=b.budget_usd:
        raise HTTPException(422,"Side and intended count/budget must be the same")
    async with httpx.AsyncClient(headers={"User-Agent":"ResearchV3-QuoteBridge/0.1"}) as client:
        quotes=await asyncio.gather(quote_body(a,client),quote_body(b,client))
    equivalent=bool(req.settlement_equivalence_verified and
                    req.verified_equivalence_evidence and req.verified_equivalence_evidence.strip())
    comparable=equivalent and all(q["status"]=="BOOK_VERIFIED_FEES_ESTIMATED" for q in quotes)
    choice={"comparable":comparable,"preferred_venue":None,
            "reason":"Unverified settlement equivalence, stale book, or insufficient depth"}
    if comparable:
        cost_a=dec(quotes[0]["fill"]["estimated_total_usd"])
        cost_b=dec(quotes[1]["fill"]["estimated_total_usd"])
        choice={"comparable":True,
                "preferred_venue":a.venue if cost_a<cost_b else b.venue if cost_b<cost_a else "tie",
                "estimated_total_difference_usd":dollars(abs(cost_a-cost_b)),
                "reason":"Estimated fee-inclusive price, not an executable fill guarantee"}
    return {"canonical_contract":req.canonical_contract,
            "equivalence_verified_by_requester":equivalent,
            "equivalence_evidence":req.verified_equivalence_evidence if equivalent else None,
            "quotes":quotes,"comparison":choice}

@app.get("/v1/discover/{venue}")
async def discover(venue: Literal["kalshi","polymarket_us"],
                   query: str=Query(min_length=2,max_length=100),
                   series_ticker: str | None=None,event_ticker: str | None=None,
                   limit: int=Query(20,ge=1,le=50)):
    """First-page discovery, never claim full slate coverage or auto-match contracts."""
    async with httpx.AsyncClient(headers={"User-Agent":"ResearchV3-QuoteBridge/0.1"}) as client:
        if venue=="kalshi":
            params={"status":"open","limit":1000}
            for key,val in (("series_ticker",series_ticker),("event_ticker",event_ticker)):
                if val:
                    if not IDENTIFIER.fullmatch(val):
                        raise HTTPException(422,"Invalid series/event identifier")
                    params[key]=val
            body=await _json(client,f"{KALSHI}/markets",params=params)
        else:
            body=await _json(client,f"{POLY_US}/v1/markets")
    raw=body.get("markets") or []
    matches=[]
    for m in raw:
        if not isinstance(m,dict):
            continue
        hay=" ".join(str(m.get(k) or "") for k in ("title","name","description","question","slug","ticker","yes_sub_title"))
        if query.lower() in hay.lower():
            matches.append({k:m.get(k) for k in ("ticker","slug","title","name","description","yes_sub_title","event_ticker","status","id") if k in m})
        if len(matches)>=limit:
            break
    return {"venue":venue,"query":query,"matches":matches,
            "has_additional_pages":bool(body.get("cursor") or body.get("nextCursor")),
            "coverage":"first_page_only (NOT full-market search)",
            "auto_equivalence_verified":False,"discovery_only":True}
