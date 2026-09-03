"""v3.8 MLB line-movement research lab.

Tests one pre-registered hypothesis: a sufficiently large pregame Kalshi price
decline between T-4h and T-90m partially mean-reverts by T-10m. It trades price
movement only; game outcomes never enter the result.
"""

from __future__ import annotations

import asyncio
import math
from collections import defaultdict
from datetime import date, datetime, timedelta

import httpx
from pydantic import BaseModel, Field, model_validator

from config import KALSHI_BASE_URL, KALSHI_HISTORICAL_BASE_URL
from hybrid_historical_backtest import (
    ET, UTC, _close, _event_date, _event_start, _last_candle_at_or_before,
    _list_markets, _request_json, _to_cents,
)


class V38LineMovementRequest(BaseModel):
    start_date: str
    end_date: str
    max_days: int = Field(default=31, ge=1, le=31)
    unit_size: float = Field(default=1.0, ge=0.25, le=100)
    observation_minutes: int = Field(default=240, ge=120, le=720)
    entry_minutes: int = Field(default=90, ge=30, le=180)
    exit_minutes: int = Field(default=10, ge=5, le=30)
    trigger_cents: float = Field(default=4.0, ge=1, le=20)
    minimum_entry_cents: int = Field(default=15, ge=1, le=49)
    maximum_entry_cents: int = Field(default=85, ge=51, le=99)
    maximum_quote_age_minutes: int = Field(default=20, ge=1, le=60)
    holdout_fraction: float = Field(default=0.30, ge=0.20, le=0.50)
    fee_rate: float = Field(default=0.07, ge=0, le=0.20)

    @model_validator(mode="after")
    def validate_timeline(self):
        if not self.observation_minutes > self.entry_minutes > self.exit_minutes:
            raise ValueError("Times must satisfy observation > entry > exit minutes before first pitch.")
        if self.minimum_entry_cents >= self.maximum_entry_cents:
            raise ValueError("Minimum entry price must be below maximum entry price.")
        return self


def _side_close(candle: dict | None, side: str) -> int | None:
    return _to_cents(_close((candle or {}).get(side)))


def _snapshot(candles: list[dict], target_ts: int) -> dict:
    candle = _last_candle_at_or_before(candles, target_ts)
    quote_ts = int(candle.get("end_period_ts")) if candle and candle.get("end_period_ts") else None
    return {
        "yes_bid": _side_close(candle, "yes_bid"),
        "yes_ask": _side_close(candle, "yes_ask"),
        "quote_ts": quote_ts,
        "quote_age_minutes": round((target_ts - quote_ts) / 60, 2) if quote_ts else None,
    }


async def _candles_at_offsets(client, tagged, offsets, warnings, progress_callback=None):
    """Fetch one candle series per contract, then sample every declared time."""
    historical_base = KALSHI_HISTORICAL_BASE_URL.rstrip("/")
    recent_base = KALSHI_BASE_URL.rstrip("/")
    output = {}
    by_day = defaultdict(list)
    for item in tagged:
        day = _event_date(str(item[0].get("event_ticker") or ""))
        if day:
            by_day[day].append(item)

    def save(market, candles):
        ticker = str(market.get("ticker") or "")
        start = _event_start(str(market.get("event_ticker") or ""))
        if ticker and start:
            output[ticker] = {
                offset: _snapshot(candles, int((start - timedelta(minutes=offset)).astimezone(UTC).timestamp()))
                for offset in offsets
            }

    day_rows = sorted(by_day.items())
    for day_index, (day, items) in enumerate(day_rows, start=1):
        recent = [market for market, historical in items if not historical]
        archive = [market for market, historical in items if historical]
        specs = []
        for market in recent:
            start = _event_start(str(market.get("event_ticker") or ""))
            if start:
                specs.append((market,
                    int((start - timedelta(minutes=max(offsets) + 90)).astimezone(UTC).timestamp()),
                    int((start - timedelta(minutes=min(offsets))).astimezone(UTC).timestamp())))
        for chunk_start in range(0, len(specs), 10):
            chunk = specs[chunk_start:chunk_start + 10]
            try:
                payload = await _request_json(client, recent_base + "/markets/candlesticks", params={
                    "market_tickers": ",".join(str(m.get("ticker")) for m, _, _ in chunk),
                    "start_ts": min(row[1] for row in chunk), "end_ts": max(row[2] for row in chunk),
                    "period_interval": 1, "include_latest_before_start": "true",
                }, max_429_retries=6, base_backoff_seconds=0.5)
                candle_map = {str(row.get("market_ticker") or row.get("ticker")): row.get("candlesticks", []) for row in payload.get("markets", [])}
                for market, _, _ in chunk:
                    save(market, candle_map.get(str(market.get("ticker")), []))
            except Exception as exc:
                warnings.append(f"{day}: recent candle batch failed: {exc}")

        semaphore = asyncio.Semaphore(5)
        async def archive_one(market):
            ticker = str(market.get("ticker") or "")
            start = _event_start(str(market.get("event_ticker") or ""))
            if not ticker or not start:
                return
            try:
                async with semaphore:
                    payload = await _request_json(client, f"{historical_base}/historical/markets/{ticker}/candlesticks", params={
                        "start_ts": int((start - timedelta(minutes=max(offsets) + 90)).astimezone(UTC).timestamp()),
                        "end_ts": int((start - timedelta(minutes=min(offsets))).astimezone(UTC).timestamp()),
                        "period_interval": 1,
                    }, max_429_retries=6, base_backoff_seconds=0.55)
                save(market, payload.get("candlesticks", []))
            except Exception as exc:
                warnings.append(f"{ticker}: historical candles failed: {exc}")
        if archive:
            await asyncio.gather(*(archive_one(market) for market in archive))
        if progress_callback:
            returned = progress_callback(day_index, len(day_rows))
            if asyncio.iscoroutine(returned):
                await returned
    return output


def kalshi_order_fee_cents(contracts: int, price_cents: float, fee_rate: float = 0.07) -> int:
    """Published taker fee formula, rounded up to the next cent per order."""
    if contracts <= 0 or price_cents <= 0 or price_cents >= 100 or fee_rate <= 0:
        return 0
    probability = price_cents / 100
    return math.ceil(100 * fee_rate * contracts * probability * (1 - probability) - 1e-12)


def _trade_result(entry_ask: int, exit_bid: int, unit_size: float, fee_rate: float) -> dict | None:
    contracts = math.floor(unit_size * 100 / entry_ask)
    budget_cents = round(unit_size * 100)
    while contracts > 0 and contracts * entry_ask + kalshi_order_fee_cents(contracts, entry_ask, fee_rate) > budget_cents:
        contracts -= 1
    if contracts < 1:
        return None
    entry_fee = kalshi_order_fee_cents(contracts, entry_ask, fee_rate)
    exit_fee = kalshi_order_fee_cents(contracts, exit_bid, fee_rate)
    cost_cents = contracts * entry_ask + entry_fee
    proceeds_cents = contracts * exit_bid - exit_fee
    pnl = (proceeds_cents - cost_cents) / 100
    return {"contracts": contracts, "capital_used": round(cost_cents / 100, 2),
            "entry_fee": round(entry_fee / 100, 2), "exit_fee": round(exit_fee / 100, 2),
            "total_fees": round((entry_fee + exit_fee) / 100, 2),
            "gross_move_cents": exit_bid - entry_ask, "profit_loss": round(pnl, 2),
            "profitable": pnl > 0}


def _metrics(rows):
    capital = round(sum(row["capital_used"] for row in rows), 2)
    pnl = round(sum(row["profit_loss"] for row in rows), 2)
    equity = peak = drawdown = 0.0
    for row in sorted(rows, key=lambda item: (item["date"], item["game_start_time"], item["ticker"])):
        equity += row["profit_loss"]; peak = max(peak, equity); drawdown = max(drawdown, peak - equity)
    wins = sum(1 for row in rows if row["profitable"])
    return {"trades": len(rows), "profitable_trades": wins,
            "hit_rate": round(wins / len(rows), 4) if rows else None,
            "capital_used": capital, "profit_loss": pnl,
            "roi": round(pnl / capital, 4) if capital else None,
            "average_net_move_cents": round(sum(row["net_move_cents"] for row in rows) / len(rows), 3) if rows else None,
            "average_total_fees": round(sum(row["total_fees"] for row in rows) / len(rows), 3) if rows else None,
            "maximum_drawdown": round(drawdown, 2)}


def analyze_line_movement(grouped_markets, snapshots, request):
    trades, diagnostics = [], defaultdict(int)
    for event_ticker, markets in grouped_markets.items():
        diagnostics["events_reviewed"] += 1
        start, day = _event_start(event_ticker), _event_date(event_ticker)
        if not start or not day:
            diagnostics["invalid_event_time"] += 1; continue
        candidates = []
        for market in markets:
            ticker = str(market.get("ticker") or "")
            series = snapshots.get(ticker) or {}
            observation = series.get(request.observation_minutes) or {}
            entry = series.get(request.entry_minutes) or {}
            exit_quote = series.get(request.exit_minutes) or {}
            if any(value is None for value in [observation.get("yes_ask"), entry.get("yes_ask"), exit_quote.get("yes_bid")]):
                diagnostics["missing_executable_quote"] += 1; continue
            ages = [observation.get("quote_age_minutes"), entry.get("quote_age_minutes"), exit_quote.get("quote_age_minutes")]
            if any(age is None or age < 0 or age > request.maximum_quote_age_minutes for age in ages):
                diagnostics["stale_quote"] += 1; continue
            candidates.append((observation["yes_ask"] - entry["yes_ask"], market, observation, entry, exit_quote))
        if not candidates:
            continue
        decline, market, observation, entry, exit_quote = max(candidates, key=lambda row: row[0])
        if decline < request.trigger_cents:
            diagnostics["below_trigger"] += 1; continue
        entry_ask, exit_bid = int(entry["yes_ask"]), int(exit_quote["yes_bid"])
        if not request.minimum_entry_cents <= entry_ask <= request.maximum_entry_cents:
            diagnostics["entry_outside_range"] += 1; continue
        result = _trade_result(entry_ask, exit_bid, request.unit_size, request.fee_rate)
        if not result:
            diagnostics["unit_too_small"] += 1; continue
        trades.append({"date": day.isoformat(), "ticker": str(market.get("ticker") or ""),
            "selection": market.get("title") or market.get("subtitle") or str(market.get("ticker") or ""),
            "event_ticker": event_ticker, "game_start_time": start.isoformat(),
            "weekend_day_game": start.weekday() >= 5 and start.astimezone(ET).hour < 17,
            "observation_ask_cents": int(observation["yes_ask"]), "entry_ask_cents": entry_ask,
            "exit_bid_cents": exit_bid, "trigger_decline_cents": round(decline, 2),
            "net_move_cents": exit_bid - entry_ask,
            "observation_quote_age_minutes": observation["quote_age_minutes"],
            "entry_quote_age_minutes": entry["quote_age_minutes"], "exit_quote_age_minutes": exit_quote["quote_age_minutes"],
            **result})
        diagnostics["qualified_trades"] += 1
    return trades, dict(diagnostics)


async def run_v38_line_movement_backtest(request, progress_callback=None):
    start = datetime.strptime(request.start_date, "%Y-%m-%d").date()
    end = datetime.strptime(request.end_date, "%Y-%m-%d").date()
    if end < start: raise ValueError("end_date must be on or after start_date.")
    days = (end - start).days + 1
    if days > request.max_days: raise ValueError(f"Requested {days} days; maximum for this run is {request.max_days}.")
    if end >= datetime.now(ET).date(): raise ValueError("The backtest end date must be before today.")
    async def emit(phase, percent, message):
        if progress_callback:
            returned = progress_callback({"phase": phase, "percent": percent, "message": message})
            if asyncio.iscoroutine(returned): await returned
    warnings = []
    await emit("markets", 5, "Finding historical MLB game contracts…")
    async with httpx.AsyncClient(headers={"User-Agent": "KalshiTradingPlatform/3.8.0-line-movement-lab"}, timeout=60) as client:
        tagged = await _list_markets(client, start, end)
        await emit("candles", 25, f"Sampling {len(tagged)} contracts at three frozen decision times…")
        async def candle_progress(completed_days, total_days):
            percent = 25 + round(55 * completed_days / max(1, total_days))
            await emit("candles", percent, f"Sampled archived quotes for {completed_days}/{total_days} slate days…")
        snapshots = await _candles_at_offsets(client, tagged, [request.observation_minutes, request.entry_minutes, request.exit_minutes], warnings, candle_progress)
    grouped = defaultdict(list)
    for market, _ in tagged: grouped[str(market.get("event_ticker") or "")].append(market)
    await emit("analysis", 85, "Applying the frozen trigger and executable ask-to-bid exit…")
    trades, diagnostics = analyze_line_movement(grouped, snapshots, request)
    split_date = start + timedelta(days=max(1, math.floor(days * (1 - request.holdout_fraction))))
    training = [row for row in trades if datetime.strptime(row["date"], "%Y-%m-%d").date() < split_date]
    holdout = [row for row in trades if datetime.strptime(row["date"], "%Y-%m-%d").date() >= split_date]
    weekend = [row for row in trades if row["weekend_day_game"]]
    await emit("complete", 100, f"Complete: {len(trades)} independent price-movement trades found.")
    return {"version": "3.8.0", "status": "complete", "mode": "historical-price-movement-paper-only",
        "hypothesis": "A >=4-cent decline from T-4h to T-90m partially mean-reverts by T-10m.",
        "entry_rule": f"Buy the single largest YES decline per game when it is at least {request.trigger_cents:g} cents; pay the T-{request.entry_minutes} ask.",
        "exit_rule": f"Sell at the T-{request.exit_minutes} YES bid. Never hold through the game.",
        "fee_rule": f"Taker fees on entry and exit: ceil({request.fee_rate:g} x contracts x price x (1-price)) per order.",
        "split_date": split_date.isoformat(), "overall": _metrics(trades), "training": _metrics(training),
        "holdout": _metrics(holdout), "weekend_day_games": _metrics(weekend),
        "coverage": {"market_contracts": len(tagged), "market_events": len(grouped), "contracts_with_snapshots": len(snapshots), **diagnostics},
        "trades": sorted(trades, key=lambda row: (row["date"], row["game_start_time"], row["ticker"])),
        "limitations": ["This is the first locked research specification, not evidence of future profitability.",
            "Historical candlesticks may omit quotes; stale or non-executable observations are rejected.",
            "The test uses observed ask entries and bid exits plus taker fees, but cannot recreate queue position or partial fills.",
            "The weekend-day result is a predeclared subgroup and should not replace the overall holdout result."],
        "warnings": warnings[:50]}
