"""Exploratory MLB strikeout momentum execution backtest.

Research-only. This hypothesis was discovered after reviewing the July-August 2026
market-efficiency map, so results on that same period are descriptive/discovery-sample
only and are NOT a clean holdout.

Primary rule:
- Observe YES midpoint from T-4h to T-2h.
- If absolute move >= 3 cents, enter at T-2h in the SAME direction.
- Up move: buy YES at executable ask.
- Down move: buy NO at executable ask (100 - YES bid).
- Evaluate two frozen exits independently: T-1h and T-10m, using executable bid.
- Include Kalshi taker fees on entry and exit.
"""

from __future__ import annotations

import asyncio
import math
from collections import defaultdict
from datetime import datetime, timedelta

import httpx

from historical_trading_backtest import _quotes_for_date


def kalshi_order_fee_cents(contracts: int, price_cents: float, fee_rate: float = 0.07) -> int:
    if contracts <= 0 or price_cents <= 0 or price_cents >= 100 or fee_rate <= 0:
        return 0
    p = price_cents / 100.0
    return math.ceil(100 * fee_rate * contracts * p * (1 - p) - 1e-12)


def _snap(row: dict, max_age: float, max_spread: int):
    bid = row.get("yes_bid_cents")
    ask = row.get("yes_ask_cents")
    age = row.get("quote_age_minutes")
    if bid is None or ask is None or age is None:
        return None
    bid, ask, age = int(bid), int(ask), float(age)
    if age < 0 or age > max_age or ask < bid or ask - bid > max_spread:
        return None
    return {
        "yes_bid": bid,
        "yes_ask": ask,
        "no_bid": 100 - ask,
        "no_ask": 100 - bid,
        "mid": (bid + ask) / 2.0,
        "spread": ask - bid,
        "age": age,
    }


def _execution(entry_ask: int, exit_bid: int, unit_size: float, fee_rate: float):
    budget = round(unit_size * 100)
    contracts = math.floor(budget / max(1, entry_ask))
    while contracts > 0:
        entry_fee = kalshi_order_fee_cents(contracts, entry_ask, fee_rate)
        if contracts * entry_ask + entry_fee <= budget:
            break
        contracts -= 1
    if contracts < 1:
        return None
    entry_fee = kalshi_order_fee_cents(contracts, entry_ask, fee_rate)
    exit_fee = kalshi_order_fee_cents(contracts, exit_bid, fee_rate)
    capital = contracts * entry_ask + entry_fee
    proceeds = contracts * exit_bid - exit_fee
    pnl = proceeds - capital
    return {
        "contracts": contracts,
        "capital_used": round(capital / 100, 2),
        "entry_fee": round(entry_fee / 100, 2),
        "exit_fee": round(exit_fee / 100, 2),
        "total_fees": round((entry_fee + exit_fee) / 100, 2),
        "gross_price_move_cents": exit_bid - entry_ask,
        "profit_loss": round(pnl / 100, 2),
        "profitable": pnl > 0,
    }


def _metrics(rows: list[dict]):
    capital = round(sum(r["capital_used"] for r in rows), 2)
    pnl = round(sum(r["profit_loss"] for r in rows), 2)
    wins = sum(1 for r in rows if r["profitable"])
    equity = peak = max_dd = 0.0
    for r in sorted(rows, key=lambda x: (x["date"], x["ticker"])):
        equity += r["profit_loss"]
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
    return {
        "trades": len(rows),
        "wins": wins,
        "hit_rate": round(wins / len(rows), 4) if rows else None,
        "capital_used": capital,
        "profit_loss": pnl,
        "roi": round(pnl / capital, 4) if capital else None,
        "avg_signal_move_cents": round(sum(abs(r["signal_move_cents"]) for r in rows) / len(rows), 3) if rows else None,
        "avg_gross_exit_move_cents": round(sum(r["gross_price_move_cents"] for r in rows) / len(rows), 3) if rows else None,
        "avg_total_fees": round(sum(r["total_fees"] for r in rows) / len(rows), 3) if rows else None,
        "maximum_drawdown": round(max_dd, 2),
    }


async def run_momentum_backtest(
    start_date: str = "2026-07-01",
    end_date: str = "2026-08-31",
    trigger_cents: float = 3.0,
    unit_size: float = 1.0,
    fee_rate: float = 0.07,
    max_age_minutes: float = 20.0,
    max_spread_cents: int = 12,
    lookback_hours: float = 6.0,
    progress_callback=None,
):
    start = datetime.strptime(start_date, "%Y-%m-%d").date()
    end = datetime.strptime(end_date, "%Y-%m-%d").date()
    if end < start:
        raise ValueError("end_date must be on or after start_date")
    days = (end - start).days + 1
    offsets = {"T-4h": 4.0, "T-2h": 2.0, "T-1h": 1.0, "T-10m": 1.0 / 6.0}
    warnings: list[str] = []
    snapshots: dict[str, dict[str, dict]] = defaultdict(dict)
    metadata: dict[str, dict] = {}

    async def emit(payload):
        if progress_callback:
            v = progress_callback(payload)
            if hasattr(v, "__await__"):
                await v

    async with httpx.AsyncClient(headers={"User-Agent": "KalshiTradingPlatform/4.2.0-strikeout-momentum"}, timeout=60) as client:
        for i in range(days):
            ds = (start + timedelta(days=i)).isoformat()
            await emit({"day": i + 1, "days": days, "date": ds})
            for label, hours in offsets.items():
                quotes, _ = await _quotes_for_date(client, ds, hours, lookback_hours, warnings)
                for row in quotes:
                    ticker = str(row.get("ticker") or "")
                    snap = _snap(row, max_age_minutes, max_spread_cents)
                    if not ticker or snap is None:
                        continue
                    snapshots[ticker][label] = snap
                    metadata[ticker] = {"date": ds, "player": row.get("player"), "threshold": row.get("threshold")}

    exit_rows = {"T-1h": [], "T-10m": []}
    diagnostics = defaultdict(int)
    for ticker, snaps in snapshots.items():
        if "T-4h" not in snaps or "T-2h" not in snaps:
            diagnostics["missing_signal_path"] += 1
            continue
        signal = snaps["T-2h"]["mid"] - snaps["T-4h"]["mid"]
        if abs(signal) < trigger_cents:
            diagnostics["below_trigger"] += 1
            continue
        side = "YES" if signal > 0 else "NO"
        entry = snaps["T-2h"]["yes_ask" if side == "YES" else "no_ask"]
        for exit_label in ("T-1h", "T-10m"):
            if exit_label not in snaps:
                diagnostics[f"missing_{exit_label}_exit"] += 1
                continue
            exit_bid = snaps[exit_label]["yes_bid" if side == "YES" else "no_bid"]
            ex = _execution(entry, exit_bid, unit_size, fee_rate)
            if not ex:
                diagnostics["unit_too_small"] += 1
                continue
            exit_rows[exit_label].append({
                "ticker": ticker,
                **metadata.get(ticker, {}),
                "side": side,
                "signal_move_cents": round(signal, 3),
                "entry_ask_cents": entry,
                "exit_bid_cents": exit_bid,
                "entry_spread_cents": snaps["T-2h"]["spread"],
                **ex,
            })
            diagnostics[f"qualified_{exit_label}"] += 1

    by_exit = {}
    for exit_label, rows in exit_rows.items():
        by_month = {m: _metrics([r for r in rows if r["date"].startswith(m)]) for m in sorted({r["date"][:7] for r in rows})}
        by_side = {s: _metrics([r for r in rows if r["side"] == s]) for s in ("YES", "NO")}
        by_exit[exit_label] = {"overall": _metrics(rows), "by_month": by_month, "by_side": by_side, "trades": rows}

    return {
        "version": "4.2.0",
        "mode": "exploratory-strikeout-momentum-execution-backtest",
        "research_only": True,
        "discovery_sample_warning": "The 3-cent momentum hypothesis was chosen after reviewing July-August efficiency-map data, so this same-period backtest is not an independent holdout.",
        "date_range": {"start": start_date, "end": end_date, "days": days},
        "rule": {
            "signal": "Absolute YES midpoint move from T-4h to T-2h >= 3 cents",
            "direction": "Enter in the same direction as the signal",
            "entry": "Executable T-2h ask",
            "exits_tested_independently": ["Executable T-1h bid", "Executable T-10m bid"],
            "unit_size": unit_size,
            "fee_rate": fee_rate,
        },
        "by_exit": by_exit,
        "diagnostics": dict(diagnostics),
        "warnings": warnings,
        "notes": [
            "This test uses executable ask-to-bid prices, not midpoints, for P/L.",
            "YES and NO are both supported; NO prices are the binary complements of the YES book.",
            "Entry and exit taker fees are included.",
            "No settlement outcome is used because the strategy exits before first pitch.",
            "Any attractive result needs a genuinely new holdout/prospective sample before promotion.",
        ],
    }
