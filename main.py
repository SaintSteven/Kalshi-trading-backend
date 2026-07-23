from datetime import datetime, timezone
import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from card_builder import build_paper_card
from config import ALLOWED_ORIGINS
from market_collector import collect_mlb_strikeout_markets, kalshi_ticker_date
from models import Market, MarketSummary, PaperCardRequest, PaperCardResponse

app=FastAPI(title="Kalshi Trading Engine",version="0.4.1",description="Paper-only engine with automatic slate resolution.")
app.add_middleware(CORSMiddleware,allow_origins=ALLOWED_ORIGINS,allow_credentials=False,allow_methods=["GET","POST","OPTIONS"],allow_headers=["*"])

@app.get("/")
async def root(): return {"service":"Kalshi Trading Engine","version":"0.4.1","mode":"paper-only","docs":"/docs"}

@app.get("/health")
async def health(): return {"status":"ok","version":"0.4.1","mode":"paper-only","time_utc":datetime.now(timezone.utc).isoformat()}

@app.get("/markets",response_model=list[Market])
async def markets(date:str|None=None,tradable_only:bool=True,min_ask:int=Query(2,ge=1,le=49),max_ask:int=Query(98,ge=51,le=100),max_combined_ask:int=Query(110,ge=100,le=200)):
    try:
        _,visible,_=await collect_mlb_strikeout_markets(date,tradable_only=tradable_only,min_ask=min_ask,max_ask=max_ask,max_combined_ask=max_combined_ask)
        return visible
    except ValueError as exc: raise HTTPException(status_code=400,detail="Date must use YYYY-MM-DD.") from exc
    except httpx.HTTPError as exc: raise HTTPException(status_code=502,detail=f"Kalshi market request failed: {exc}") from exc

@app.get("/market-summary",response_model=MarketSummary)
async def market_summary(date:str|None=None):
    requested=kalshi_ticker_date(date)
    selected,_,all_markets=await collect_mlb_strikeout_markets(date,tradable_only=False)
    tradable=sum(1 for m in all_markets if m.tradable)
    return MarketSummary(requested_slate=requested,selected_slate=selected,slate_shifted=selected!=requested,total_markets=len(all_markets),tradable_markets=tradable,hidden_markets=len(all_markets)-tradable,pitchers=len({m.player for m in all_markets}))

@app.post("/build-card",response_model=PaperCardResponse)
async def build_card(request:PaperCardRequest):
    requested=kalshi_ticker_date(request.date)
    selected,markets,_=await collect_mlb_strikeout_markets(request.date,tradable_only=True)
    recommendations,matched=build_paper_card(markets,request)
    shifted=selected!=requested
    message="Paper-only projection pricing is active. MODEL EDGE is not a real-money BUY recommendation."
    if shifted and request.date is None: message += f" No markets were found for {requested}; nearest available slate {selected} was selected automatically."
    return PaperCardResponse(status="paper_projection_complete",requested_slate=requested,selected_slate=selected,slate_shifted=shifted,markets_reviewed=len(markets),projections_matched=matched,recommendations=recommendations,message=message)
