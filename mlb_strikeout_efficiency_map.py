"""Descriptive MLB strikeout market-efficiency map.

Research-only. Reconstructs KXMLBKS quotes at several pregame timestamps and
measures where price discovery happens. It does not create a betting rule and
never uses game outcomes.
"""

from __future__ import annotations

import asyncio
import math
from collections import defaultdict
from datetime import datetime, timedelta

import httpx

from historical_trading_backtest import _quotes_for_date

OFFSETS_HOURS = (8.0, 6.0, 4.0, 2.0, 1.0, 1.0 / 6.0)  # final point = T-10m
OFFSET_LABELS = {
    8.0: "T-8h", 6.0: "T-6h", 4.0: "T-4h", 2.0: "T-2h", 1.0: "T-1h", 1.0 / 6.0: "T-10m"
}


def _median(values):
    xs = sorted(values)
    if not xs:
        return None
    n = len(xs)
    return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2


def _pct(values, predicate):
    return round(sum(1 for v in values if predicate(v)) / len(values), 4) if values else None


def _snapshot(row: dict, max_age_minutes: float, max_spread_cents: int):
    bid = row.get("yes_bid_cents")
    ask = row.get("yes_ask_cents")
    age = row.get("quote_age_minutes")
    if bid is None or ask is None or age is None:
        return None
    bid, ask, age = int(bid), int(ask), float(age)
    if age < 0 or age > max_age_minutes or ask < bid or ask - bid > max_spread_cents:
        return None
    return {
        "bid": bid,
        "ask": ask,
        "mid": (bid + ask) / 2.0,
        "spread": ask - bid,
        "age": age,
    }


def _summarize_interval(rows: list[dict]):
    moves = [r["mid_move"] for r in rows]
    absolute = [abs(v) for v in moves]
    return {
        "contracts": len(rows),
        "mean_signed_mid_move_cents": round(sum(moves) / len(moves), 3) if moves else None,
        "mean_absolute_mid_move_cents": round(sum(absolute) / len(absolute), 3) if absolute else None,
        "median_absolute_mid_move_cents": round(_median(absolute), 3) if absolute else None,
        "pct_abs_move_ge_2c": _pct(absolute, lambda x: x >= 2),
        "pct_abs_move_ge_3c": _pct(absolute, lambda x: x >= 3),
        "pct_abs_move_ge_5c": _pct(absolute, lambda x: x >= 5),
        "median_start_spread_cents": round(_median([r["start_spread"] for r in rows]), 3) if rows else None,
        "median_end_spread_cents": round(_median([r["end_spread"] for r in rows]), 3) if rows else None,
    }


def _continuation(rows_by_ticker: dict[str, dict[str, dict]]):
    labels = [OFFSET_LABELS[o] for o in OFFSETS_HOURS]
    output = []
    for i in range(len(labels) - 2):
        a, b, c = labels[i:i + 3]
        qualifying = []
        for ticker, snaps in rows_by_ticker.items():
            if a not in snaps or b not in snaps or c not in snaps:
                continue
            first = snaps[b]["mid"] - snaps[a]["mid"]
            second = snaps[c]["mid"] - snaps[b]["mid"]
            if abs(first) < 3:
                continue
            qualifying.append((first, second))
        same = [1 for first, second in qualifying if second != 0 and math.copysign(1, first) == math.copysign(1, second)]
        reverse = [1 for first, second in qualifying if second != 0 and math.copysign(1, first) != math.copysign(1, second)]
        output.append({
            "signal_window": f"{a}→{b}",
            "next_window": f"{b}→{c}",
            "contracts_with_prior_abs_move_ge_3c": len(qualifying),
            "continuation_rate": round(len(same) / len(qualifying), 4) if qualifying else None,
            "reversal_rate": round(len(reverse) / len(qualifying), 4) if qualifying else None,
            "mean_next_signed_in_prior_direction_cents": round(sum((1 if first > 0 else -1) * second for first, second in qualifying) / len(qualifying), 3) if qualifying else None,
        })
    return output


async def run_efficiency_map(
    start_date: str = "2026-07-01",
    end_date: str = "2026-08-31",
    lookback_hours: float = 6.0,
    max_age_minutes: float = 20.0,
    max_spread_cents: int = 12,
    progress_callback=None,
):
    start = datetime.strptime(start_date, "%Y-%m-%d").date()
    end = datetime.strptime(end_date, "%Y-%m-%d").date()
    if end < start:
        raise ValueError("end_date must be on or after start_date")
    days = (end - start).days + 1
    warnings: list[str] = []
    rows_by_ticker: dict[str, dict[str, dict]] = defaultdict(dict)
    metadata: dict[str, dict] = {}
    coverage = defaultdict(int)

    async def emit(message):
        if progress_callback:
            value = progress_callback(message)
            if hasattr(value, "__await__"):
                await value

    async with httpx.AsyncClient(headers={"User-Agent": "KalshiTradingPlatform/4.1.0-strikeout-efficiency-map"}, timeout=60) as client:
        for day_index in range(days):
            ds = (start + timedelta(days=day_index)).isoformat()
            await emit({"day": day_index + 1, "days": days, "date": ds})
            for offset in OFFSETS_HOURS:
                quotes, found = await _quotes_for_date(client, ds, offset, lookback_hours, warnings)
                coverage[f"markets_found_{OFFSET_LABELS[offset]}"] += found
                coverage[f"quotes_returned_{OFFSET_LABELS[offset]}"] += len(quotes)
                for row in quotes:
                    ticker = str(row.get("ticker") or "")
                    snap = _snapshot(row, max_age_minutes, max_spread_cents)
                    if not ticker or snap is None:
                        continue
                    rows_by_ticker[ticker][OFFSET_LABELS[offset]] = snap
                    metadata[ticker] = {
                        "date": ds,
                        "player": row.get("player"),
                        "threshold": row.get("threshold"),
                    }

    labels = [OFFSET_LABELS[o] for o in OFFSETS_HOURS]
    intervals = []
    interval_rows = {}
    for a, b in zip(labels, labels[1:]):
        rows = []
        for ticker, snaps in rows_by_ticker.items():
            if a not in snaps or b not in snaps:
                continue
            rows.append({
                "ticker": ticker,
                **metadata.get(ticker, {}),
                "start": a,
                "end": b,
                "start_mid": snaps[a]["mid"],
                "end_mid": snaps[b]["mid"],
                "mid_move": snaps[b]["mid"] - snaps[a]["mid"],
                "start_spread": snaps[a]["spread"],
                "end_spread": snaps[b]["spread"],
            })
        name = f"{a}→{b}"
        interval_rows[name] = rows
        intervals.append({"interval": name, **_summarize_interval(rows)})

    full_path = []
    for ticker, snaps in rows_by_ticker.items():
        if all(label in snaps for label in labels):
            mids = [snaps[label]["mid"] for label in labels]
            full_path.append({
                "ticker": ticker,
                **metadata.get(ticker, {}),
                "range_cents": max(mids) - min(mids),
                "net_T8h_to_T10m_cents": mids[-1] - mids[0],
                "total_absolute_path_cents": sum(abs(b - a) for a, b in zip(mids, mids[1:])),
            })

    return {
        "version": "4.1.0",
        "mode": "descriptive-strikeout-market-efficiency-map",
        "research_only": True,
        "date_range": {"start": start_date, "end": end_date, "days": days},
        "timestamps": labels,
        "filters": {"max_quote_age_minutes": max_age_minutes, "max_spread_cents": max_spread_cents, "lookback_hours": lookback_hours},
        "unique_contracts_with_any_snapshot": len(rows_by_ticker),
        "contracts_with_complete_path": len(full_path),
        "intervals": intervals,
        "continuation_after_3c_move": _continuation(rows_by_ticker),
        "complete_path_summary": {
            "contracts": len(full_path),
            "mean_intraday_range_cents": round(sum(r["range_cents"] for r in full_path) / len(full_path), 3) if full_path else None,
            "median_intraday_range_cents": round(_median([r["range_cents"] for r in full_path]), 3) if full_path else None,
            "mean_total_absolute_path_cents": round(sum(r["total_absolute_path_cents"] for r in full_path) / len(full_path), 3) if full_path else None,
            "mean_net_T8h_to_T10m_cents": round(sum(r["net_T8h_to_T10m_cents"] for r in full_path) / len(full_path), 3) if full_path else None,
        },
        "coverage": dict(coverage),
        "warnings": warnings,
        "notes": [
            "This is a descriptive map, not a betting strategy or promotion test.",
            "No game outcomes are used.",
            "Midpoint movement measures price discovery; spread summaries measure execution friction.",
            "The next strategy, if any, should be chosen only after reviewing where movement and continuation/reversal actually occur.",
        ],
    }
