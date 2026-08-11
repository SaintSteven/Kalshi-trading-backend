from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware

from backtest_engine import run_backtest
from backtest_models import BacktestRequest, BacktestResponse, HistoricalStart
from config import ALLOWED_ORIGINS
from edge_engine import analyze_edges
from edge_models import EdgeAnalysisRequest, EdgeAnalysisResponse, SettleSnapshotsRequest, SettleSnapshotsResponse
from historical_backtest_collector import collect_historical_starts
from historical_market_poc import historical_price_poc
from snapshot_settlement import settle_snapshots
from historical_backtest_models import HistoricalBacktestRequest, HistoricalBacktestResponse
from lineup_experiment import run_lineup_experiment
from lineup_experiment_models import LineupExperimentRequest, LineupExperimentResponse
from market_collector import (
    KalshiRateLimitError,
    collect_mlb_strikeout_markets,
    kalshi_ticker_date,
    normalize_target_date,
)
from model_lab import run_model_lab
from model_lab_models import ModelLabRequest, ModelLabResponse
from models import ExportCardRequest, Market, MarketSummary, PaperCardRequest, PaperCardResponse
from pipeline_card_builder import build_card_from_pipeline
from excel_export import build_card_workbook
from research_pipeline import run_research_pipeline
from workload_experiment import run_workload_experiment
from workload_experiment_models import WorkloadExperimentRequest, WorkloadExperimentResponse


app = FastAPI(
    title="Kalshi Trading Engine",
    version="2.6.3",
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
        "version": "2.6.3",
        "mode": "paper-only",
        "docs": "/docs",
    }


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "version": "2.6.3",
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
            "confidence_v2",
            "pregame_live_filter",
            "forward_snapshot_ledger",
            "settlement_and_trading_backtest",
            "matchup_metadata",
            "automatic_ledger_refresh_on_app_open",
            "four_plus_yes_research_guardrail",
            "unique_market_sample_counts",
            "historical_kalshi_price_poc",
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
        upcoming_markets=sum(1 for market in all_markets if market.game_status == "UPCOMING"),
        started_markets=sum(1 for market in all_markets if market.game_status in {"LIVE", "STARTED"}),
        hidden_markets=len(all_markets) - tradable,
        pitchers=len({market.player for market in all_markets}),
    )


@app.post("/build-card", response_model=PaperCardResponse)
async def build_card(request: PaperCardRequest):
    try:
        target_date = normalize_target_date(request.date)
        requested = kalshi_ticker_date(target_date)
        selected, tradable_markets, _ = await collect_mlb_strikeout_markets(
            target_date,
            tradable_only=True,
        )
        started_markets = [
            market for market in tradable_markets
            if market.game_status in {"LIVE", "STARTED"}
        ]
        markets = [
            market for market in tradable_markets
            if market.game_status not in {"LIVE", "STARTED"}
        ]
        live_games_filtered = len({
            (market.away_team, market.home_team, market.game_start_time)
            for market in started_markets
        })
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
            live_markets_filtered=len(started_markets),
            live_games_filtered=live_games_filtered,
            automatic_pitchers_collected=len(pipeline.raw_inputs),
            projections_matched=matched,
            recommendations=recommendations,
            message=(
                "v2.6.1 Strategy Comparison Analytics active; Portfolio Selector v2 logic remains unchanged: paper-budget allocation is ranked reliability-first using 65% QC confidence + 35% capped calibrated edge; unlimited-model sizing remains unchanged; unique-market analytics remain active; v2.5.1 calibration remains unchanged: raw-side selection and raw-edge gate prevent calibration-created bets; rare-tail probabilities are never increased; 4+ YES and 15+ raw adjusted-edge cohorts are research-only; started games are filtered, "
                "and every recommendation includes away team, home team, matchup, and ET start time."
            ),
        )
    except KalshiRateLimitError as exc:
        requested = kalshi_ticker_date(normalize_target_date(request.date))
        return PaperCardResponse(
            status="rate_limited",
            requested_slate=requested,
            selected_slate=requested,
            slate_shifted=False,
            markets_reviewed=0,
            live_markets_filtered=0,
            live_games_filtered=0,
            automatic_pitchers_collected=0,
            projections_matched=0,
            recommendations=[],
            message=(
                "Kalshi temporarily rate-limited market requests. "
                f"Please try again in about {exc.retry_after_seconds} seconds."
            ),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        return PaperCardResponse(
            status="temporary_error",
            requested_slate=kalshi_ticker_date(None),
            selected_slate=kalshi_ticker_date(None),
            slate_shifted=False,
            markets_reviewed=0,
            live_markets_filtered=0,
            live_games_filtered=0,
            automatic_pitchers_collected=0,
            projections_matched=0,
            recommendations=[],
            message="The card could not be built because a data source was temporarily unavailable. Please try again shortly.",
        )


@app.post("/export-card")
async def export_card(request: ExportCardRequest):
    workbook = build_card_workbook(request)
    safe_date = (request.card_date or request.selected_slate or "current").replace("/", "-")
    filename = f"{safe_date}_Kalshi_MLB_Daily_Card.xlsx"
    return StreamingResponse(
        workbook,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.post("/backtest", response_model=BacktestResponse)
async def backtest(request: BacktestRequest):
    return run_backtest(request)


@app.get("/historical-market-poc")
async def historical_market_price_poc(
    date: str = Query(..., description="Historical MLB slate date in YYYY-MM-DD format"),
    hours_before_first_pitch: float = Query(2.0, ge=0.25, le=24.0),
    max_markets: int = Query(12, ge=1, le=50),
):
    try:
        datetime.strptime(date, "%Y-%m-%d")
        return await historical_price_poc(
            date,
            hours_before_first_pitch=hours_before_first_pitch,
            max_markets=max_markets,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


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


@app.post("/settle-edge-snapshots", response_model=SettleSnapshotsResponse)
async def settle_edge_snapshots(request: SettleSnapshotsRequest):
    settled, pending, warnings = await settle_snapshots(request.records)
    return SettleSnapshotsResponse(
        settled_records=settled,
        pending_records=pending,
        warnings=warnings,
    )


@app.post("/edge-analysis", response_model=EdgeAnalysisResponse)
async def edge_analysis(request: EdgeAnalysisRequest):
    return analyze_edges(request)
