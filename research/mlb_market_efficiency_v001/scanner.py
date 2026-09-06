from __future__ import annotations

import argparse
import asyncio
import csv
import json
import math
import os
import random
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx

BASE = os.getenv("KALSHI_HISTORICAL_BASE_URL", "https://external-api.kalshi.com/trade-api/v2").rstrip("/")
OFFSETS_HOURS = (4.0, 2.0, 1.0)
MAX_AGE_MINUTES = 20.0
MAX_SPREAD_CENTS = 12
FEE_RATE = 0.07


def parse_iso(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def cents(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        x = float(value)
    except (TypeError, ValueError):
        return None
    if 0 <= x <= 1:
        return int(round(100 * x))
    if 1 < x <= 100:
        return int(round(x))
    return None


def close_value(obj: Any) -> Any:
    if isinstance(obj, dict):
        return obj.get("close")
    return None


def outcome_yes(market: dict) -> int | None:
    result = str(market.get("result") or market.get("settlement_result") or "").strip().lower()
    if result in {"yes", "y", "true", "1"}:
        return 1
    if result in {"no", "n", "false", "0"}:
        return 0
    settlement = market.get("settlement_value")
    if settlement is None:
        settlement = market.get("settlement_value_dollars")
    if settlement is not None:
        try:
            x = float(settlement)
            if x in {0.0, 1.0}:
                return int(x)
        except (TypeError, ValueError):
            pass
    return None


def fee_cents(contracts: int, price_cents: int, rate: float = FEE_RATE) -> int:
    if contracts <= 0 or not (0 < price_cents < 100):
        return 0
    p = price_cents / 100.0
    return math.ceil(100 * rate * contracts * p * (1 - p) - 1e-12)


def settle_unit(entry_cents: int, won: bool, unit_size: float = 1.0) -> dict | None:
    budget = int(round(unit_size * 100))
    contracts = budget // max(1, entry_cents)
    while contracts > 0 and contracts * entry_cents + fee_cents(contracts, entry_cents) > budget:
        contracts -= 1
    if contracts < 1:
        return None
    fee = fee_cents(contracts, entry_cents)
    cost = contracts * entry_cents + fee
    payout = contracts * 100 if won else 0
    pnl = payout - cost
    return {
        "contracts": contracts,
        "capital_used": round(cost / 100, 2),
        "entry_fee": round(fee / 100, 2),
        "profit_loss": round(pnl / 100, 2),
        "roi": round(pnl / cost, 6) if cost else None,
    }


def price_bucket(price: float | int | None) -> str:
    if price is None:
        return "missing"
    x = float(price)
    if x < 10: return "01-09"
    if x < 20: return "10-19"
    if x < 40: return "20-39"
    if x <= 60: return "40-60"
    if x <= 80: return "61-80"
    if x <= 90: return "81-90"
    return "91-99"


def family(series: dict) -> str:
    text = " ".join([
        str(series.get("ticker") or ""), str(series.get("title") or ""),
        " ".join(series.get("tags") or [])
    ]).lower()
    if "strikeout" in text or "pitcher k" in text: return "strikeouts"
    if "home run" in text or "homer" in text: return "home_runs"
    if "total bases" in text: return "total_bases"
    if "hits allowed" in text: return "hits_allowed"
    if "hits" in text or "hit " in text: return "hits"
    if "rbi" in text: return "rbis"
    if "runs scored" in text: return "runs_scored"
    if "pitching outs" in text or "outs recorded" in text: return "pitching_outs"
    if "first inning" in text or "1st inning" in text: return "first_inning"
    if "run line" in text or "spread" in text: return "run_line"
    if "total runs" in text or "game total" in text: return "game_total"
    if "winner" in text or "moneyline" in text or "win game" in text: return "moneyline"
    return "other"


def is_mlb_series(series: dict) -> bool:
    ticker = str(series.get("ticker") or "").upper()
    title = str(series.get("title") or "").lower()
    tags = " ".join(str(x) for x in (series.get("tags") or [])).lower()
    blob = f"{title} {tags}"
    return ticker.startswith("KXMLB") or "major league baseball" in blob or " mlb" in f" {blob}"


async def get_json(client: httpx.AsyncClient, url: str, params: dict | None = None) -> dict:
    for attempt in range(7):
        r = await client.get(url, params=params, timeout=60)
        if r.status_code != 429:
            if r.is_error:
                raise RuntimeError(f"HTTP {r.status_code} {r.url}: {r.text[:300]}")
            return r.json()
        await asyncio.sleep(0.5 * (2 ** attempt) + random.random() * 0.2)
    raise RuntimeError(f"429 retries exhausted for {url}")


async def discover_series(client: httpx.AsyncClient) -> list[dict]:
    payload = await get_json(client, BASE + "/series", params={"category": "Sports", "include_volume": "true"})
    rows = [s for s in payload.get("series", []) if is_mlb_series(s)]
    rows.sort(key=lambda s: str(s.get("ticker") or ""))
    return rows


async def list_markets_for_series(client: httpx.AsyncClient, series_ticker: str, historical: bool) -> list[dict]:
    path = "/historical/markets" if historical else "/markets"
    out: list[dict] = []
    cursor = None
    while True:
        params = {"series_ticker": series_ticker, "limit": 1000}
        if not historical:
            params["status"] = "settled"
            params["mve_filter"] = "exclude"
        if cursor:
            params["cursor"] = cursor
        payload = await get_json(client, BASE + path, params=params)
        out.extend(payload.get("markets", []))
        cursor = payload.get("cursor") or None
        if not cursor:
            return out


def market_anchor(market: dict) -> datetime | None:
    for key in ("close_time", "expected_expiration_time", "expiration_time", "settlement_ts"):
        dt = parse_iso(market.get(key))
        if dt:
            return dt
    return None


async def candles(client: httpx.AsyncClient, series_ticker: str, ticker: str, historical: bool, start_ts: int, end_ts: int) -> list[dict]:
    if historical:
        url = f"{BASE}/historical/markets/{ticker}/candlesticks"
    else:
        url = f"{BASE}/series/{series_ticker}/markets/{ticker}/candlesticks"
    payload = await get_json(client, url, params={"start_ts": start_ts, "end_ts": end_ts, "period_interval": 1})
    return payload.get("candlesticks", []) or []


def snapshot(candles_: list[dict], target: datetime) -> dict | None:
    target_ts = int(target.timestamp())
    eligible = [c for c in candles_ if isinstance(c.get("end_period_ts"), (int, float)) and int(c["end_period_ts"]) <= target_ts]
    if not eligible:
        return None
    c = max(eligible, key=lambda x: int(x["end_period_ts"]))
    ts = int(c["end_period_ts"])
    age = (target_ts - ts) / 60.0
    bid = cents(close_value(c.get("yes_bid")))
    ask = cents(close_value(c.get("yes_ask")))
    if bid is None or ask is None or age < 0 or age > MAX_AGE_MINUTES or ask < bid or ask - bid > MAX_SPREAD_CENTS:
        return None
    return {"yes_bid": bid, "yes_ask": ask, "no_ask": 100 - bid, "mid": (bid + ask) / 2, "spread": ask - bid, "age": round(age, 2)}


async def build_inventory(out_dir: Path):
    out_dir.mkdir(parents=True, exist_ok=True)
    async with httpx.AsyncClient(headers={"User-Agent": "KalshiTradingPlatform/MLB-efficiency-v0.01"}) as client:
        series = await discover_series(client)
    rows = []
    for s in series:
        rows.append({
            "series_ticker": s.get("ticker"), "series_title": s.get("title"),
            "family": family(s), "tags": "|".join(s.get("tags") or []),
            "volume": s.get("volume_fp") or s.get("volume") or "",
        })
    with (out_dir / "mlb_series_inventory.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["series_ticker","series_title","family","tags","volume"])
        w.writeheader(); w.writerows(rows)
    (out_dir / "INVENTORY_SUMMARY.json").write_text(json.dumps({"mlb_series": len(rows), "families": dict(sorted(defaultdict(int, {k: sum(1 for r in rows if r['family']==k) for k in set(r['family'] for r in rows)}).items()))}, indent=2), encoding="utf-8")
    print(f"Discovered {len(rows)} MLB series")


async def scan_shard(inventory_path: Path, out_dir: Path, start_date: str, end_date: str, shard_index: int, shard_count: int):
    out_dir.mkdir(parents=True, exist_ok=True)
    with inventory_path.open(encoding="utf-8") as f:
        inventory = list(csv.DictReader(f))
    selected = [r for i, r in enumerate(inventory) if i % shard_count == shard_index]
    start = datetime.strptime(start_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    end = (datetime.strptime(end_date, "%Y-%m-%d") + timedelta(days=1)).replace(tzinfo=timezone.utc)
    ledger = []
    diagnostics = defaultdict(int)
    semaphore = asyncio.Semaphore(5)

    async with httpx.AsyncClient(headers={"User-Agent": "KalshiTradingPlatform/MLB-efficiency-v0.01"}) as client:
        for si, s in enumerate(selected, start=1):
            st = s["series_ticker"]
            print(f"[{si}/{len(selected)}] {st} {s['family']}", flush=True)
            tagged: dict[str, tuple[dict,bool]] = {}
            for historical in (True, False):
                try:
                    markets = await list_markets_for_series(client, st, historical)
                except Exception as exc:
                    diagnostics[f"market_list_error_{'hist' if historical else 'recent'}"] += 1
                    print(f"warning {st}: {exc}", flush=True)
                    continue
                for m in markets:
                    ticker = str(m.get("ticker") or "")
                    anchor = market_anchor(m)
                    if ticker and anchor and start <= anchor < end:
                        tagged[ticker] = (m, historical)
            diagnostics["markets_in_window"] += len(tagged)

            async def one(ticker: str, market: dict, historical: bool):
                yes = outcome_yes(market)
                anchor = market_anchor(market)
                if yes is None or anchor is None:
                    diagnostics["missing_outcome_or_anchor"] += 1; return
                window_start = anchor - timedelta(hours=max(OFFSETS_HOURS) + 1)
                window_end = anchor - timedelta(hours=min(OFFSETS_HOURS))
                try:
                    async with semaphore:
                        cs = await candles(client, st, ticker, historical, int(window_start.timestamp()), int(window_end.timestamp()))
                except Exception:
                    diagnostics["candle_error"] += 1; return
                row = {
                    "series_ticker": st, "series_title": s["series_title"], "family": s["family"],
                    "ticker": ticker, "event_ticker": market.get("event_ticker"), "market_title": market.get("title"),
                    "anchor_time": anchor.isoformat(), "yes_outcome": yes, "tier": "historical" if historical else "recent",
                }
                any_snap = False
                for off in OFFSETS_HOURS:
                    label = f"T{int(off)}h"
                    snap = snapshot(cs, anchor - timedelta(hours=off))
                    if not snap:
                        continue
                    any_snap = True
                    row.update({
                        f"{label}_yes_bid": snap["yes_bid"], f"{label}_yes_ask": snap["yes_ask"],
                        f"{label}_no_ask": snap["no_ask"], f"{label}_mid": snap["mid"],
                        f"{label}_spread": snap["spread"], f"{label}_age": snap["age"],
                    })
                    for side in ("YES", "NO"):
                        entry = snap["yes_ask"] if side == "YES" else snap["no_ask"]
                        won = bool(yes) if side == "YES" else not bool(yes)
                        ex = settle_unit(entry, won)
                        if ex:
                            row[f"{label}_{side}_entry"] = entry
                            row[f"{label}_{side}_bucket"] = price_bucket(entry)
                            row[f"{label}_{side}_pnl"] = ex["profit_loss"]
                            row[f"{label}_{side}_capital"] = ex["capital_used"]
                            row[f"{label}_{side}_roi"] = ex["roi"]
                if any_snap:
                    ledger.append(row); diagnostics["usable_markets"] += 1
                else:
                    diagnostics["no_usable_snapshot"] += 1

            batch = list(tagged.items())
            for j in range(0, len(batch), 20):
                await asyncio.gather(*(one(t, m, h) for t, (m, h) in batch[j:j+20]))
                await asyncio.sleep(0.2)

    keys = sorted({k for r in ledger for k in r.keys()})
    with (out_dir / "market_ledger.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader(); w.writerows(ledger)
    (out_dir / "diagnostics.json").write_text(json.dumps(dict(diagnostics), indent=2), encoding="utf-8")
    (out_dir / "SHARD_DONE.txt").write_text(f"series={len(selected)} markets={len(ledger)}\n", encoding="utf-8")
    print(json.dumps({"series": len(selected), "usable_markets": len(ledger), "diagnostics": dict(diagnostics)}, indent=2))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["inventory","scan"], required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--inventory")
    ap.add_argument("--start", default="2026-07-01")
    ap.add_argument("--end", default="2026-08-31")
    ap.add_argument("--shard-index", type=int, default=0)
    ap.add_argument("--shard-count", type=int, default=1)
    args = ap.parse_args()
    if args.mode == "inventory":
        asyncio.run(build_inventory(Path(args.out)))
    else:
        if not args.inventory:
            raise SystemExit("--inventory required for scan")
        asyncio.run(scan_shard(Path(args.inventory), Path(args.out), args.start, args.end, args.shard_index, args.shard_count))

if __name__ == "__main__":
    main()
