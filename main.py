from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from backtest_engine import run_backtest
from backtest_models import BacktestRequest, BacktestResponse, HistoricalStart
from config import ALLOWED_ORIGINS
from edge_engine import analyze_edges
from edge_models import EdgeAnalysisRequest, EdgeAnalysisResponse
from historical_backtest_collector import collect_historical_starts
from historical_backtest_models import HistoricalBacktestRequest, HistoricalBacktestResponse
from lineup_experiment import run_lineup_experiment
from lineup_experiment_models import LineupExperimentRequest, LineupExperimentResponse
from market_collector import collect_mlb_strikeout_markets, kalshi_ticker_date, normalize_target_date
from model_lab import run_model_lab
from model_lab_models import ModelLabRequest, ModelLabResponse
from models import Market, MarketSummary, PaperCardRequest, PaperCardResponse
from pipeline_card_builder import build_card_from_pipeline
from research_pipeline import run_research_pipeline
from workload_experiment import run_workload_experiment
from workload_experiment_models import WorkloadExperimentRequest, WorkloadExperimentResponse


app = FastAPI(
    title="Kalshi Trading Engine",
    version="1.4.1",
    description=(
        "Paper-only MLB research engine with leakage-safe "
        "historical backtesting and model experimentation."
    ),
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


@app.get("/")
async def root():
    return {
        "service": "Kalshi Trading Engine",
        "version": "1.4.1",
        "mode": "paper-only",
        "docs": "/docs",
    }


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "version": "1.4.1",
        "mode": "paper-only",
        "pipeline": [
            "collect",
            "clean",
            "feature_engineering",
            "projection",
            "pricing",
            "quality_control",
            "backtesting",
            "historical_collection",
            "lineup_experiment",
            "workload_experiment",
            "model_lab",
            "edge_analysis",
        ],
        "time_utc": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/research-pipeline")
async def research_pipeline(date: str | None = None):
    pipeline = await run_research_pipeline(date)
    return {
        "date": date,
        "pitchers_collected": len(pipeline.raw_inputs),
        "pitchers_projected": len(pipeline.projections),
        "pitchers_excluded": len(pipeline.excluded),
        "features": pipeline.features,
        "excluded": pipeline.excluded,
    }


@app.get("/research-inputs")
async def research_inputs(date: str | None = None):
    return (await run_research_pipeline(date)).raw_inputs


@app.get("/markets", response_model=list[Market])
async def markets(
    date: str | None = None,
    tradable_only: bool = True,
    min_ask: int = Query(2, ge=1, le=49),
    max_ask: int = Query(98, ge=51, le=100),
    max_combined_ask: int = Query(110, ge=100, le=200),
):
    _, visible, _ = await collect_mlb_strikeout_markets(
        date,
        tradable_only=tradable_only,
        min_ask=min_ask,
        max_ask=max_ask,
        max_combined_ask=max_combined_ask,
    )
    return visible


@app.get("/market-summary", response_model=MarketSummary)
async def market_summary(date: str | None = None):
    requested = kalshi_ticker_date(date)
    selected, _, all_markets = await collect_mlb_strikeout_markets(
        date,
        tradable_only=False,
    )
    tradable = sum(1 for market in all_markets if market.tradable)
    return MarketSummary(
        requested_slate=requested,
        selected_slate=selected,
        slate_shifted=selected != requested,
        total_markets=len(all_markets),
        tradable_markets=tradable,
        hidden_markets=len(all_markets) - tradable,
        pitchers=len({market.player for market in all_markets}),
    )


@app.post("/build-card", response_model=PaperCardResponse)
async def build_card(request: PaperCardRequest):
    try:
        target_date = normalize_target_date(request.date)
        requested = kalshi_ticker_date(target_date)
        selected, markets, _ = await collect_mlb_strikeout_markets(
            target_date,
            tradable_only=True,
        )
        pipeline = await run_research_pipeline(target_date)
        recommendations, matched = build_card_from_pipeline(
            markets,
            request,
            pipeline,
        )
        return PaperCardResponse(
            status="research_pipeline_card_complete",
            requested_slate=requested,
            selected_slate=selected,
            slate_shifted=selected != requested,
            markets_reviewed=len(markets),
            automatic_pitchers_collected=len(pipeline.raw_inputs),
            projections_matched=matched,
            recommendations=recommendations,
            message="v1.4.1 paper-only research engine active.",
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/backtest", response_model=BacktestResponse)
async def backtest(request: BacktestRequest):
    return run_backtest(request)


@app.post("/historical-backtest", response_model=HistoricalBacktestResponse)
async def historical_backtest(request: HistoricalBacktestRequest):
    try:
        raw_records, collection_warnings = await collect_historical_starts(
            request.start_date,
            request.end_date,
            request.max_days,
        )
        starts = [HistoricalStart(**record) for record in raw_records]
        metrics = run_backtest(
            BacktestRequest(
                starts=starts,
                model_version=request.model_version,
            )
        )
        return HistoricalBacktestResponse(
            start_date=request.start_date,
            end_date=request.end_date,
            records_collected=len(starts),
            collection_warnings=collection_warnings,
            metrics=metrics,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/lineup-experiment", response_model=LineupExperimentResponse)
async def lineup_experiment(request: LineupExperimentRequest):
    try:
        result = await run_lineup_experiment(
            request.start_date,
            request.end_date,
            request.max_days,
        )
        return LineupExperimentResponse(
            start_date=request.start_date,
            end_date=request.end_date,
            records_collected=result["records_collected"],
            records_skipped=result["records_skipped"],
            comparison=result["comparison"],
            warnings=result["warnings"],
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc




@app.post("/workload-experiment", response_model=WorkloadExperimentResponse)
async def workload_experiment(request: WorkloadExperimentRequest):
    try:
        result = await run_workload_experiment(
            request.start_date,
            request.end_date,
            request.max_days,
        )
        return WorkloadExperimentResponse(
            start_date=request.start_date,
            end_date=request.end_date,
            records_collected=result["records_collected"],
            records_skipped=result["records_skipped"],
            comparison=result["comparison"],
            warnings=result["warnings"],
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/model-lab", response_model=ModelLabResponse)
async def model_lab(request: ModelLabRequest):
    try:
        result = await run_model_lab(
            request.start_date,
            request.end_date,
            request.max_days,
        )
        return ModelLabResponse(
            start_date=request.start_date,
            end_date=request.end_date,
            baseline_name=result["baseline_name"],
            records_collected=result["records_collected"],
            records_skipped=result["records_skipped"],
            experiments=result["experiments"],
            best_experiment_by_mae=result["best_experiment_by_mae"],
            best_experiment_by_rmse=result["best_experiment_by_rmse"],
            warnings=result["warnings"],
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/edge-analysis", response_model=EdgeAnalysisResponse)
async def edge_analysis(request: EdgeAnalysisRequest):
    return analyze_edges(request)
