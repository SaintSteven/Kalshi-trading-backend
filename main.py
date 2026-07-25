from datetime import datetime, timezone
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from backtest_engine import run_backtest
from backtest_models import BacktestRequest, BacktestResponse, HistoricalStart
from config import ALLOWED_ORIGINS
from historical_backtest_collector import collect_historical_starts
from historical_backtest_models import HistoricalBacktestRequest, HistoricalBacktestResponse
from market_collector import collect_mlb_strikeout_markets, kalshi_ticker_date
from models import Market, MarketSummary, PaperCardRequest, PaperCardResponse
from pipeline_card_builder import build_card_from_pipeline
from research_pipeline import run_research_pipeline

app = FastAPI(title="Kalshi Trading Engine", version="1.0.0", description="Paper-only MLB research engine with leakage-safe historical backtesting.")
app.add_middleware(CORSMiddleware, allow_origins=ALLOWED_ORIGINS, allow_credentials=False, allow_methods=["GET","POST","OPTIONS"], allow_headers=["*"])

@app.get("/")
async def root():
    return {"service":"Kalshi Trading Engine","version":"1.0.0","mode":"paper-only","docs":"/docs"}

@app.get("/health")
async def health():
    return {"status":"ok","version":"1.0.0","mode":"paper-only","pipeline":["collect","clean","feature_engineering","projection","pricing","quality_control","backtesting","historical_collection"],"time_utc":datetime.now(timezone.utc).isoformat()}

@app.get("/research-pipeline")
async def research_pipeline(date: str | None = None):
    p = await run_research_pipeline(date)
    return {"date":date,"pitchers_collected":len(p.raw_inputs),"pitchers_projected":len(p.projections),"pitchers_excluded":len(p.excluded),"features":p.features,"excluded":p.excluded}

@app.get("/research-inputs")
async def research_inputs(date: str | None = None):
    return (await run_research_pipeline(date)).raw_inputs

@app.get("/markets", response_model=list[Market])
async def markets(date: str | None = None, tradable_only: bool = True, min_ask: int = Query(2, ge=1, le=49), max_ask: int = Query(98, ge=51, le=100), max_combined_ask: int = Query(110, ge=100, le=200)):
    _, visible, _ = await collect_mlb_strikeout_markets(date, tradable_only=tradable_only, min_ask=min_ask, max_ask=max_ask, max_combined_ask=max_combined_ask)
    return visible

@app.get("/market-summary", response_model=MarketSummary)
async def market_summary(date: str | None = None):
    requested = kalshi_ticker_date(date)
    selected, _, all_markets = await collect_mlb_strikeout_markets(date, tradable_only=False)
    tradable = sum(1 for market in all_markets if market.tradable)
    return MarketSummary(requested_slate=requested, selected_slate=selected, slate_shifted=selected != requested, total_markets=len(all_markets), tradable_markets=tradable, hidden_markets=len(all_markets)-tradable, pitchers=len({market.player for market in all_markets}))

@app.post("/build-card", response_model=PaperCardResponse)
async def build_card(request: PaperCardRequest):
    requested = kalshi_ticker_date(request.date)
    selected, markets, _ = await collect_mlb_strikeout_markets(request.date, tradable_only=True)
    p = await run_research_pipeline(request.date)
    recommendations, matched = build_card_from_pipeline(markets, request, p)
    return PaperCardResponse(status="research_pipeline_card_complete", requested_slate=requested, selected_slate=selected, slate_shifted=selected != requested, markets_reviewed=len(markets), automatic_pitchers_collected=len(p.raw_inputs), projections_matched=matched, recommendations=recommendations, message="v1.0 historical backtesting engine active in paper-only mode.")

@app.post("/backtest", response_model=BacktestResponse)
async def backtest(request: BacktestRequest):
    return run_backtest(request)

@app.post("/historical-backtest", response_model=HistoricalBacktestResponse)
async def historical_backtest(request: HistoricalBacktestRequest):
    try:
        raw, warnings = await collect_historical_starts(request.start_date, request.end_date, request.max_days)
        starts = [HistoricalStart(**record) for record in raw]
        metrics = run_backtest(BacktestRequest(starts=starts, model_version=request.model_version))
        return HistoricalBacktestResponse(start_date=request.start_date, end_date=request.end_date, records_collected=len(starts), collection_warnings=warnings, metrics=metrics)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
