from datetime import datetime,timezone
import httpx
from fastapi import FastAPI,HTTPException,Query
from fastapi.middleware.cors import CORSMiddleware
from card_builder import build_paper_card
from config import ALLOWED_ORIGINS
from market_collector import collect_mlb_strikeout_markets,kalshi_ticker_date
from models import Market,MarketSummary,PaperCardRequest,PaperCardResponse
app=FastAPI(title='Kalshi Trading Engine',version='0.5.0',description='Paper-only workload-aware MLB strikeout engine.')
app.add_middleware(CORSMiddleware,allow_origins=ALLOWED_ORIGINS,allow_credentials=False,allow_methods=['GET','POST','OPTIONS'],allow_headers=['*'])
@app.get('/')
async def root():return {'service':'Kalshi Trading Engine','version':'0.5.0','mode':'paper-only','docs':'/docs'}
@app.get('/health')
async def health():return {'status':'ok','version':'0.5.0','mode':'paper-only','time_utc':datetime.now(timezone.utc).isoformat()}
@app.get('/markets',response_model=list[Market])
async def markets(date:str|None=None,tradable_only:bool=True,min_ask:int=Query(2,ge=1,le=49),max_ask:int=Query(98,ge=51,le=100),max_combined_ask:int=Query(110,ge=100,le=200)):
    try:
        _,v,_=await collect_mlb_strikeout_markets(date,tradable_only=tradable_only,min_ask=min_ask,max_ask=max_ask,max_combined_ask=max_combined_ask);return v
    except ValueError as e:raise HTTPException(status_code=400,detail='Date must use YYYY-MM-DD.') from e
    except httpx.HTTPError as e:raise HTTPException(status_code=502,detail=f'Kalshi request failed: {e}') from e
@app.get('/market-summary',response_model=MarketSummary)
async def summary(date:str|None=None):
    req=kalshi_ticker_date(date);sel,_,allm=await collect_mlb_strikeout_markets(date,tradable_only=False);t=sum(1 for m in allm if m.tradable)
    return MarketSummary(requested_slate=req,selected_slate=sel,slate_shifted=sel!=req,total_markets=len(allm),tradable_markets=t,hidden_markets=len(allm)-t,pitchers=len({m.player for m in allm}))
@app.post('/build-card',response_model=PaperCardResponse)
async def build(request:PaperCardRequest):
    req=kalshi_ticker_date(request.date);sel,markets,_=await collect_mlb_strikeout_markets(request.date,tradable_only=True);recs,matched=build_paper_card(markets,request)
    msg='Paper-only workload-aware pricing is active. MODEL EDGE is not a real-money BUY recommendation.'
    if sel!=req and request.date is None:msg+=f' Nearest available slate {sel} selected automatically.'
    return PaperCardResponse(status='paper_projection_complete',requested_slate=req,selected_slate=sel,slate_shifted=sel!=req,markets_reviewed=len(markets),projections_matched=matched,recommendations=recs,message=msg)
