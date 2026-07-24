from datetime import datetime,timezone
from fastapi import FastAPI,Query
from fastapi.middleware.cors import CORSMiddleware
from config import ALLOWED_ORIGINS
from market_collector import collect_mlb_strikeout_markets,kalshi_ticker_date
from models import Market,MarketSummary,PaperCardRequest,PaperCardResponse
from pipeline_card_builder import build_card_from_pipeline
from research_pipeline import run_research_pipeline

app=FastAPI(title="Kalshi Trading Engine",version="0.8.0",description="Paper-only MLB research pipeline.")
app.add_middleware(CORSMiddleware,allow_origins=ALLOWED_ORIGINS,allow_credentials=False,allow_methods=["GET","POST","OPTIONS"],allow_headers=["*"])

@app.get("/")
async def root(): return {"service":"Kalshi Trading Engine","version":"0.8.0","mode":"paper-only","docs":"/docs"}

@app.get("/health")
async def health(): return {"status":"ok","version":"0.8.0","mode":"paper-only","pipeline":["collect","clean","feature_engineering","projection","pricing","quality_control"],"time_utc":datetime.now(timezone.utc).isoformat()}

@app.get("/research-pipeline")
async def research_pipeline(date:str|None=None):
    p=await run_research_pipeline(date)
    return {"date":date,"pitchers_collected":len(p.raw_inputs),"pitchers_projected":len(p.projections),"pitchers_excluded":len(p.excluded),"features":p.features,"excluded":p.excluded}

@app.get("/research-inputs")
async def research_inputs(date:str|None=None):
    return (await run_research_pipeline(date)).raw_inputs

@app.get("/markets",response_model=list[Market])
async def markets(date:str|None=None,tradable_only:bool=True,min_ask:int=Query(2,ge=1,le=49),max_ask:int=Query(98,ge=51,le=100),max_combined_ask:int=Query(110,ge=100,le=200)):
    _,visible,_=await collect_mlb_strikeout_markets(date,tradable_only=tradable_only,min_ask=min_ask,max_ask=max_ask,max_combined_ask=max_combined_ask)
    return visible

@app.get("/market-summary",response_model=MarketSummary)
async def market_summary(date:str|None=None):
    req=kalshi_ticker_date(date); sel,_,allm=await collect_mlb_strikeout_markets(date,tradable_only=False)
    tradable=sum(1 for m in allm if m.tradable)
    return MarketSummary(requested_slate=req,selected_slate=sel,slate_shifted=sel!=req,total_markets=len(allm),tradable_markets=tradable,hidden_markets=len(allm)-tradable,pitchers=len({m.player for m in allm}))

@app.post("/build-card",response_model=PaperCardResponse)
async def build_card(request:PaperCardRequest):
    req=kalshi_ticker_date(request.date); sel,markets,_=await collect_mlb_strikeout_markets(request.date,tradable_only=True)
    p=await run_research_pipeline(request.date); rec,matched=build_card_from_pipeline(markets,request,p)
    return PaperCardResponse(status="research_pipeline_card_complete",requested_slate=req,selected_slate=sel,slate_shifted=sel!=req,markets_reviewed=len(markets),automatic_pitchers_collected=len(p.raw_inputs),projections_matched=matched,recommendations=rec,message="v0.8 research pipeline active in paper-only mode.")
