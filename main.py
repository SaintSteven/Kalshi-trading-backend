from datetime import datetime, timezone
import asyncio
import os
import uuid
import time
import traceback
import json
from pathlib import Path

import httpx

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
from historical_trading_models import HistoricalTradingBacktestRequest, HistoricalTradingBacktestResponse
from historical_trading_backtest import run_historical_trading_backtest
from historical_diagnostics import build_diagnostics, build_model_error_lab
from probability_engine_lab import build_probability_lab
from walk_forward_probability_lab import build_walk_forward_lab
from v3_challenger_lab import build_v3_challenger_lab
from v31_residual_edge_lab import build_v31_residual_edge_lab
from v32_robustness_lab import build_v32_robustness_lab
from v33_forward_validation import ForwardValidationStore, score_recommendations, settle_state, summarize_state
from historical_job_store import HistoricalJobStore
from lineup_experiment import run_lineup_experiment
from lineup_experiment_models import LineupExperimentRequest, LineupExperimentResponse
from market_collector import (
    KalshiRateLimitError,
    collect_mlb_strikeout_markets,
    inspect_mlb_strikeout_markets,
    kalshi_ticker_date,
    normalize_target_date,
)
from model_lab import run_model_lab
from model_lab_models import ModelLabRequest, ModelLabResponse
from models import ExportCardRequest, Market, MarketSummary, PaperCardRequest, PaperCardResponse
from pipeline_card_builder import build_card_from_pipeline
from excel_export import build_card_workbook
from research_pipeline import run_research_pipeline
from hybrid_mlb import CLVRecord, HybridCandidateRequest, evaluate_candidate, summarize_clv
from automatic_hybrid_card import build_automatic_game_card, settle_automatic_records
from hybrid_historical_backtest import HybridBacktestRequest, run_hybrid_historical_backtest
from workload_experiment import run_workload_experiment
from workload_experiment_models import WorkloadExperimentRequest, WorkloadExperimentResponse
from v38_research_lab import V38LineMovementRequest, run_v38_line_movement_backtest


app = FastAPI(
    title="Kalshi Trading Engine",
    version="3.8.0",
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
        "version": "3.8.0",
        "mode": "paper-only",
        "docs": "/docs",
    }


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "version": "3.7.0",
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
            "background_historical_backtest_jobs",
            "persistent_historical_job_checkpoints",
            "restart_resume_for_historical_jobs",
            "active_job_keepalive",
            "candlestick_cap_safe_batching",
            "v33_forward_validation",
            "hybrid_mlb_discovery_qc_clv",
            "automatic_free_source_hybrid_card",
            "timestamped_hybrid_snapshots",
            "automatic_hybrid_result_settlement",
            "historical_hybrid_proxy_backtest",
            "historical_hybrid_holdout_reporting",
            "hybrid_backtest_single_job_guard",
            "memory_safe_historical_odds_collection",
            "v38_line_movement_research_lab",
            "three_timestamp_executable_quote_sampling",
            "exact_contract_fee_backtesting",
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




# Background historical backtests use both an in-memory task registry and a
# SQLite checkpoint store. The store is consulted by status endpoints and on
# service startup so a partially completed month can resume from the last
# completed slate instead of starting over.
_HISTORICAL_JOBS: dict[str, dict] = {}
_HISTORICAL_JOB_TASKS: dict[str, asyncio.Task] = {}
_HISTORICAL_KEEPALIVE_TASKS: dict[str, asyncio.Task] = {}
_HISTORICAL_JOB_STORE = HistoricalJobStore()


def _job_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _persist_job(job: dict, *, external: bool = False):
    job["updated_at"] = _job_now()
    _HISTORICAL_JOB_STORE.upsert(job, sync_external=external)


def _prune_historical_jobs(max_jobs: int = 20):
    if len(_HISTORICAL_JOBS) <= max_jobs:
        return
    done = [j for j in _HISTORICAL_JOBS.values() if j.get("status") in {"completed", "failed", "cancelled"}]
    done.sort(key=lambda x: x.get("updated_at") or x.get("created_at") or "")
    for job in done[: max(0, len(_HISTORICAL_JOBS) - max_jobs)]:
        jid = job["job_id"]
        _HISTORICAL_JOBS.pop(jid, None)
        _HISTORICAL_JOB_TASKS.pop(jid, None)
        keep = _HISTORICAL_KEEPALIVE_TASKS.pop(jid, None)
        if keep and not keep.done():
            keep.cancel()


async def _historical_job_keepalive(job_id: str):
    """Generate light inbound traffic while a long free-tier job is active.

    Render can recycle an otherwise idle web service even while a detached task
    is doing work. When RENDER_EXTERNAL_URL is available, ping /health every
    eight minutes. This is not a persistence substitute; it simply reduces idle
    recycling while SQLite checkpoints protect completed slate days.
    """
    base = (os.getenv("RENDER_EXTERNAL_URL") or "").rstrip("/")
    if not base:
        return
    async with httpx.AsyncClient(timeout=20) as client:
        while True:
            await asyncio.sleep(8 * 60)
            job = _HISTORICAL_JOBS.get(job_id) or _HISTORICAL_JOB_STORE.get(job_id)
            if not job or job.get("status") not in {"queued", "running"}:
                return
            try:
                await client.get(f"{base}/health", params={"job": job_id})
            except Exception:
                pass


async def _run_historical_job(job_id: str, request: HistoricalTradingBacktestRequest, *, resumed: bool = False):
    job = _HISTORICAL_JOBS.get(job_id) or _HISTORICAL_JOB_STORE.get(job_id)
    if not job:
        return
    _HISTORICAL_JOBS[job_id] = job
    if not job.get("started_at"):
        job["started_at"] = _job_now()
    job.update(status="running", error=None)
    if resumed:
        p = job.get("progress") or {}
        p["message"] = f"Backend restarted; resuming from checkpoint after {p.get('days_processed', 0)} completed day(s)…"
        p["phase"] = "resuming"
        job["progress"] = p
    _persist_job(job, external=True)

    async def progress(payload: dict):
        current = _HISTORICAL_JOBS.get(job_id) or _HISTORICAL_JOB_STORE.get(job_id)
        if not current:
            return
        current["progress"] = payload
        _HISTORICAL_JOBS[job_id] = current
        _persist_job(current, external=False)

    async def checkpoint(payload: dict):
        current = _HISTORICAL_JOBS.get(job_id) or _HISTORICAL_JOB_STORE.get(job_id)
        if not current:
            return
        current["checkpoint"] = payload
        current["progress"] = {
            **(current.get("progress") or {}),
            "checkpoint_saved": True,
            "last_completed_date": payload.get("last_completed_date"),
        }
        _HISTORICAL_JOBS[job_id] = current
        _persist_job(current, external=True)

    keepalive = asyncio.create_task(_historical_job_keepalive(job_id))
    _HISTORICAL_KEEPALIVE_TASKS[job_id] = keepalive
    try:
        resume_state = job.get("checkpoint") or None
        result = await run_historical_trading_backtest(
            request,
            progress_callback=progress,
            checkpoint_callback=checkpoint,
            resume_state=resume_state,
        )
        job.update(
            status="completed",
            result=result,
            checkpoint=None,
            progress={**job.get("progress", {}), "percent": 100, "phase": "completed"},
            finished_at=_job_now(),
        )
        _persist_job(job, external=True)
    except asyncio.CancelledError:
        # Preserve checkpoint. On an ordinary process shutdown/restart, startup
        # recovery can continue from the last completed slate.
        job.update(status="running", error=None)
        p = job.get("progress") or {}
        p["message"] = "Job interrupted after a saved checkpoint; it can resume after backend startup."
        job["progress"] = p
        _persist_job(job, external=True)
        raise
    except Exception as exc:
        job.update(status="failed", error=str(exc), finished_at=_job_now())
        _persist_job(job, external=True)
    finally:
        _HISTORICAL_JOB_TASKS.pop(job_id, None)
        keep = _HISTORICAL_KEEPALIVE_TASKS.pop(job_id, None)
        if keep and not keep.done():
            keep.cancel()
        _prune_historical_jobs()


def _launch_historical_job(job: dict, *, resumed: bool = False):
    jid = job["job_id"]
    if jid in _HISTORICAL_JOB_TASKS and not _HISTORICAL_JOB_TASKS[jid].done():
        return
    try:
        request = HistoricalTradingBacktestRequest(**job["request"])
    except Exception as exc:
        job.update(status="failed", error=f"Could not restore saved request: {exc}", finished_at=_job_now())
        _persist_job(job, external=True)
        return
    _HISTORICAL_JOBS[jid] = job
    task = asyncio.create_task(_run_historical_job(jid, request, resumed=resumed))
    _HISTORICAL_JOB_TASKS[jid] = task


@app.on_event("startup")
async def resume_interrupted_historical_jobs():
    for job in _HISTORICAL_JOB_STORE.resumable():
        _launch_historical_job(job, resumed=True)


@app.post("/historical-trading-backtest", response_model=HistoricalTradingBacktestResponse)
async def historical_trading_backtest(request: HistoricalTradingBacktestRequest):
    try:
        result = await run_historical_trading_backtest(request)
        return HistoricalTradingBacktestResponse(**result)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/historical-trading-backtest/persistence")
async def historical_backtest_persistence_status():
    return _HISTORICAL_JOB_STORE.persistence_status()


@app.post("/historical-trading-backtest/jobs")
async def start_historical_trading_backtest_job(request: HistoricalTradingBacktestRequest):
    try:
        _HISTORICAL_JOB_STORE.require_external()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    try:
        start = datetime.strptime(request.start_date, "%Y-%m-%d").date()
        end = datetime.strptime(request.end_date, "%Y-%m-%d").date()
        if end < start:
            raise ValueError("end_date must be on or after start_date.")
        days = (end - start).days + 1
        if days > request.max_days:
            raise ValueError(f"Requested {days} days; maximum for this run is {request.max_days}.")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    job_id = uuid.uuid4().hex[:12]
    job = {
        "job_id": job_id,
        "status": "queued",
        "created_at": _job_now(),
        "updated_at": _job_now(),
        "request": request.model_dump(),
        "progress": {
            "phase": "queued",
            "message": "Background backtest queued; external GitHub checkpointing is enabled.",
            "days_total": days,
            "days_processed": 0,
            "percent": 0,
        },
        "result": None,
        "checkpoint": None,
        "error": None,
        "persistence_note": (
            "Progress is mirrored to the configured GitHub checkpoint branch after every completed slate. "
            "A fresh Render instance can rediscover the job and resume from the latest externally saved date."
        ),
    }
    _HISTORICAL_JOBS[job_id] = job
    _persist_job(job, external=True)
    _launch_historical_job(job)
    return job


@app.get("/historical-trading-backtest/jobs/{job_id}")
async def get_historical_trading_backtest_job(job_id: str):
    job = _HISTORICAL_JOBS.get(job_id) or _HISTORICAL_JOB_STORE.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Historical backtest job not found in the persistent job store.")
    _HISTORICAL_JOBS[job_id] = job
    if job.get("status") in {"queued", "running"} and job_id not in _HISTORICAL_JOB_TASKS:
        _launch_historical_job(job, resumed=True)
    return job


@app.get("/historical-trading-backtest/jobs")
async def list_historical_trading_backtest_jobs(limit: int = Query(default=10, ge=1, le=50)):
    return _HISTORICAL_JOB_STORE.list_recent(limit)


@app.get("/historical-trading-backtest/jobs/{job_id}/diagnostics")
async def historical_trading_backtest_diagnostics(job_id: str, strategy: str = Query(default="unlimited_model")):
    job = _HISTORICAL_JOBS.get(job_id) or _HISTORICAL_JOB_STORE.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Historical backtest job not found.")
    checkpoint = job.get("checkpoint")
    if not isinstance(checkpoint, dict):
        try:
            checkpoint = _HISTORICAL_JOB_STORE.recover_latest_checkpoint(job_id)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"Could not recover durable July checkpoint: {exc}") from exc
    if not isinstance(checkpoint, dict):
        raise HTTPException(status_code=404, detail="No per-bet checkpoint was found for this job.")
    key={"unlimited_model":"all_unlimited","edge_first_5_control":"all_edge_first","portfolio_selector_v2":"all_selector","v27_candidate_unlimited":"all_v27_candidate","v27_candidate_selector_v2":"all_v27_selector"}.get(strategy)
    if not key:
        raise HTTPException(status_code=400, detail="Unknown strategy.")
    return build_diagnostics(checkpoint.get(key) or [], strategy=strategy)

@app.get("/historical-trading-backtest/jobs/{job_id}/probability-lab")
async def historical_probability_engine_lab(job_id: str, minimum_edge_points: float = Query(default=5.0, ge=0, le=50)):
    job = _HISTORICAL_JOBS.get(job_id) or _HISTORICAL_JOB_STORE.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Historical backtest job not found.")
    checkpoint = job.get("checkpoint")
    if not isinstance(checkpoint, dict):
        try:
            checkpoint = _HISTORICAL_JOB_STORE.recover_latest_checkpoint(job_id)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"Could not recover durable diagnostic checkpoint: {exc}") from exc
    records = (checkpoint or {}).get("all_unlimited") or []
    if not records:
        raise HTTPException(status_code=404, detail="No frozen qualifier records were found for this job.")
    try:
        return build_probability_lab(records, minimum_edge_points=minimum_edge_points)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/historical-trading-backtest/jobs/{job_id}/walk-forward-lab")
async def historical_walk_forward_probability_lab(job_id: str, minimum_edge_points: float = Query(default=5.0, ge=0, le=50)):
    job = _HISTORICAL_JOBS.get(job_id) or _HISTORICAL_JOB_STORE.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Historical backtest job not found.")
    checkpoint = job.get("checkpoint")
    if not isinstance(checkpoint, dict):
        try:
            checkpoint = _HISTORICAL_JOB_STORE.recover_latest_checkpoint(job_id)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"Could not recover durable extended diagnostic checkpoint: {exc}") from exc
    records = (checkpoint or {}).get("all_unlimited") or []
    if not records:
        raise HTTPException(status_code=404, detail="No frozen qualifier records were found for this job.")
    try:
        return build_walk_forward_lab(records, minimum_edge_points=minimum_edge_points)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/historical-trading-backtest/jobs/{job_id}/model-error-lab")
async def historical_model_error_lab(job_id: str):
    job = _HISTORICAL_JOBS.get(job_id) or _HISTORICAL_JOB_STORE.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Historical backtest job not found.")
    checkpoint = job.get("checkpoint")
    if not isinstance(checkpoint, dict):
        try:
            checkpoint = _HISTORICAL_JOB_STORE.recover_latest_checkpoint(job_id)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"Could not recover durable diagnostic checkpoint: {exc}") from exc
    records = (checkpoint or {}).get("all_unlimited") or []
    if not records:
        raise HTTPException(status_code=404, detail="No frozen qualifier records were found for this job.")
    try:
        return build_model_error_lab(records)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _v3_full_universe_checkpoint_for_job(job_id: str):
    """Return a valid v3 full-universe checkpoint, preferring the requested job.

    Completed jobs can have their current checkpoint cleared, and the UI may still
    hold an older completed job id after the user presses only the *Prepare* button.
    Search recent durable jobs for the newest completed v3 capture before failing.
    """
    def recover(jid: str, job: dict | None):
        checkpoint = (job or {}).get("checkpoint") if isinstance(job, dict) else None
        if isinstance(checkpoint, dict) and checkpoint.get("all_evaluated"):
            return checkpoint
        try:
            checkpoint = _HISTORICAL_JOB_STORE.recover_latest_checkpoint(jid)
        except Exception:
            checkpoint = None
        if isinstance(checkpoint, dict) and checkpoint.get("all_evaluated"):
            return checkpoint
        return None

    requested = _HISTORICAL_JOBS.get(job_id) or _HISTORICAL_JOB_STORE.get(job_id)
    if requested:
        checkpoint = recover(job_id, requested)
        if checkpoint:
            return job_id, checkpoint

    try:
        recent = _HISTORICAL_JOB_STORE.list_recent(50)
    except Exception:
        recent = []
    for candidate in recent:
        if candidate.get("status") != "completed":
            continue
        request = candidate.get("request") or {}
        result = candidate.get("result") or {}
        model_label = str(request.get("model_version") or request.get("frozen_model_label") or "")
        if model_label != "3.0.0-full-universe-capture" and not result.get("v3_full_universe_records"):
            continue
        jid = candidate.get("job_id")
        if not jid:
            continue
        checkpoint = recover(jid, candidate)
        if checkpoint:
            return jid, checkpoint
    return None, None


@app.get("/historical-trading-backtest/jobs/{job_id}/v3-challenger-lab")
async def historical_v3_challenger_lab(job_id: str, minimum_edge_points: float = Query(default=5.0, ge=0, le=50)):
    job = _HISTORICAL_JOBS.get(job_id) or _HISTORICAL_JOB_STORE.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Historical backtest job not found.")
    try:
        source_job_id, checkpoint = _v3_full_universe_checkpoint_for_job(job_id)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Could not recover durable v3 full-universe checkpoint: {exc}") from exc
    records = (checkpoint or {}).get("all_evaluated") or []
    if not records:
        raise HTTPException(status_code=400, detail="No completed v3 full-universe capture exists yet. 'Prepare v3 Full Universe Capture' only changes the form; you must then tap Start Background Backtest and wait for it to finish before running the challenger.")
    try:
        payload = build_v3_challenger_lab(records, minimum_edge_points=minimum_edge_points)
        payload["source_job_id"] = source_job_id
        payload["source_checkpoint_records"] = len(records)
        return payload
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/historical-trading-backtest/jobs/{job_id}/v31-residual-edge-lab")
async def historical_v31_residual_edge_lab(job_id: str, minimum_edge_points: float = Query(default=5.0, ge=0, le=50)):
    job = _HISTORICAL_JOBS.get(job_id) or _HISTORICAL_JOB_STORE.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Historical backtest job not found.")
    source_job_id, checkpoint = _v3_full_universe_checkpoint_for_job(job_id)
    records = (checkpoint or {}).get("all_evaluated") or []
    if not records:
        raise HTTPException(status_code=400, detail="No completed v3 full-universe checkpoint is available.")
    try:
        payload = build_v31_residual_edge_lab(records, minimum_edge_points=minimum_edge_points)
        payload["source_job_id"] = source_job_id
        payload["source_checkpoint_records"] = len(records)
        return payload
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/historical-trading-backtest/jobs/{job_id}/v32-robustness-lab")
async def historical_v32_robustness_lab(job_id: str, minimum_edge_points: float = Query(default=5.0, ge=0, le=50)):
    job = _HISTORICAL_JOBS.get(job_id) or _HISTORICAL_JOB_STORE.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Historical backtest job not found.")
    source_job_id, checkpoint = _v3_full_universe_checkpoint_for_job(job_id)
    records = (checkpoint or {}).get("all_evaluated") or []
    if not records:
        raise HTTPException(status_code=400, detail="No completed v3 full-universe checkpoint is available.")
    try:
        payload = build_v32_robustness_lab(records, minimum_edge_points=minimum_edge_points)
        payload["source_job_id"] = source_job_id
        payload["source_checkpoint_records"] = len(records)
        return payload
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


_V33_FORWARD_STORE = ForwardValidationStore()




@app.get("/v33-forward-validation/connectivity")
async def v33_forward_validation_connectivity():
    """Tiny same-origin proxy probe used by the v3.3.5 mobile frontend."""
    return {
        "status": "ok",
        "version": "3.7.0",
        "transport": "network-direct",
        "service_worker_bypass_expected": True,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }



@app.get("/v33-forward-validation/market-inspector")
async def v33_forward_validation_market_inspector(date: str | None = Query(default=None)):
    """Read-only inspection of the current KXMLBKS slate before filtering."""
    try:
        payload = await inspect_mlb_strikeout_markets(date, force_refresh=True)
        payload["version"] = "3.7.0"
        payload["ledger_write"] = False
        payload["timestamp"] = datetime.now(timezone.utc).isoformat()
        return payload
    except KalshiRateLimitError as exc:
        raise HTTPException(status_code=429, detail={"message": "Kalshi market data is temporarily rate-limited.", "retry_after_seconds": exc.retry_after_seconds}, headers={"Retry-After": str(exc.retry_after_seconds)}) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

@app.post("/v33-forward-validation/transport-echo")
async def v33_forward_validation_transport_echo(request: PaperCardRequest, job_id: str | None = Query(default=None)):
    """Fast POST/body/proxy diagnostic. Never touches the forward ledger or live data providers."""
    return {
        "status": "ok",
        "version": "3.7.0",
        "diagnostic": "transport_echo",
        "method": "POST",
        "job_id": job_id,
        "request": {
            "date": request.date,
            "bankroll": request.bankroll,
            "already_committed_today": request.already_committed_today,
            "max_bet": request.max_bet,
            "minimum_edge_points": request.minimum_edge_points,
            "use_automatic_data": request.use_automatic_data,
            "pitcher_count": len(request.pitchers or []),
        },
        "ledger_write": False,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


_V339_TRACE_JOBS: dict[str, dict] = {}
_V339_TRACE_TASKS: dict[str, asyncio.Task] = {}
_V3310_TRACE_FILE = Path(os.getenv("V3310_TRACE_FILE", "/tmp/kalshi_v3310_forward_traces.json"))


def _v3310_persist_traces() -> None:
    try:
        _V3310_TRACE_FILE.write_text(json.dumps(_V339_TRACE_JOBS, default=str))
    except Exception as exc:
        print(f"[v3.7.0 trace persist warning] {type(exc).__name__}: {exc}", flush=True)


def _v3310_restore_traces() -> None:
    if _V339_TRACE_JOBS or not _V3310_TRACE_FILE.exists():
        return
    try:
        data = json.loads(_V3310_TRACE_FILE.read_text())
        if isinstance(data, dict):
            for item in data.values():
                if isinstance(item, dict) and item.get("status") in {"queued", "running"}:
                    item["status"] = "interrupted_after_restart"
                    item["message"] = "Trace state was recovered after a backend process restart; last recorded substage is preserved below."
            _V339_TRACE_JOBS.update(data)
    except Exception as exc:
        print(f"[v3.7.0 trace restore warning] {type(exc).__name__}: {exc}", flush=True)


def _v339_trace_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _v339_trace_mark(trace: dict, stage: str, status: str, started_perf: float, detail: dict | None = None):
    elapsed_ms = int((time.perf_counter() - started_perf) * 1000)
    row = {
        "stage": stage,
        "status": status,
        "elapsed_ms": elapsed_ms,
        "at": _v339_trace_now(),
    }
    if detail:
        row["detail"] = detail
    trace.setdefault("events", []).append(row)
    trace["current_stage"] = stage
    trace["updated_at"] = row["at"]
    trace["elapsed_ms"] = elapsed_ms
    # Also emit to Render logs so a worker crash still leaves the last completed stage visible there.
    _v3310_persist_traces()
    print(f"[v3.7.0 trace {trace.get('trace_id')}] {stage} {status} +{elapsed_ms}ms {detail or ''}", flush=True)


async def _run_v339_forward_trace(trace_id: str, request: PaperCardRequest, job_id: str | None):
    """Run the real forward pipeline in the background, with NO ledger write.

    The HTTP request that starts this trace returns immediately, so a 25-30 second
    Render/Vercel request timeout cannot hide which backend stage is slow or failing.
    """
    trace = _V339_TRACE_JOBS[trace_id]
    started = time.perf_counter()
    trace.update(status="running", started_at=_v339_trace_now(), current_stage="start")
    _v339_trace_mark(trace, "start", "begin", started, {"ledger_write": False})
    try:
        _v339_trace_mark(trace, "resolve_history", "begin", started)
        resolved_job_id = job_id
        if not resolved_job_id:
            recent = _HISTORICAL_JOB_STORE.list_recent(50)
            resolved_job_id = next((j.get("job_id") for j in recent if j.get("status") == "completed" and (j.get("result") or {}).get("v3_full_universe_records")), None)
        if not resolved_job_id:
            raise RuntimeError("No completed v3 full-universe checkpoint is available.")
        source_job_id, checkpoint = _v3_full_universe_checkpoint_for_job(resolved_job_id)
        history = (checkpoint or {}).get("all_evaluated") or []
        if not history:
            raise RuntimeError("Completed checkpoint contains no v3 full-universe history.")
        trace["source_job_id"] = source_job_id
        _v339_trace_mark(trace, "resolve_history", "complete", started, {"history_records": len(history)})

        _v339_trace_mark(trace, "normalize_date", "begin", started)
        from zoneinfo import ZoneInfo
        target_date = normalize_target_date(request.date)
        game_date = target_date or datetime.now(ZoneInfo("America/New_York")).date().isoformat()
        trace["game_date"] = game_date
        _v339_trace_mark(trace, "normalize_date", "complete", started, {"game_date": game_date})

        _v339_trace_mark(trace, "kalshi_markets", "begin", started, {"scope": "series_ticker=KXMLBKS"})

        def _kalshi_trace(substage: str, detail: dict | None = None):
            # Persist each network/parsing milestone. If Render restarts, the last
            # completed substage can be restored from /tmp on the next status call.
            _v339_trace_mark(trace, f"kalshi_markets.{substage}", "complete", started, detail or {})

        selected, tradable_markets, all_markets = await collect_mlb_strikeout_markets(
            game_date, tradable_only=True, force_refresh=True, trace_callback=_kalshi_trace
        )
        markets = [m for m in tradable_markets if m.game_status not in {"LIVE", "STARTED"}]
        market_pitchers = sorted({m.player.strip() for m in markets if getattr(m, "player", None)})
        trace["selected_slate"] = selected
        trace["counts"] = {
            "kalshi_all": len(all_markets),
            "kalshi_tradable": len(tradable_markets),
            "kalshi_upcoming": len(markets),
            "kalshi_pitchers": len(market_pitchers),
        }
        _v339_trace_mark(trace, "kalshi_markets", "complete", started, trace["counts"].copy())

        _v339_trace_mark(trace, "research_pipeline", "begin", started)
        pipeline = await run_research_pipeline(game_date)
        projected_pitchers = sorted((pipeline.projections or {}).keys())
        trace["counts"].update({
            "mlb_raw_probables": len(getattr(pipeline, "raw_inputs", []) or []),
            "mlb_projections": len(projected_pitchers),
            "mlb_excluded": len(getattr(pipeline, "excluded", []) or []),
        })
        _v339_trace_mark(trace, "research_pipeline", "complete", started, {
            "mlb_raw_probables": trace["counts"]["mlb_raw_probables"],
            "mlb_projections": trace["counts"]["mlb_projections"],
            "mlb_excluded": trace["counts"]["mlb_excluded"],
        })

        _v339_trace_mark(trace, "build_recommendations", "begin", started)
        capture_request = request.model_copy(update={"minimum_edge_points": 0.0, "already_committed_today": 0.0})
        recommendations, matched = build_card_from_pipeline(markets, capture_request, pipeline)
        trace["counts"].update({"matched_pitchers": matched, "recommendations": len(recommendations)})
        _v339_trace_mark(trace, "build_recommendations", "complete", started, {
            "matched_pitchers": matched, "recommendations": len(recommendations)
        })

        _v339_trace_mark(trace, "score_v31", "begin", started)
        scored = score_recommendations(history, recommendations, game_date, source_job_id=source_job_id)
        trace["counts"].update({
            "scored": len(scored.get("scored", [])),
            "qualifiers_5pt": len(scored.get("qualifiers", [])),
            "primary_10pt": len(scored.get("primary", [])),
        })
        _v339_trace_mark(trace, "score_v31", "complete", started, {
            "scored": trace["counts"]["scored"],
            "qualifiers_5pt": trace["counts"]["qualifiers_5pt"],
            "primary_10pt": trace["counts"]["primary_10pt"],
        })

        _v339_trace_mark(trace, "summarize_ledger", "begin", started)
        summary = summarize_state(_V33_FORWARD_STORE.load())
        trace["ledger_snapshot"] = {
            "all_captured": (summary.get("all_5pt") or {}).get("captured", 0),
            "primary_settled": (summary.get("primary_10pt") or {}).get("settled", 0),
        }
        _v339_trace_mark(trace, "summarize_ledger", "complete", started, trace["ledger_snapshot"].copy())

        trace.update(
            status="complete",
            current_stage="complete",
            finished_at=_v339_trace_now(),
            elapsed_ms=int((time.perf_counter() - started) * 1000),
            ledger_write=False,
            message="Full forward pipeline trace completed without writing the validation ledger.",
        )
        _v339_trace_mark(trace, "complete", "complete", started, {"ledger_write": False})
    except asyncio.CancelledError:
        trace.update(status="cancelled", finished_at=_v339_trace_now(), error="Trace task cancelled.")
        _v339_trace_mark(trace, trace.get("current_stage") or "unknown", "cancelled", started)
        raise
    except BaseException as exc:
        trace.update(
            status="error",
            finished_at=_v339_trace_now(),
            error_type=type(exc).__name__,
            error=str(exc),
            traceback="".join(traceback.format_exception(type(exc), exc, exc.__traceback__))[-6000:],
            ledger_write=False,
        )
        _v339_trace_mark(trace, trace.get("current_stage") or "unknown", "error", started, {
            "error_type": type(exc).__name__, "error": str(exc)[:1000]
        })


@app.post("/v33-forward-validation/trace/start")
async def v339_forward_trace_start(request: PaperCardRequest, job_id: str | None = Query(default=None)):
    _v3310_restore_traces()
    running = next((t for t in _V339_TRACE_JOBS.values() if t.get("status") in {"queued", "running"}), None)
    if running:
        return {
            "status": "already_running",
            "version": "3.7.0",
            "trace_id": running.get("trace_id"),
            "ledger_write": False,
            "message": "A diagnostic trace is already running. Use Check Trace Status instead of starting another.",
        }
    trace_id = uuid.uuid4().hex[:12]
    trace = {
        "trace_id": trace_id,
        "version": "3.7.0",
        "status": "queued",
        "created_at": _v339_trace_now(),
        "updated_at": _v339_trace_now(),
        "current_stage": "queued",
        "events": [],
        "counts": {},
        "ledger_write": False,
    }
    _V339_TRACE_JOBS[trace_id] = trace
    _v3310_persist_traces()
    task = asyncio.create_task(_run_v339_forward_trace(trace_id, request, job_id))
    _V339_TRACE_TASKS[trace_id] = task
    return {
        "status": "started",
        "version": "3.7.0",
        "trace_id": trace_id,
        "ledger_write": False,
        "message": "Background forward pipeline trace started. Poll trace status for stage timing/results.",
    }


@app.get("/v33-forward-validation/trace/{trace_id}")
async def v339_forward_trace_status(trace_id: str):
    _v3310_restore_traces()
    trace = _V339_TRACE_JOBS.get(trace_id)
    if not trace:
        raise HTTPException(status_code=404, detail="Forward trace not found after in-memory and persisted-state lookup. Start one new trace.")
    return trace


@app.post("/v33-forward-validation/capture")
async def v33_forward_validation_capture(request: PaperCardRequest, job_id: str | None = Query(default=None), dry_run: bool = Query(default=False)):

    try:
        if not job_id:
            recent = _HISTORICAL_JOB_STORE.list_recent(50)
            job_id = next((j.get("job_id") for j in recent if j.get("status") == "completed" and (j.get("result") or {}).get("v3_full_universe_records")), None)
        if not job_id:
            raise HTTPException(status_code=400, detail="No completed v3 full-universe checkpoint is available. Tap Check Status in Lab first.")
        source_job_id, checkpoint = _v3_full_universe_checkpoint_for_job(job_id)
        history = (checkpoint or {}).get("all_evaluated") or []
        if not history:
            raise HTTPException(status_code=400, detail="No completed v3 full-universe checkpoint is available.")

        from zoneinfo import ZoneInfo
        target_date = normalize_target_date(request.date)
        game_date = target_date or datetime.now(ZoneInfo("America/New_York")).date().isoformat()
        # v3.3.3: force a fresh Kalshi read for prospective capture and expose
        # every pre-scorer count so a zero-trade day can be distinguished from
        # a pipeline/data-availability problem.
        selected, tradable_markets, all_markets = await collect_mlb_strikeout_markets(
            game_date, tradable_only=True, force_refresh=True
        )
        markets = [m for m in tradable_markets if m.game_status not in {"LIVE", "STARTED"}]
        market_pitchers = sorted({m.player.strip() for m in markets if getattr(m, "player", None)})
        pipeline = await run_research_pipeline(game_date)
        projected_pitchers = sorted((pipeline.projections or {}).keys())
        capture_request = request.model_copy(update={"minimum_edge_points": 0.0, "already_committed_today": 0.0})
        recommendations, matched = build_card_from_pipeline(markets, capture_request, pipeline)
        scored = score_recommendations(history, recommendations, game_date, source_job_id=source_job_id)

        matched_names = sorted({
            m.player.strip() for m in markets
            if m.player.strip().lower() in (pipeline.projections or {})
        })
        unmatched_names = [x for x in market_pitchers if x not in matched_names]
        diagnostics = {
            "requested_date": game_date,
            "selected_slate": selected,
            "kalshi_markets_all": len(all_markets),
            "kalshi_markets_tradable": len(tradable_markets),
            "kalshi_markets_upcoming": len(markets),
            "kalshi_unique_pitchers": len(market_pitchers),
            "mlb_raw_probable_pitchers": len(getattr(pipeline, "raw_inputs", []) or []),
            "mlb_projection_pitchers": len(projected_pitchers),
            "mlb_excluded_pitchers": len(getattr(pipeline, "excluded", []) or []),
            "matched_pitchers": matched,
            "recommendations_built": len(recommendations),
            "scored_pitchers": len(scored.get("scored", [])),
            "market_pitcher_sample": market_pitchers[:8],
            "matched_pitcher_sample": matched_names[:8],
            "unmatched_pitcher_sample": unmatched_names[:8],
            "excluded_sample": (getattr(pipeline, "excluded", []) or [])[:5],
        }
        diagnostics["summary_text"] = (
            f"Kalshi all/tradable/upcoming {diagnostics['kalshi_markets_all']}/{diagnostics['kalshi_markets_tradable']}/{diagnostics['kalshi_markets_upcoming']} · "
            f"Kalshi pitchers {diagnostics['kalshi_unique_pitchers']} · MLB probable/projections {diagnostics['mlb_raw_probable_pitchers']}/{diagnostics['mlb_projection_pitchers']} · "
            f"matched {diagnostics['matched_pitchers']} · recommendations {diagnostics['recommendations_built']} · scored {diagnostics['scored_pitchers']}"
        )

        # Do not write a fake zero-capture marker when the upstream slate was
        # not actually scorable.  Return a diagnostic state instead.
        if not markets:
            summary = summarize_state(_V33_FORWARD_STORE.load())
            return {
                **summary, "summary": summary, "status": "waiting_for_kalshi_markets",
                "message": "No upcoming tradable Kalshi MLB strikeout markets are available for this slate yet.",
                "selected_slate": selected, "game_date": game_date, "diagnostics": diagnostics,
                "markets_reviewed": 0, "projections_matched": 0, "scored_pitchers": 0,
                "qualifiers_5pt": 0, "primary_10pt_count": 0, "added": 0, "today": [],
                "source_job_id": source_job_id,
            }
        if not (pipeline.projections or {}):
            summary = summarize_state(_V33_FORWARD_STORE.load())
            return {
                **summary, "summary": summary, "status": "waiting_for_mlb_pitcher_data",
                "message": "Kalshi markets are live, but MLB probable-starter/projection data is not ready for this slate yet.",
                "selected_slate": selected, "game_date": game_date, "diagnostics": diagnostics,
                "markets_reviewed": len(markets), "projections_matched": 0, "scored_pitchers": 0,
                "qualifiers_5pt": 0, "primary_10pt_count": 0, "added": 0, "today": [],
                "source_job_id": source_job_id,
            }
        if matched == 0:
            summary = summarize_state(_V33_FORWARD_STORE.load())
            return {
                **summary, "summary": summary, "status": "pitcher_match_failure",
                "message": "Kalshi markets and MLB projections both exist, but no pitcher names matched. Review the diagnostic samples below.",
                "selected_slate": selected, "game_date": game_date, "diagnostics": diagnostics,
                "markets_reviewed": len(markets), "projections_matched": 0, "scored_pitchers": 0,
                "qualifiers_5pt": 0, "primary_10pt_count": 0, "added": 0, "today": [],
                "source_job_id": source_job_id,
            }
        if not recommendations:
            summary = summarize_state(_V33_FORWARD_STORE.load())
            return {
                **summary, "summary": summary, "status": "recommendation_build_failure",
                "message": "Pitchers matched, but the live card builder produced zero recommendations. No ledger entry was created.",
                "selected_slate": selected, "game_date": game_date, "diagnostics": diagnostics,
                "markets_reviewed": len(markets), "projections_matched": matched, "scored_pitchers": 0,
                "qualifiers_5pt": 0, "primary_10pt_count": 0, "added": 0, "today": [],
                "source_job_id": source_job_id,
            }
        if not scored.get("scored"):
            summary = summarize_state(_V33_FORWARD_STORE.load())
            return {
                **summary, "summary": summary, "status": "scoring_failure",
                "message": "Recommendations were built, but frozen v3.1 scored zero pitchers. No ledger entry was created.",
                "selected_slate": selected, "game_date": game_date, "diagnostics": diagnostics,
                "markets_reviewed": len(markets), "projections_matched": matched, "scored_pitchers": 0,
                "qualifiers_5pt": 0, "primary_10pt_count": 0, "added": 0, "today": [],
                "source_job_id": source_job_id,
            }

        if dry_run:
            summary = summarize_state(_V33_FORWARD_STORE.load())
            return {
                **summary,
                "summary": summary,
                "status": "dry_run_complete",
                "message": "Full forward pipeline completed in dry-run mode. No ledger write occurred.",
                "selected_slate": selected,
                "game_date": game_date,
                "markets_reviewed": len(markets),
                "projections_matched": matched,
                "scored_pitchers": len(scored.get("scored", [])),
                "qualifiers_5pt": len(scored.get("qualifiers", [])),
                "primary_10pt_count": len(scored.get("primary", [])),
                "added": 0,
                "today": scored.get("qualifiers", []),
                "diagnostics": diagnostics,
                "source_job_id": source_job_id,
                "ledger_write": False,
            }

        state = _V33_FORWARD_STORE.append_capture(scored, game_date)
        summary = summarize_state(state)
        # Response contract v3.3.3: keep the nested summary used by the current UI,
        # while also mirroring summary fields at the top level for compatibility.
        # This prevents a successful durable capture from looking like a failure
        # if a cached frontend/backend pair disagrees about the response shape.
        response = {
            **summary,
            "status": "captured", "selected_slate": selected, "game_date": game_date,
            "markets_reviewed": len(markets), "projections_matched": matched, "scored_pitchers": len(scored.get("scored", [])),
            "qualifiers_5pt": len(scored.get("qualifiers", [])), "primary_10pt_count": len(scored.get("primary", [])),
            "added": state.get("added", 0), "today": scored.get("qualifiers", []), "summary": summary,
            "diagnostics": diagnostics, "source_job_id": source_job_id,
        }
        return response
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"v3.3 forward capture failed: {exc}") from exc


@app.post("/v33-forward-validation/settle")
async def v33_forward_validation_settle():
    try:
        state = _V33_FORWARD_STORE.load()
        state, warnings = await settle_state(state)
        _V33_FORWARD_STORE.save(state)
        return {"status": "settled", "warnings": warnings, "summary": summarize_state(state)}
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"v3.3 forward settlement failed: {exc}") from exc


@app.get("/v33-forward-validation/status")
async def v33_forward_validation_status():
    try:
        return summarize_state(_V33_FORWARD_STORE.load())
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"v3.3 forward validation status failed: {exc}") from exc


@app.delete("/historical-trading-backtest/jobs/{job_id}")
async def cancel_historical_trading_backtest_job(job_id: str):
    job = _HISTORICAL_JOBS.get(job_id) or _HISTORICAL_JOB_STORE.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Historical backtest job not found.")
    task = _HISTORICAL_JOB_TASKS.get(job_id)
    if task and not task.done():
        task.cancel()
    job.update(status="cancelled", error="Job cancelled by user.", finished_at=_job_now())
    _HISTORICAL_JOBS[job_id] = job
    _persist_job(job, external=True)
    return job


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


_HYBRID_BACKTEST_JOBS: dict[str, dict] = {}
_HYBRID_BACKTEST_TASKS: dict[str, asyncio.Task] = {}
_V38_BACKTEST_JOBS: dict[str, dict] = {}
_V38_BACKTEST_TASKS: dict[str, asyncio.Task] = {}


async def _run_v38_backtest_job(job_id: str, request: V38LineMovementRequest):
    job = _V38_BACKTEST_JOBS[job_id]
    job.update(status="running", started_at=_job_now())

    async def progress(payload: dict):
        job["progress"] = payload
        job["updated_at"] = _job_now()

    try:
        job["result"] = await run_v38_line_movement_backtest(request, progress_callback=progress)
        job.update(status="completed", finished_at=_job_now(), updated_at=_job_now())
    except Exception as exc:
        job.update(status="failed", error=str(exc), finished_at=_job_now(), updated_at=_job_now())
    finally:
        _V38_BACKTEST_TASKS.pop(job_id, None)


async def _run_hybrid_backtest_job(job_id: str, request: HybridBacktestRequest):
    job = _HYBRID_BACKTEST_JOBS[job_id]
    job.update(status="running", started_at=_job_now())

    async def progress(payload: dict):
        job["progress"] = payload
        job["updated_at"] = _job_now()

    try:
        job["result"] = await run_hybrid_historical_backtest(request, progress_callback=progress)
        job.update(status="completed", finished_at=_job_now(), updated_at=_job_now())
    except Exception as exc:
        job.update(status="failed", error=str(exc), finished_at=_job_now(), updated_at=_job_now())
    finally:
        _HYBRID_BACKTEST_TASKS.pop(job_id, None)
        completed = sorted(
            (row for row in _HYBRID_BACKTEST_JOBS.values() if row.get("status") in {"completed", "failed"}),
            key=lambda row: row.get("updated_at") or "",
        )
        for old in completed[:-10]:
            _HYBRID_BACKTEST_JOBS.pop(old["job_id"], None)


@app.post("/hybrid-mlb/historical-backtest/jobs")
async def start_hybrid_historical_backtest_job(request: HybridBacktestRequest):
    try:
        start = datetime.strptime(request.start_date, "%Y-%m-%d").date()
        end = datetime.strptime(request.end_date, "%Y-%m-%d").date()
        if end < start:
            raise ValueError("end_date must be on or after start_date.")
        if (end - start).days + 1 > request.max_days:
            raise ValueError("Requested date range exceeds Maximum Days.")
        if end >= datetime.now(timezone.utc).astimezone().date():
            raise ValueError("The backtest end date must be before today.")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    requested = request.model_dump()
    active = [job for job in _HYBRID_BACKTEST_JOBS.values() if job.get("status") in {"queued", "running"}]
    duplicate = next((job for job in active if job.get("request") == requested), None)
    if duplicate:
        duplicate["message"] = "An identical historical hybrid backtest is already running; returning the existing job."
        return duplicate
    if active:
        raise HTTPException(status_code=409, detail="Another historical hybrid backtest is already running. Check its progress before starting a new one.")
    job_id = uuid.uuid4().hex[:12]
    job = {
        "job_id": job_id, "status": "queued", "created_at": _job_now(), "updated_at": _job_now(),
        "request": request.model_dump(), "progress": {"phase": "queued", "percent": 0, "message": "Historical hybrid backtest queued on the backend."},
        "result": None, "error": None,
        "message": "The backend job continues if the phone leaves or closes the page; reopen Backtest and tap Check Progress.",
    }
    _HYBRID_BACKTEST_JOBS[job_id] = job
    task = asyncio.create_task(_run_hybrid_backtest_job(job_id, request))
    _HYBRID_BACKTEST_TASKS[job_id] = task
    return job


@app.get("/hybrid-mlb/historical-backtest/jobs/{job_id}")
async def get_hybrid_historical_backtest_job(job_id: str):
    job = _HYBRID_BACKTEST_JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Hybrid backtest job was not found; the backend may have restarted.")
    return job


@app.post("/v38/line-movement/backtest/jobs")
async def start_v38_line_movement_job(request: V38LineMovementRequest):
    active = next((job for job in _V38_BACKTEST_JOBS.values() if job.get("status") in {"queued", "running"}), None)
    requested = request.model_dump()
    if active and active.get("request") == requested:
        active["message"] = "An identical v3.8 research job is already running."
        return active
    if active:
        raise HTTPException(status_code=409, detail="Another v3.8 research job is already running. Check its progress first.")
    job_id = uuid.uuid4().hex[:12]
    job = {
        "job_id": job_id,
        "status": "queued",
        "created_at": _job_now(),
        "updated_at": _job_now(),
        "request": requested,
        "progress": {"phase": "queued", "percent": 0, "message": "v3.8 line-movement research queued."},
        "result": None,
        "error": None,
        "message": "The backend job continues if the phone closes the page.",
    }
    _V38_BACKTEST_JOBS[job_id] = job
    task = asyncio.create_task(_run_v38_backtest_job(job_id, request))
    _V38_BACKTEST_TASKS[job_id] = task
    return job


@app.get("/v38/line-movement/backtest/jobs/{job_id}")
async def get_v38_line_movement_job(job_id: str):
    job = _V38_BACKTEST_JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="v3.8 research job was not found; the backend may have restarted.")
    return job


@app.get("/hybrid-mlb/auto-card")
async def hybrid_mlb_auto_card(
    date: str | None = None,
    minimum_edge_points: float = Query(default=5.0, ge=0, le=30),
):
    try:
        return await build_automatic_game_card(date, minimum_edge_points)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/hybrid-mlb/settle-auto")
async def hybrid_mlb_settle_auto(records: list[dict]):
    return await settle_automatic_records(records)


@app.get("/hybrid-mlb/schema")
async def hybrid_mlb_schema():
    return {
        "version": "3.8.0",
        "mode": "paper-only",
        "game_pipeline": ["market_discovery", "sportsbook_no_vig_baseline", "model_veto", "qc", "cost_adjusted_kalshi_price", "decision", "clv"],
        "strikeout_pipeline": ["bottom_up_projection", "external_validation", "qc", "kalshi_price", "decision", "clv"],
        "discovery_grades": {
            "A": "Four independent supporting sources, three signal types, and sharp-market confirmation.",
            "B": "At least two independent supporting sources across two signal types.",
            "C": "Single-source or weakly diversified game idea; watch-only under v3.7.",
        },
        "game_pricing_policy": "MARKET_FIRST_V37",
        "legacy_benchmark": "LEGACY_V36",
        "default_estimated_cost_cents": 2.0,
        "v38_research_lab": {
            "status": "challenger",
            "hypothesis": "T-4h to T-90m declines partially mean-revert by T-10m.",
            "execution": "T-90m ask entry; T-10m bid exit; contract-level taker fees on both orders.",
        },
    }


@app.post("/hybrid-mlb/evaluate")
async def hybrid_mlb_evaluate(request: HybridCandidateRequest):
    return evaluate_candidate(request)


@app.post("/hybrid-mlb/clv-summary")
async def hybrid_mlb_clv_summary(records: list[CLVRecord]):
    return summarize_clv(records)


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
