"""Read-only cross-sport Kalshi / Polymarket US pricing API."""
from __future__ import annotations
import asyncio
import hmac
import os
import re
from datetime import datetime, timezone
from decimal import Decimal
from typing import Literal

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Query
from pydantic import BaseModel, Field
from engine import kalshi_levels, polymarket_us_levels, simulated_buy

KALSHI = "https://external-api.kalshi.com/trade-api/v2"
POLY_US = "https://gateway.polymarket.us"
ID = re.compile(r"^[a-zA-Z0-9_-]{2,180}$")

app = FastAPI(
    title="Prediction Markets Research v3 — Read-only Quotes",
    version="0.1.0",
    description="Direct public order-book depth snapshots. No trade execution.",
)

def require_read_key(x_pricing_key: str | None = Header(default=None)):
    expected = os.getenv("PRICING_READ_KEY")
    if expected and (not x_pricing_key or not hmac.compare_digest(expected, x_pricing_key)):
        raise HTTPException(status_code=401, detail="X-Pricing-Key is required")

def check_id(value: str):
    if not ID.fullmatch(value):
        raise HTTPException(status_code=400, detail="Invalid contract ticker/slug")
    return value

def fee_rate(venue: str):
    key = "KALSHI_FEE_RATE" if venue == "kalshi" else "POLYMARKET_US_FEE_RATE"
    default = "0.07" if venue == "kalshi" else "0.0695"
    try:
        rate = Decimal(os.getenv(key, default))
        if not rate.is_finite() or not 0 <= rate <= 1:
            raise ValueError("fee rate out of range")
        return rate
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Fee rate configuration invalid") from exc

async def get_json(client: httpx.AsyncClient, url: str, params=None):
    try:
        response = await client.get(url, params=params)
        response.raise_for_status()
        result = response.json()
        if not isinstance(result, dict):
            raise ValueError("response is not JSON object")
        return result
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=502, detail=f"Exchange HTTP {exc.response.status_code}") from exc
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(status_code=502, detail="Exchange unavailable or unrecognized JSON") from exc

async def quote(venue: str, identifier: str, side: str, contracts: int):
    check_id(identifier)
    if not 1 <= contracts <= 10000:
        raise HTTPException(status_code=422, detail="Whole share count out of range")
    if venue == "polymarket-us" and side != "YES":
        raise HTTPException(status_code=422, detail="Polymarket US NO/short not validated")
    read_started = datetime.now(timezone.utc).isoformat()
    async with httpx.AsyncClient(timeout=9.0, headers={"User-Agent":"PredictionMarketsResearchV3-ReadOnly/0.1"}) as client:
        if venue == "kalshi":
            meta_doc, book_doc = await asyncio.gather(
                get_json(client, f"{KALSHI}/markets/{identifier}"),
                get_json(client, f"{KALSHI}/markets/{identifier}/orderbook"),
            )
            meta = meta_doc.get("market")
            if not isinstance(meta, dict) or meta.get("ticker") != identifier:
                raise HTTPException(status_code=502, detail="Kalshi metadata mismatch")
            if meta.get("status") not in ("active", "open"):
                raise HTTPException(status_code=409, detail="Kalshi market not confirmed open")
            try:
                asks = kalshi_levels(book_doc, side)
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            rule_metadata = {k:meta.get(k) for k in ("ticker","title","subtitle","yes_sub_title","no_sub_title","rules_primary","rules_secondary","status","close_time","event_ticker")}
            exchange_time = None
        elif venue == "polymarket-us":
            meta_doc, book_doc = await asyncio.gather(
                get_json(client, f"{POLY_US}/v1/market/slug/{identifier}"),
                get_json(client, f"{POLY_US}/v1/markets/{identifier}/book"),
            )
            meta = meta_doc.get("market")
            if not isinstance(meta, dict) or meta.get("slug") != identifier:
                raise HTTPException(status_code=502, detail="Polymarket US metadata mismatch")
            try:
                asks = polymarket_us_levels(book_doc, side)
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            rule_metadata = {k:meta.get(k) for k in ("id","slug","title","question","description","rules","status","eventId")}
            exchange_time = book_doc.get("marketData",{}).get("transactTime")
        else:
            raise HTTPException(status_code=400, detail="Unknown venue")
    return {
        "venue": venue,
        "contract": identifier,
        "side": side,
        "retrieved_at_utc": read_started,
        "exchange_book_time": exchange_time,
        "source": "DIRECT_PUBLIC_PROVIDER_API",
        "market_rules": rule_metadata,
        "pricing": simulated_buy(asks, contracts, fee_rate(venue)),
        "quote_status": "LIVE_BOOK_SNAPSHOT_UNRESERVED",
        "limitations": [
            "Displayed liquidity may change before any purchase.",
            "Taker fee is an estimate, not authenticated order preview.",
            "Whole contracts only; exchange minimum size not verified.",
            "Cross-exchange settlement equivalence not inferred.",
            "This is not a trade, an account quote, or a recommendation.",
        ],
    }

class CompareRequest(BaseModel):
    kalshi_ticker: str = Field(min_length=2, max_length=180)
    polymarket_us_slug: str = Field(min_length=2, max_length=180)
    side: Literal["YES"] = "YES"
    contracts: int = Field(default=3, ge=1, le=10000)
    same_settlement_rules_checked: bool = False
    equivalence_evidence: str | None = None

@app.get("/health")
async def health():
    return {"status":"ok","mode":"read-only","trading_enabled":False,"version":"0.1.0"}

@app.get("/v1/quote/kalshi/{ticker}", dependencies=[Depends(require_read_key)])
async def kalshi_quote(ticker: str, side: Literal["YES","NO"]="YES", contracts: int=Query(3,ge=1,le=10000)):
    return await quote("kalshi",ticker,side,contracts)

@app.get("/v1/quote/polymarket-us/{slug}", dependencies=[Depends(require_read_key)])
async def polymarket_quote(slug: str, side: Literal["YES"]="YES", contracts: int=Query(3,ge=1,le=10000)):
    return await quote("polymarket-us",slug,side,contracts)

@app.post("/v1/compare", dependencies=[Depends(require_read_key)])
async def compare(body: CompareRequest):
    responses = await asyncio.gather(
        quote("kalshi",body.kalshi_ticker,body.side,body.contracts),
        quote("polymarket-us",body.polymarket_us_slug,body.side,body.contracts),
        return_exceptions=True,
    )
    rows=[]
    for venue, result in zip(("kalshi","polymarket-us"),responses):
        if isinstance(result,BaseException):
            rows.append({"venue":venue,"status":"UNAVAILABLE","error":str(getattr(result,"detail",type(result).__name__))})
        else:
            rows.append(result)
    attested = bool(body.same_settlement_rules_checked and (body.equivalence_evidence or "").strip())
    eligible = [r for r in rows if "pricing" in r and r["pricing"]["complete_depth"]]
    winner = min(eligible,key=lambda r: Decimal(r["pricing"]["estimated_total_usd"]))["venue"] if attested and eligible else None
    return {
        "comparison_status": "ANALYST_ATTESTED_NOT_AUTOMATICALLY_VERIFIED" if attested else "SETTLEMENT_EQUIVALENCE_UNVERIFIED",
        "equivalence_evidence": body.equivalence_evidence if attested else None,
        "winner_on_estimated_cost": winner,
        "quotes": rows,
        "warning": "Winner requires externally checked identical terms. Fees estimated; no guaranteed fills.",
    }

@app.get("/v1/discover/kalshi", dependencies=[Depends(require_read_key)])
async def discover_kalshi(series_ticker: str, cursor: str | None=None):
    check_id(series_ticker)
    if cursor and len(cursor)>600:
        raise HTTPException(status_code=400,detail="Cursor too long")
    params={"series_ticker":series_ticker,"status":"open","limit":100}
    if cursor:
        params["cursor"]=cursor
    async with httpx.AsyncClient(timeout=10) as client:
        data=await get_json(client,f"{KALSHI}/markets",params)
    return {"markets":data.get("markets",[]),"cursor":data.get("cursor"),"status":"DISCOVERY_ONLY"}

@app.get("/v1/discover/polymarket-us", dependencies=[Depends(require_read_key)])
async def discover_polymarket_us(limit: int=Query(50,ge=1,le=100),offset: int=Query(0,ge=0)):
    async with httpx.AsyncClient(timeout=10) as client:
        data=await get_json(client,f"{POLY_US}/v1/markets",{"limit":limit,"offset":offset})
    return {"markets":data.get("markets",[]),"status":"DISCOVERY_ONLY"}
