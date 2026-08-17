from datetime import datetime, timezone
import asyncio
import os
import uuid

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
from historical_job_store import HistoricalJobStore
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
    version="3.0.1",
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
        "version": "3.0.1",
        "mode": "paper-only",
        "docs": "/docs",
    }


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "version": "3.0.1",
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
