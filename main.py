from datetime import datetime,timezone
import httpx
from fastapi import FastAPI,HTTPException,Query
from fastapi.middleware.cors import CORSMiddleware
from automatic_input_builder import build
from card_builder import build_paper_card
from config import ALLOWED_ORIGINS
from market_collector import collect_mlb_strikeout_markets,kalshi_ticker_date
from mlb_data_collector import collect
from models import Market,MarketSummary,PaperCardRequest,PaperCardResponse
app=FastAPI(title="Kalshi Trading Engine",version="0.6.0",description="Paper-only engine with automatic MLB data collection.")
app.add_middleware(CORSMiddleware,allow_origins=ALLOWED_ORIGINS,allow_credentials=False,allow_methods=["GET","POST","OPTIONS"],allow_headers=["*"])
@app.get("/")
async def root():return {"service":"Kalshi Trading Engine","version":"0.6.0","mode":"paper-only","docs":"/docs"}
@app.get("/health")
async def health():return {"status":"ok","version":"0.6.0","mode":"paper-only","time_utc":datetime.now(timezone.utc).isoformat()}
@app.get("/research-inputs")
async def research_inputs(date:str|None=None):
    try:return await collect(date)
    except Exception as e:raise HTTPException(status_code=502,detail=f"MLB data request failed: {e}")
@app.get("/markets",response_model=list[Market])
async def markets(date:str|None=None,tradable_only:bool=True,min_ask:int=Query(2,ge=1,le=49),max_ask:int=Query(98,ge=51,le=100),max_combined_ask:int=Query(110,ge=100,le=200)):
    _,v,_=await collect_mlb_strikeout_markets(date,tradable_only=tradable_only,min_ask=min_ask,max_ask=max_ask,max_combined_ask=max_combined_ask);return v
@app.get("/market-summary",response_model=MarketSummary)
async def market_summary(date:str|None=None):
    req=kalshi_ticker_date(date);sel,_,allm=await collect_mlb_strikeout_markets(date,tradable_only=False);t=sum(1 for m in allm if m.tradable)
    return MarketSummary(requested_slate=req,selected_slate=sel,slate_shifted=sel!=req,total_markets=len(allm),tradable_markets=t,hidden_markets=len(allm)-t,pitchers=len({m.player for m in allm}))
@app.post("/build-card",response_model=PaperCardResponse)
async def build_card(r:PaperCardRequest):
    req=kalshi_ticker_date(r.date);sel,mk,_=await collect_mlb_strikeout_markets(r.date,tradable_only=True);raw=[];inputs=list(r.pitchers)
    if r.use_automatic_data:
        raw=await collect(r.date);manual={p.player.lower() for p in inputs};inputs += [build(x) for x in raw if x["player"].lower() not in manual]
    rec,matched=build_paper_card(mk,r.model_copy(update={"pitchers":inputs}))
    return PaperCardResponse(status="automatic_paper_projection_complete",requested_slate=req,selected_slate=sel,slate_shifted=sel!=req,markets_reviewed=len(mk),automatic_pitchers_collected=len(raw),projections_matched=matched,recommendations=rec,message="Automatic MLB data collection is active in paper-only mode. Opponent adjustment uses team season K%, not confirmed lineups. Advanced Statcast and QC inputs remain neutral.")
