from __future__ import annotations

import argparse
import asyncio
import csv
import json
import math
import os
import random
import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx

BASE = os.getenv("KALSHI_HISTORICAL_BASE_URL", "https://external-api.kalshi.com/trade-api/v2").rstrip("/")
ET = ZoneInfo("America/New_York")
UTC = timezone.utc
OFFSETS_HOURS = (4.0, 2.0, 1.0)
MAX_AGE_MINUTES = 20.0
MAX_SPREAD_CENTS = 12
FEE_RATE = 0.07

# Explicit daily-MLB research universe. Futures/awards/season-long markets are excluded.
DAILY_SERIES = {
    "KXMLBGAME": "moneyline",
    "KXMLBTOTAL": "game_total",
    "KXMLBSPREAD": "run_line",
    "KXMLBRFI": "first_inning",
    "KXMLBF5TOTAL": "first5_total",
    "KXMLBF5": "first5_moneyline",
    "KXMLBF5SPREAD": "first5_spread",
    "KXMLBKS": "strikeouts",
    "KXMLBTEAMTOTAL": "team_total",
    "KXMLBHIT": "hits",
    "KXMLBHR": "home_runs",
    "KXMLBHRR": "hits_runs_rbis",
    "KXMLBTB": "total_bases",
    "KXMLBOUTS": "pitching_outs",
    "KXMLBRBI": "rbis",
    "KXMLBHA": "hits_allowed",
    "KXMLBF3": "first3_moneyline",
    "KXMLBF7": "first7_moneyline",
    "KXMLBEXTRAS": "extra_innings",
}


def parse_iso(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def game_start_from_ticker(series: str, ticker: str) -> datetime | None:
    # Daily MLB series use SERIES-YYMONDDHHMM... where HHMM is local ET first pitch.
    m = re.match(rf"^{re.escape(series)}-(\d{{2}}[A-Z]{{3}}\d{{2}})(\d{{4}})", ticker.upper())
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1) + m.group(2), "%y%b%d%H%M").replace(tzinfo=ET)
    except ValueError:
        return None


def date_token(day: str) -> str:
    return datetime.strptime(day, "%Y-%m-%d").strftime("%y%b%d").upper()


def block_close_cents(block: Any) -> int | None:
    if not isinstance(block, dict):
        return None
    # Explicitly distinguish fixed-point dollar fields from legacy cent fields.
    for key in ("close_dollars", "close_fp"):
        value = block.get(key)
        if value not in (None, ""):
            try:
                x = float(value)
            except (TypeError, ValueError):
                return None
            if 0 <= x <= 1:
                return int(round(x * 100))
            return None
    value = block.get("close")
    if value in (None, ""):
        return None
    try:
        x = float(value)
    except (TypeError, ValueError):
        return None
    return int(round(x)) if 0 <= x <= 100 else None


def outcome_yes(market: dict) -> int | None:
    result = str(market.get("result") or market.get("settlement_result") or "").strip().lower()
    if result in {"yes", "y", "true"}:
        return 1
    if result in {"no", "n", "false"}:
        return 0
    # Settlement values are explicit dollars when *_dollars is used, otherwise cents.
    if market.get("settlement_value_dollars") not in (None, ""):
        try:
            x = float(market["settlement_value_dollars"])
            if x in {0.0, 1.0}:
                return int(x)
        except (TypeError, ValueError):
            pass
    if market.get("settlement_value") not in (None, ""):
        try:
            x = float(market["settlement_value"])
            if x in {0.0, 100.0}:
                return 1 if x == 100 else 0
            if x in {0.0, 1.0}:
                return int(x)
        except (TypeError, ValueError):
            pass
    return None


def fee_cents(contracts: int, price_cents: int) -> int:
    if contracts <= 0 or not (0 < price_cents < 100):
        return 0
    p = price_cents / 100.0
    return math.ceil(100 * FEE_RATE * contracts * p * (1 - p) - 1e-12)


def settle_unit(entry_cents: int, won: bool) -> dict | None:
    contracts = 100 // max(1, entry_cents)
    while contracts > 0 and contracts * entry_cents + fee_cents(contracts, entry_cents) > 100:
        contracts -= 1
    if contracts < 1:
        return None
    fee = fee_cents(contracts, entry_cents)
    cost = contracts * entry_cents + fee
    pnl = (contracts * 100 if won else 0) - cost
    return {"contracts": contracts, "capital": round(cost / 100, 2), "pnl": round(pnl / 100, 2), "roi": round(pnl / cost, 6)}


async def get_json(client: httpx.AsyncClient, url: str, params: dict | None = None) -> dict:
    for attempt in range(8):
        r = await client.get(url, params=params, timeout=60)
        if r.status_code != 429:
            if r.is_error:
                raise RuntimeError(f"HTTP {r.status_code} {r.url}: {r.text[:300]}")
            return r.json()
        await asyncio.sleep(0.5 * (2 ** attempt) + random.random() * 0.2)
    raise RuntimeError(f"429 retries exhausted for {url}")


async def list_series_markets(client: httpx.AsyncClient, series: str, historical: bool) -> list[dict]:
    path = "/historical/markets" if historical else "/markets"
    out, cursor = [], None
    while True:
        params: dict[str, Any] = {"series_ticker": series, "limit": 1000}
        if not historical:
            params.update({"status": "settled", "mve_filter": "exclude"})
        if cursor:
            params["cursor"] = cursor
        payload = await get_json(client, BASE + path, params)
        out.extend(payload.get("markets", []))
        cursor = payload.get("cursor") or None
        if not cursor:
            return out


async def single_candles(client: httpx.AsyncClient, series: str, ticker: str, historical: bool, start_ts: int, end_ts: int) -> list[dict]:
    url = (f"{BASE}/historical/markets/{ticker}/candlesticks" if historical else f"{BASE}/series/{series}/markets/{ticker}/candlesticks")
    payload = await get_json(client, url, {"start_ts": start_ts, "end_ts": end_ts, "period_interval": 1})
    return payload.get("candlesticks", []) or []


async def batch_recent_candles(client: httpx.AsyncClient, tickers: list[str], start_ts: int, end_ts: int) -> dict[str, list[dict]]:
    payload = await get_json(client, BASE + "/markets/candlesticks", {
        "market_tickers": ",".join(tickers), "start_ts": start_ts, "end_ts": end_ts,
        "period_interval": 1, "include_latest_before_start": "false",
    })
    out = {}
    for item in payload.get("markets", []):
        t = str(item.get("market_ticker") or item.get("ticker") or "")
        if t:
            out[t] = item.get("candlesticks", []) or []
    return out


def snapshot(candles_: list[dict], target: datetime) -> dict | None:
    target_ts = int(target.astimezone(UTC).timestamp())
    eligible = [c for c in candles_ if isinstance(c.get("end_period_ts"), (int, float)) and int(c["end_period_ts"]) <= target_ts]
    if not eligible:
        return None
    c = max(eligible, key=lambda x: int(x["end_period_ts"]))
    age = (target_ts - int(c["end_period_ts"])) / 60.0
    bid = block_close_cents(c.get("yes_bid"))
    ask = block_close_cents(c.get("yes_ask"))
    if bid is None or ask is None or age < 0 or age > MAX_AGE_MINUTES or ask < bid or ask - bid > MAX_SPREAD_CENTS:
        return None
    return {"yes_bid": bid, "yes_ask": ask, "no_ask": 100 - bid, "mid": (bid + ask) / 2, "spread": ask - bid, "age": round(age, 2)}


def safe_recent_chunks(tickers: list[str], specs: dict[str, tuple], max_candles: int = 9000, max_markets: int = 90):
    ordered = sorted(tickers, key=lambda t: specs[t][3])
    chunk = []
    for ticker in ordered:
        candidate = chunk + [ticker]
        starts = [specs[t][2] for t in candidate]
        ends = [specs[t][3] for t in candidate]
        minutes = max(1, (max(ends) - min(starts) + 59) // 60)
        if chunk and (len(candidate) > max_markets or minutes * len(candidate) > max_candles):
            yield chunk
            chunk = [ticker]
        else:
            chunk = candidate
    if chunk:
        yield chunk


async def scan(series: str, start_date: str, end_date: str, out_dir: Path):
    out_dir.mkdir(parents=True, exist_ok=True)
    if series not in DAILY_SERIES:
        raise SystemExit(f"Unsupported series: {series}")
    start = datetime.strptime(start_date, "%Y-%m-%d").date()
    end = datetime.strptime(end_date, "%Y-%m-%d").date()
    wanted_tokens = {date_token((start + timedelta(days=i)).isoformat()) for i in range((end - start).days + 1)}
    diagnostics = defaultdict(int)
    warnings = []

    async with httpx.AsyncClient(headers={"User-Agent": "KalshiTradingPlatform/MLB-efficiency-v0.02"}) as client:
        tagged: dict[str, tuple[dict, bool]] = {}
        for historical in (True, False):
            try:
                markets = await list_series_markets(client, series, historical)
                diagnostics[f"listed_{'historical' if historical else 'recent'}"] = len(markets)
            except Exception as exc:
                warnings.append(f"listing {'historical' if historical else 'recent'} failed: {exc}")
                continue
            for m in markets:
                ticker = str(m.get("ticker") or "")
                gs = game_start_from_ticker(series, ticker)
                if not gs or gs.strftime("%y%b%d").upper() not in wanted_tokens:
                    continue
                # Recent tier wins on duplicate, because it is the active storage tier near cutoff.
                if ticker not in tagged or not historical:
                    tagged[ticker] = (m, historical)

        diagnostics["markets_in_window"] = len(tagged)
        date_counts = defaultdict(int)
        specs: dict[str, tuple[dict, bool, int, int, datetime]] = {}
        for ticker, (m, historical) in tagged.items():
            gs = game_start_from_ticker(series, ticker)
            if not gs:
                continue
            date_counts[gs.date().isoformat()] += 1
            specs[ticker] = (m, historical, int((gs - timedelta(hours=5)).astimezone(UTC).timestamp()), int((gs - timedelta(hours=1)).astimezone(UTC).timestamp()), gs)

        candle_map: dict[str, list[dict]] = {}
        recent = [t for t, x in specs.items() if not x[1]]
        archive = [t for t, x in specs.items() if x[1]]
        for chunk in safe_recent_chunks(recent, specs):
            s = min(specs[t][2] for t in chunk); e = max(specs[t][3] for t in chunk)
            try:
                batch = await batch_recent_candles(client, chunk, s, e)
                for t in chunk:
                    candle_map[t] = batch.get(t, [])
            except Exception as exc:
                warnings.append(f"recent batch failed ({len(chunk)}): {exc}")
                for t in chunk:
                    try:
                        candle_map[t] = await single_candles(client, series, t, False, specs[t][2], specs[t][3])
                    except Exception as sub:
                        warnings.append(f"recent candle failed {t}: {sub}")
                        candle_map[t] = []
                    await asyncio.sleep(0.15)
            await asyncio.sleep(0.25)

        for i, t in enumerate(archive, start=1):
            try:
                candle_map[t] = await single_candles(client, series, t, True, specs[t][2], specs[t][3])
            except Exception as exc:
                warnings.append(f"historical candle failed {t}: {exc}")
                candle_map[t] = []
            if i % 25 == 0:
                print(f"{series}: historical candles {i}/{len(archive)}", flush=True)
            await asyncio.sleep(0.18)

    ledger = []
    usable_date_counts = defaultdict(int)
    for ticker, (m, historical, _, _, gs) in specs.items():
        yes = outcome_yes(m)
        if yes is None:
            diagnostics["missing_outcome"] += 1
            continue
        row = {
            "series_ticker": series, "family": DAILY_SERIES[series], "ticker": ticker,
            "event_ticker": m.get("event_ticker"), "market_title": m.get("title"),
            "game_start_et": gs.isoformat(), "game_date": gs.date().isoformat(),
            "source_tier": "historical" if historical else "recent", "yes_outcome": yes,
        }
        any_snap = False
        for off in OFFSETS_HOURS:
            label = f"T{int(off)}h"
            snap = snapshot(candle_map.get(ticker, []), gs - timedelta(hours=off))
            if not snap:
                continue
            any_snap = True
            row.update({f"{label}_yes_bid": snap["yes_bid"], f"{label}_yes_ask": snap["yes_ask"], f"{label}_no_ask": snap["no_ask"], f"{label}_mid": snap["mid"], f"{label}_spread": snap["spread"], f"{label}_age": snap["age"]})
            for side in ("YES", "NO"):
                entry = snap["yes_ask"] if side == "YES" else snap["no_ask"]
                ex = settle_unit(entry, bool(yes) if side == "YES" else not bool(yes))
                if ex:
                    row.update({f"{label}_{side}_entry": entry, f"{label}_{side}_pnl": ex["pnl"], f"{label}_{side}_capital": ex["capital"], f"{label}_{side}_roi": ex["roi"]})
        if any_snap:
            ledger.append(row)
            usable_date_counts[gs.date().isoformat()] += 1
        else:
            diagnostics["no_usable_snapshot"] += 1

    diagnostics["usable_markets"] = len(ledger)
    diagnostics["requested_days"] = (end - start).days + 1
    diagnostics["dates_with_markets"] = len(date_counts)
    diagnostics["dates_with_usable_markets"] = len(usable_date_counts)
    all_days = [(start + timedelta(days=i)).isoformat() for i in range((end - start).days + 1)]
    missing_market_dates = [d for d in all_days if date_counts.get(d, 0) == 0]
    missing_usable_dates = [d for d in all_days if usable_date_counts.get(d, 0) == 0]

    keys = sorted({k for r in ledger for k in r})
    with (out_dir / "market_ledger.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=keys)
        if keys:
            w.writeheader(); w.writerows(ledger)
    report = {
        "version": "0.02", "series": series, "family": DAILY_SERIES[series],
        "date_range": {"start": start_date, "end": end_date},
        "diagnostics": dict(diagnostics), "markets_by_date": dict(sorted(date_counts.items())),
        "usable_by_date": dict(sorted(usable_date_counts.items())),
        "missing_market_dates": missing_market_dates, "missing_usable_dates": missing_usable_dates,
        "warnings": warnings[:200],
    }
    (out_dir / "coverage.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    (out_dir / "SHARD_DONE.txt").write_text(f"series={series} markets={len(tagged)} usable={len(ledger)}\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--series", required=True)
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    asyncio.run(scan(args.series, args.start, args.end, Path(args.out)))

if __name__ == "__main__":
    main()
