from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import datetime, timedelta
import inspect
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import httpx

from edge_engine import analyze_edges
from edge_models import EdgeAnalysisRequest, HistoricalMarketRecord
from historical_backtest_collector import collect_historical_starts
from historical_market_poc import (
    HIST_BASE,
    MLB_STRIKEOUT_PREFIX,
    UTC,
    _batch_recent_candles,
    _collect_series_markets,
    _finalize_row,
    _game_start_from_ticker,
    _get_cutoff,
    _parse_iso,
    _single_candles,
    _target_route,
)
from historical_trading_models import HistoricalTradingBacktestRequest
from models import Market, PaperCardRequest
from pipeline_card_builder import build_card_from_pipeline

ET = ZoneInfo("America/New_York")


def _date_token(target_date: str) -> str:
    return datetime.strptime(target_date, "%Y-%m-%d").strftime("%y%b%d").upper()


def _norm(value: str | None) -> str:
    return "".join(c.lower() for c in (value or "") if c.isalnum())


def _chunked(values: list[str], size: int = 90):
    for i in range(0, len(values), size):
        yield values[i:i + size]


def _candle_safe_chunks(values: list[str], specs: dict[str, tuple], max_candles: int = 9000, max_markets: int = 90):
    """Pack recent market tickers into batches that stay below Kalshi's 10k candle cap.

    The batch endpoint applies one common start/end window across every ticker. A large
    MLB slate can therefore explode into tens of thousands of requested 1-minute candles
    even when each individual market only needs a six-hour lookback. We sort by target
    time and grow a batch only while the estimated common-window candle count stays
    under a conservative 9,000-candle ceiling.
    """
    ordered = sorted(values, key=lambda t: specs[t][3])
    chunk: list[str] = []
    for ticker in ordered:
        candidate = chunk + [ticker]
        starts = [specs[t][2] for t in candidate]
        ends = [specs[t][3] for t in candidate]
        minutes = max(1, (max(ends) - min(starts) + 59) // 60)
        estimate = minutes * len(candidate)
        if chunk and (len(candidate) > max_markets or estimate > max_candles):
            yield chunk
            chunk = [ticker]
        else:
            chunk = candidate
    if chunk:
        yield chunk


async def _emit_progress(callback, payload: dict):
    if callback is None:
        return
    result = callback(payload)
    if inspect.isawaitable(result):
        await result


def _won(actual_k: int, threshold: str, side: str) -> bool:
    n = int(str(threshold).rstrip("+"))
    yes = actual_k >= n
    return yes if side == "YES" else not yes


def _record_from_rec(rec, game_date: str, actual_k: int, stake: float, model_version: str) -> HistoricalMarketRecord | None:
    if rec.side not in {"YES", "NO"} or rec.market_price_cents is None or stake <= 0:
        return None
    model_probability = rec.calibrated_fair_probability if rec.calibrated_fair_probability is not None else rec.fair_probability
    if model_probability is None:
        return None
    return HistoricalMarketRecord(
        player=rec.player,
        game_date=game_date,
        threshold=rec.threshold,
        side=rec.side,
        model_probability=model_probability,
        raw_model_probability=rec.fair_probability,
        entry_price_cents=rec.market_price_cents,
        actual_strikeouts=actual_k,
        model_version=model_version,
        confidence=float(rec.confidence.get("overall", 0)) if rec.confidence else None,
        confidence_tier=rec.confidence.get("tier") if rec.confidence and rec.confidence.get("tier") in {"LOW", "MEDIUM", "HIGH"} else None,
        adjusted_edge_points=rec.adjusted_edge_points,
        stake=round(float(stake), 2),
        model_stake=rec.unlimited_bankroll_stake,
        model_units=rec.model_units,
        paper_included=stake > 0,
        research_only=rec.research_only,
        research_units=rec.research_units,
        research_stake=rec.research_stake,
        research_reason=rec.research_reason,
        ticker=rec.ticker,
        matchup=rec.matchup,
        selector_score=rec.selector_score,
        selector_rank=rec.selector_rank,
        selector_method=rec.selector_method,
        portfolio_selected=rec.portfolio_selected,
    )


def _strategy_summary(records: list[HistoricalMarketRecord]) -> dict:
    result = analyze_edges(EdgeAnalysisRequest(records=records, minimum_edge_points=0, minimum_confidence=0, fee_rate=0))
    return result.model_dump()


async def _quotes_for_date(client: httpx.AsyncClient, target_date: str, hours_before: float, lookback_hours: float, warnings: list[str]) -> tuple[list[dict], int]:
    """Return all reconstructable KXMLBKS quotes for a slate date.

    Uses the same no-lookahead point-in-time rule proven by v2.6.4, but processes
    all date-matched ladders. Recent-tier candles are batched in <=90 ticker chunks;
    archive-tier candles remain serialized with backoff.
    """
    cutoff = None
    try:
        cutoff_payload = await _get_cutoff(client)
        cutoff = _parse_iso(cutoff_payload.get("market_settled_ts"))
    except Exception as exc:
        warnings.append(f"{target_date}: cutoff lookup failed; recent tier tried first: {exc}")

    route = _target_route(target_date, cutoff)
    tiers = [route] if route in {"recent", "historical"} else ["recent", "historical"]
    if cutoff is None and "historical" not in tiers:
        tiers.append("historical")

    token = _date_token(target_date)
    tagged: list[tuple[dict, bool]] = []
    seen: set[str] = set()
    for tier in tiers:
        historical = tier == "historical"
        try:
            markets = await _collect_series_markets(client, historical=historical)
            for m in markets:
                ticker = str(m.get("ticker") or "")
                if ticker.startswith(f"{MLB_STRIKEOUT_PREFIX}-{token}") and ticker not in seen:
                    tagged.append((m, historical)); seen.add(ticker)
        except Exception as exc:
            warnings.append(f"{target_date}: {tier} market listing failed: {exc}")

    if not tagged and cutoff is not None and route != "both":
        fallback = "historical" if route == "recent" else "recent"
        try:
            markets = await _collect_series_markets(client, historical=fallback == "historical")
            for m in markets:
                ticker = str(m.get("ticker") or "")
                if ticker.startswith(f"{MLB_STRIKEOUT_PREFIX}-{token}") and ticker not in seen:
                    tagged.append((m, fallback == "historical")); seen.add(ticker)
        except Exception as exc:
            warnings.append(f"{target_date}: fallback {fallback} listing failed: {exc}")

    specs: dict[str, tuple[dict, bool, int, int]] = {}
    for market, historical in tagged:
        ticker = str(market.get("ticker") or "")
        start = _game_start_from_ticker(ticker)
        if not start:
            continue
        target = start - timedelta(hours=hours_before)
        specs[ticker] = (
            market,
            historical,
            int((target - timedelta(hours=lookback_hours)).astimezone(UTC).timestamp()),
            int(target.astimezone(UTC).timestamp()),
        )

    candle_map: dict[str, list[dict]] = {}
    methods: dict[str, str] = {}
    recent = [t for t, (_, h, _, _) in specs.items() if not h]
    archive = [t for t, (_, h, _, _) in specs.items() if h]

    if recent:
        await asyncio.sleep(0.35)
        for chunk in _candle_safe_chunks(recent, specs):
            batch_start = min(specs[t][2] for t in chunk)
            batch_end = max(specs[t][3] for t in chunk)
            try:
                batch = await _batch_recent_candles(client, chunk, start_ts=batch_start, end_ts=batch_end)
                for t in chunk:
                    candle_map[t] = batch.get(t, [])
                    methods[t] = "batch_recent"
            except Exception as exc:
                warnings.append(f"{target_date}: batch candles failed for {len(chunk)} markets; fallback used: {exc}")
                await asyncio.sleep(1.0)
                for t in chunk:
                    _, historical, s, e = specs[t]
                    try:
                        candle_map[t] = await _single_candles(client, t, historical=historical, start_ts=s, end_ts=e)
                        methods[t] = "single_recent_fallback"
                    except Exception as sub:
                        warnings.append(f"{target_date} {t}: candle retrieval failed: {sub}")
                        candle_map[t] = []
                    await asyncio.sleep(0.25)
            await asyncio.sleep(0.35)

    if archive:
        await asyncio.sleep(0.8)
        for t in archive:
            _, _, s, e = specs[t]
            try:
                candle_map[t] = await _single_candles(client, t, historical=True, start_ts=s, end_ts=e)
                methods[t] = "single_historical_throttled"
            except Exception as exc:
                warnings.append(f"{target_date} {t}: historical candle retrieval failed: {exc}")
                candle_map[t] = []
            await asyncio.sleep(0.30)

    rows = []
    for market, historical in tagged:
        ticker = str(market.get("ticker") or "")
        row = _finalize_row(
            market=market,
            historical=historical,
            candles=candle_map.get(ticker, []),
            hours_before_first_pitch=hours_before,
            retrieval_method=methods.get(ticker, "not_requested"),
        )
        row["title"] = market.get("title")
        rows.append(row)
    return [r for r in rows if r.get("usable_entry_quote")], len(tagged)


def _market_from_quote(q: dict) -> Market | None:
    if not q.get("player") or not q.get("threshold"):
        return None
    game_start = None
    try:
        if q.get("game_start_et"):
            game_start = datetime.fromisoformat(q["game_start_et"])
    except Exception:
        pass
    yes_ask = q.get("yes_ask_cents")
    no_ask = q.get("no_ask_cents")
    if yes_ask is None or no_ask is None:
        return None
    return Market(
        ticker=q["ticker"],
        title=q.get("title") or f"{q['player']}: {q['threshold']} strikeouts",
        player=q["player"],
        threshold=q["threshold"],
        game_start_time=game_start,
        game_start_display=game_start.astimezone(ET).strftime("%b %-d · %-I:%M %p ET") if game_start else None,
        game_status="UPCOMING",
        yes_ask_cents=int(yes_ask),
        no_ask_cents=int(no_ask),
        tradable=True,
    )


async def run_historical_trading_backtest(
    request: HistoricalTradingBacktestRequest,
    progress_callback=None,
    checkpoint_callback=None,
    resume_state: dict | None = None,
) -> dict:
    start = datetime.strptime(request.start_date, "%Y-%m-%d").date()
    end = datetime.strptime(request.end_date, "%Y-%m-%d").date()
    if end < start:
        raise ValueError("end_date must be on or after start_date.")
    days = (end - start).days + 1
    if days > request.max_days:
        raise ValueError(f"Requested {days} days; maximum for this run is {request.max_days}.")

    resume_state = resume_state or {}
    warnings: list[str] = list(resume_state.get("warnings") or [])
    resumed_days = len(resume_state.get("daily_results") or [])
    await _emit_progress(progress_callback, {
        "phase": "collecting_features",
        "message": (
            f"Rebuilding leakage-safe historical inputs before resuming after {resumed_days} completed day(s)…"
            if resumed_days else
            "Collecting leakage-safe historical pitcher and opponent inputs…"
        ),
        "days_total": days,
        "days_processed": resumed_days,
        "percent": 2 if not resumed_days else min(8, 2 + resumed_days),
    })
    raw_records, projection_warnings = await collect_historical_starts(request.start_date, request.end_date, request.max_days)
    warnings.extend(projection_warnings)
    by_date_projection: dict[str, dict[str, dict]] = defaultdict(dict)
    actuals: dict[tuple[str, str], int] = {}
    for r in raw_records:
        p = r.get("projection_details")
        if p:
            by_date_projection[r["game_date"]][r["player"].strip().lower()] = p
        actuals[(r["game_date"], _norm(r["player"]))] = int(r["actual_strikeouts"])

    def _restore_records(key: str) -> list[HistoricalMarketRecord]:
        return [HistoricalMarketRecord(**r) for r in (resume_state.get(key) or [])]

    all_unlimited: list[HistoricalMarketRecord] = _restore_records("all_unlimited")
    all_edge_first: list[HistoricalMarketRecord] = _restore_records("all_edge_first")
    all_selector: list[HistoricalMarketRecord] = _restore_records("all_selector")
    all_4yes: list[HistoricalMarketRecord] = _restore_records("all_4yes")
    all_extreme: list[HistoricalMarketRecord] = _restore_records("all_extreme")
    daily_results: list[dict] = list(resume_state.get("daily_results") or [])
    totals = resume_state.get("totals") or {}
    total_found = int(totals.get("markets_found", 0))
    total_quotes = int(totals.get("usable_quotes", 0))
    total_eval = int(totals.get("recommendations_evaluated", 0))
    total_qualifiers = int(totals.get("qualifiers", 0))
    matched_pitchers = int(totals.get("matched_pitchers", 0))

    await _emit_progress(progress_callback, {
        "phase": "processing_slates",
        "message": f"Historical inputs ready. Processing {days} slate day(s)…",
        "days_total": days,
        "days_processed": len(daily_results),
        "projected_starters": len(raw_records),
        "percent": 8,
    })

    headers = {"User-Agent": "KalshiTradingPlatform/2.6.6-background-historical-backtest"}
    async with httpx.AsyncClient(headers=headers) as client:
        completed_dates = {r.get("date") for r in daily_results if r.get("date")}
        current = start
        while current <= end and current.isoformat() in completed_dates:
            current += timedelta(days=1)
        while current <= end:
            ds = current.isoformat()
            quotes, found = await _quotes_for_date(client, ds, request.hours_before_first_pitch, request.quote_lookback_hours, warnings)
            total_found += found; total_quotes += len(quotes)
            markets = [m for q in quotes if (m := _market_from_quote(q)) is not None]
            projections = by_date_projection.get(ds, {})
            matched_names = {m.player.strip().lower() for m in markets if m.player.strip().lower() in projections}
            matched_pitchers += len(matched_names)

            pipeline = SimpleNamespace(projections=projections)
            card_request = PaperCardRequest(
                bankroll=request.bankroll,
                already_committed_today=0,
                max_bet=request.unit_size,
                date=ds,
                minimum_edge_points=request.minimum_edge_points,
                use_automatic_data=False,
            )
            recs, _ = build_card_from_pipeline(markets, card_request, pipeline)
            total_eval += len(recs)
            deployable = [r for r in recs if r.decision == "MODEL EDGE" and not r.research_only and r.model_units > 0]
            total_qualifiers += len(deployable)

            # Unlimited = every deployable candidate at frozen model sizing.
            day_unlimited = []
            for rec in deployable:
                actual = actuals.get((ds, _norm(rec.player)))
                if actual is None:
                    warnings.append(f"{ds} {rec.player}: no actual strikeout result matched; skipped.")
                    continue
                row = _record_from_rec(rec, ds, actual, rec.unlimited_bankroll_stake, request.model_version)
                if row: day_unlimited.append(row)

            # Edge-first $5 control: same candidates, descending calibrated adjusted edge.
            remaining = request.daily_cap_dollars
            day_edge = []
            for rec in sorted(deployable, key=lambda r: (-(r.adjusted_edge_points or -999), -r.confidence.get("overall", 0), r.player)):
                stake = min(float(rec.unlimited_bankroll_stake), remaining)
                remaining = round(max(0.0, remaining - stake), 2)
                if stake <= 0: continue
                actual = actuals.get((ds, _norm(rec.player)))
                if actual is None: continue
                row = _record_from_rec(rec, ds, actual, stake, request.model_version)
                if row: day_edge.append(row)

            # Selector v2 = actual frozen card allocation. Override default 5% cap when requested.
            # build_card_from_pipeline uses 5% of bankroll; choose an equivalent synthetic bankroll
            # if the requested historical daily cap differs from 5% of the displayed bankroll.
            if abs(request.daily_cap_dollars - request.bankroll * 0.05) > 1e-9:
                selector_request = card_request.model_copy(update={"bankroll": request.daily_cap_dollars / 0.05 if request.daily_cap_dollars else 0})
                recs_selector, _ = build_card_from_pipeline(markets, selector_request, pipeline)
            else:
                recs_selector = recs
            day_selector = []
            for rec in recs_selector:
                if rec.research_only or rec.suggested_stake <= 0 or rec.decision != "MODEL EDGE": continue
                actual = actuals.get((ds, _norm(rec.player)))
                if actual is None: continue
                row = _record_from_rec(rec, ds, actual, rec.suggested_stake, request.model_version)
                if row: day_selector.append(row)

            # Research cohorts use hypothetical research stakes only.
            day_4, day_extreme = [], []
            for rec in recs:
                if not rec.research_only or rec.research_stake <= 0: continue
                actual = actuals.get((ds, _norm(rec.player)))
                if actual is None: continue
                row = _record_from_rec(rec, ds, actual, rec.research_stake, request.model_version)
                if not row: continue
                if rec.research_reason == "4+ YES CALIBRATION GUARDRAIL": day_4.append(row)
                if rec.research_reason and "EXTREME DISAGREEMENT" in rec.research_reason: day_extreme.append(row)

            all_unlimited.extend(day_unlimited); all_edge_first.extend(day_edge); all_selector.extend(day_selector)
            all_4yes.extend(day_4); all_extreme.extend(day_extreme)
            daily_results.append({
                "date": ds,
                "markets_found": found,
                "usable_quotes": len(quotes),
                "matched_pitchers": len(matched_names),
                "qualifiers": len(day_unlimited),
                "unlimited": _strategy_summary(day_unlimited),
                "edge_first": _strategy_summary(day_edge),
                "selector_v2": _strategy_summary(day_selector),
            })
            processed = len(daily_results)
            await _emit_progress(progress_callback, {
                "phase": "processing_slates",
                "message": f"Processed {processed} of {days} slate day(s).",
                "days_total": days,
                "days_processed": processed,
                "current_date": ds,
                "markets_found": total_found,
                "usable_quotes": total_quotes,
                "matched_pitchers": matched_pitchers,
                "qualifiers": total_qualifiers,
                "percent": min(98, 8 + round(90 * processed / max(days, 1))),
            })
            if checkpoint_callback is not None:
                checkpoint = {
                    "daily_results": daily_results,
                    "all_unlimited": [r.model_dump() for r in all_unlimited],
                    "all_edge_first": [r.model_dump() for r in all_edge_first],
                    "all_selector": [r.model_dump() for r in all_selector],
                    "all_4yes": [r.model_dump() for r in all_4yes],
                    "all_extreme": [r.model_dump() for r in all_extreme],
                    "totals": {
                        "markets_found": total_found,
                        "usable_quotes": total_quotes,
                        "recommendations_evaluated": total_eval,
                        "qualifiers": total_qualifiers,
                        "matched_pitchers": matched_pitchers,
                    },
                    "warnings": warnings,
                    "last_completed_date": ds,
                }
                value = checkpoint_callback(checkpoint)
                if inspect.isawaitable(value):
                    await value
            current += timedelta(days=1)

    strategy_results = {
        "unlimited_model": _strategy_summary(all_unlimited),
        "edge_first_5_control": _strategy_summary(all_edge_first),
        "portfolio_selector_v2": _strategy_summary(all_selector),
    }
    watchlists = {
        "4plus_yes": _strategy_summary(all_4yes),
        "extreme_disagreement": _strategy_summary(all_extreme),
    }
    result = {
        "status": "success",
        "start_date": request.start_date,
        "end_date": request.end_date,
        "days_requested": days,
        "days_processed": len(daily_results),
        "entry_rule": f"latest quoted 1-minute executable ask at or before T-{request.hours_before_first_pitch:g}h; {request.quote_lookback_hours:g}h max quote lookback",
        "leakage_policy": [
            "Pitcher season/recent/career inputs include only starts before the historical game date.",
            "Opponent strikeout profile is calculated only through the day before the historical game.",
            "Historical lineups are not backfilled from final-game lineups; lineup_confirmed remains false.",
            "Market price is the last quoted 1-minute bid/ask at or before the pre-registered T-2h entry timestamp.",
            "Actual strikeouts are used only after recommendation generation for settlement.",
            "Frozen v2.6.x qualification, calibration, guardrails, sizing, and Portfolio Selector v2 rules are replayed without optimization.",
        ],
        "markets_found": total_found,
        "usable_quotes": total_quotes,
        "projected_starters": len(raw_records),
        "matched_pitchers": matched_pitchers,
        "recommendations_evaluated": total_eval,
        "unique_qualifiers": len({r.ticker for r in all_unlimited if r.ticker}),
        "strategy_results": strategy_results,
        "daily_results": daily_results,
        "research_watchlists": watchlists,
        "warnings": warnings,
    }
    await _emit_progress(progress_callback, {
        "phase": "completed",
        "message": f"Backtest complete: {len(daily_results)} day(s), {len({r.ticker for r in all_unlimited if r.ticker})} unique qualifiers.",
        "days_total": days,
        "days_processed": len(daily_results),
        "markets_found": total_found,
        "usable_quotes": total_quotes,
        "qualifiers": total_qualifiers,
        "percent": 100,
    })
    return result
