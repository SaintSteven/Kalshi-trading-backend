from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from config import KALSHI_HISTORICAL_BASE_URL, MLB_STRIKEOUT_PREFIX

HIST_BASE = KALSHI_HISTORICAL_BASE_URL.rstrip("/")
ET = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")


def _to_cents(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if 0 <= number <= 1:
        return int(round(number * 100))
    if 1 < number <= 100:
        return int(round(number))
    return None


def _date_token(target_date: str) -> str:
    dt = datetime.strptime(target_date, "%Y-%m-%d")
    return dt.strftime("%y%b%d").upper()


def _game_start_from_ticker(ticker: str) -> datetime | None:
    prefix = f"{MLB_STRIKEOUT_PREFIX}-"
    if not ticker.startswith(prefix):
        return None
    rest = ticker[len(prefix):]
    if len(rest) < 11:
        return None
    token = rest[:7]
    hhmm = rest[7:11]
    try:
        return datetime.strptime(token + hhmm, "%y%b%d%H%M").replace(tzinfo=ET)
    except ValueError:
        return None


def _player_and_threshold(market: dict) -> tuple[str | None, str | None]:
    title = str(market.get("title") or "").strip()
    player = title.split(":", 1)[0].strip() if title else None
    threshold = None
    if "+" in title:
        import re
        match = re.search(r"(\d+)\+", title)
        if match:
            threshold = f"{match.group(1)}+"
    return player, threshold


def _parse_iso(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _target_route(target_date: str, market_settled_cutoff: datetime | None) -> str:
    """Choose the Kalshi storage tier without peeking at market outcomes.

    Kalshi routes based on market settlement time. MLB strikeout markets normally
    settle shortly after the game, so use noon ET on the following day as a
    conservative proxy. If the requested date sits within 36 hours of the cutoff,
    return 'both' because a slate can straddle the archive boundary.
    """
    if market_settled_cutoff is None:
        return "recent"
    game_day = datetime.strptime(target_date, "%Y-%m-%d").replace(tzinfo=ET)
    settlement_proxy = (game_day + timedelta(days=1, hours=12)).astimezone(UTC)
    delta = (settlement_proxy - market_settled_cutoff).total_seconds()
    if abs(delta) <= 36 * 3600:
        return "both"
    return "historical" if delta < 0 else "recent"


async def _request_json(client: httpx.AsyncClient, url: str, *, params: dict | None = None) -> dict:
    response = await client.get(url, params=params, timeout=45)
    if response.is_error:
        detail = ""
        try:
            payload = response.json()
            detail = str(payload.get("message") or payload.get("details") or payload)
        except Exception:
            detail = response.text[:500]
        raise RuntimeError(f"HTTP {response.status_code} for {response.url}: {detail}".strip())
    return response.json()


async def _get_cutoff(client: httpx.AsyncClient) -> dict:
    return await _request_json(client, HIST_BASE + "/historical/cutoff")


async def _collect_series_markets(client: httpx.AsyncClient, historical: bool) -> list[dict]:
    """Collect KXMLBKS markets from the correct storage tier.

    Historical filters are documented as mutually exclusive, so unlike the old
    POC we do NOT combine series_ticker with mve_filter on /historical/markets.
    """
    path = "/historical/markets" if historical else "/markets"
    markets: list[dict] = []
    cursor: str | None = None
    while True:
        if historical:
            params: dict[str, Any] = {
                "series_ticker": MLB_STRIKEOUT_PREFIX,
                "limit": 1000,
            }
        else:
            params = {
                "series_ticker": MLB_STRIKEOUT_PREFIX,
                "status": "settled",
                "limit": 1000,
                "mve_filter": "exclude",
            }
        if cursor:
            params["cursor"] = cursor
        payload = await _request_json(client, HIST_BASE + path, params=params)
        markets.extend(payload.get("markets", []))
        cursor = payload.get("cursor") or None
        if not cursor:
            return markets


async def _candles(
    client: httpx.AsyncClient,
    ticker: str,
    *,
    historical: bool,
    start_ts: int,
    end_ts: int,
) -> list[dict]:
    if historical:
        url = f"{HIST_BASE}/historical/markets/{ticker}/candlesticks"
    else:
        url = f"{HIST_BASE}/series/{MLB_STRIKEOUT_PREFIX}/markets/{ticker}/candlesticks"
    payload = await _request_json(
        client,
        url,
        params={"start_ts": start_ts, "end_ts": end_ts, "period_interval": 1},
    )
    return payload.get("candlesticks", [])


def _last_candle_at_or_before(candles: list[dict], target_ts: int) -> dict | None:
    eligible = [
        c for c in candles
        if isinstance(c.get("end_period_ts"), (int, float)) and int(c["end_period_ts"]) <= target_ts
    ]
    return max(eligible, key=lambda c: int(c["end_period_ts"])) if eligible else None


def _close(block: Any) -> Any:
    return block.get("close") if isinstance(block, dict) else None


async def historical_price_poc(
    target_date: str,
    *,
    hours_before_first_pitch: float = 2.0,
    max_markets: int = 12,
) -> dict:
    """Reconstruct executable pregame Kalshi K prices without look-ahead.

    Routing convention:
      1. Read Kalshi's /historical/cutoff.
      2. Choose recent or historical storage for the requested slate.
      3. If the slate is close to the cutoff boundary, query both tiers.

    Entry convention: use the final 1-minute quoted candle ending at or before
    `hours_before_first_pitch`. YES entry uses YES ask close. NO entry is derived
    from YES bid close (NO ask = 100 - YES bid). A six-hour lookback is used so
    sparse markets can still return the most recent pre-entry quote.
    """
    if not 0.25 <= hours_before_first_pitch <= 24:
        raise ValueError("hours_before_first_pitch must be between 0.25 and 24.")
    if not 1 <= max_markets <= 50:
        raise ValueError("max_markets must be between 1 and 50.")

    token = _date_token(target_date)
    warnings: list[str] = []
    headers = {"User-Agent": "KalshiTradingPlatform/2.6.3-historical-poc"}

    async with httpx.AsyncClient(headers=headers) as client:
        cutoff_payload: dict = {}
        market_cutoff: datetime | None = None
        try:
            cutoff_payload = await _get_cutoff(client)
            market_cutoff = _parse_iso(cutoff_payload.get("market_settled_ts"))
        except Exception as exc:
            warnings.append(f"Historical cutoff lookup failed; defaulting to recent tier first: {exc}")

        route = _target_route(target_date, market_cutoff)
        tiers = [route] if route in {"recent", "historical"} else ["recent", "historical"]
        if market_cutoff is None and "historical" not in tiers:
            tiers.append("historical")  # fallback only when cutoff could not be read

        listings: list[tuple[dict, bool]] = []
        listing_counts = {"historical": 0, "recent": 0}
        for tier in tiers:
            is_historical = tier == "historical"
            try:
                found = await _collect_series_markets(client, historical=is_historical)
                listing_counts[tier] = len(found)
                listings.extend((m, is_historical) for m in found)
            except Exception as exc:
                warnings.append(f"{tier.title()} market listing failed: {exc}")

        # If routing found no markets for a valid-looking game date, try the other
        # tier once. This protects us from a market settling just across the cutoff.
        def date_matches(items: list[tuple[dict, bool]]) -> list[tuple[dict, bool]]:
            out: list[tuple[dict, bool]] = []
            seen: set[str] = set()
            for market, historical in items:
                ticker = str(market.get("ticker") or "")
                if ticker.startswith(f"{MLB_STRIKEOUT_PREFIX}-{token}") and ticker not in seen:
                    seen.add(ticker)
                    out.append((market, historical))
            return out

        tagged = date_matches(listings)
        if not tagged and market_cutoff is not None and route != "both":
            fallback_tier = "historical" if route == "recent" else "recent"
            try:
                fallback = await _collect_series_markets(client, historical=fallback_tier == "historical")
                listing_counts[fallback_tier] = len(fallback)
                listings.extend((m, fallback_tier == "historical") for m in fallback)
                tagged = date_matches(listings)
                if tagged:
                    warnings.append(f"No date matches in routed {route} tier; fallback {fallback_tier} tier returned matches.")
            except Exception as exc:
                warnings.append(f"Fallback {fallback_tier} market listing failed: {exc}")

        tagged.sort(key=lambda pair: str(pair[0].get("ticker") or ""))
        tagged = tagged[:max_markets]
        semaphore = asyncio.Semaphore(4)

        async def inspect(pair: tuple[dict, bool]) -> dict:
            market, historical = pair
            ticker = str(market.get("ticker") or "")
            game_start = _game_start_from_ticker(ticker)
            player, threshold = _player_and_threshold(market)
            if not game_start:
                return {
                    "ticker": ticker,
                    "source": "historical" if historical else "live_recent",
                    "player": player,
                    "threshold": threshold,
                    "error": "Could not parse game start time from ticker.",
                }

            target = game_start - timedelta(hours=hours_before_first_pitch)
            target_ts = int(target.astimezone(UTC).timestamp())
            start_ts = int((target - timedelta(hours=6)).astimezone(UTC).timestamp())
            try:
                async with semaphore:
                    candles = await _candles(
                        client,
                        ticker,
                        historical=historical,
                        start_ts=start_ts,
                        end_ts=target_ts,
                    )
                candle = _last_candle_at_or_before(candles, target_ts)
                if not candle:
                    return {
                        "ticker": ticker,
                        "source": "historical" if historical else "live_recent",
                        "player": player,
                        "threshold": threshold,
                        "game_start_et": game_start.isoformat(),
                        "entry_target_et": target.isoformat(),
                        "error": "No quoted 1-minute candle found in the six hours before target entry.",
                    }

                yes_ask = _to_cents(_close(candle.get("yes_ask")))
                yes_bid = _to_cents(_close(candle.get("yes_bid")))
                no_ask = 100 - yes_bid if yes_bid is not None else None
                last_trade = _to_cents(_close(candle.get("price")))
                candle_end = datetime.fromtimestamp(int(candle["end_period_ts"]), tz=UTC).astimezone(ET)
                quote_age_minutes = max(0.0, (target - candle_end).total_seconds() / 60.0)
                return {
                    "ticker": ticker,
                    "source": "historical" if historical else "live_recent",
                    "player": player,
                    "threshold": threshold,
                    "game_start_et": game_start.isoformat(),
                    "entry_target_et": target.isoformat(),
                    "quote_time_et": candle_end.isoformat(),
                    "quote_age_minutes": round(quote_age_minutes, 1),
                    "yes_bid_cents": yes_bid,
                    "yes_ask_cents": yes_ask,
                    "no_ask_cents": no_ask,
                    "last_trade_cents": last_trade,
                    "volume": candle.get("volume"),
                    "open_interest": candle.get("open_interest"),
                    "usable_entry_quote": yes_ask is not None and no_ask is not None,
                }
            except Exception as exc:
                return {
                    "ticker": ticker,
                    "source": "historical" if historical else "live_recent",
                    "player": player,
                    "threshold": threshold,
                    "game_start_et": game_start.isoformat(),
                    "entry_target_et": target.isoformat(),
                    "error": str(exc),
                }

        rows = await asyncio.gather(*(inspect(pair) for pair in tagged)) if tagged else []

    usable = [r for r in rows if r.get("usable_entry_quote")]
    cutoff_text = market_cutoff.isoformat() if market_cutoff else None
    return {
        "status": "success" if usable else "no_usable_quotes",
        "target_date": target_date,
        "series_ticker": MLB_STRIKEOUT_PREFIX,
        "storage_route": route,
        "market_settled_cutoff": cutoff_text,
        "entry_rule": f"last quoted 1-minute candle at or before {hours_before_first_pitch:g} hours before first pitch; 6-hour quote lookback",
        "market_records_found": len(tagged),
        "markets_checked": len(rows),
        "usable_entry_quotes": len(usable),
        "historical_listing_records": listing_counts["historical"],
        "live_recent_listing_records": listing_counts["recent"],
        "proof_passed": bool(usable),
        "markets": rows,
        "warnings": warnings,
        "next_step": (
            "Historical Kalshi price reconstruction is viable. Build the full leakage-safe trading backtester next."
            if usable
            else "No usable quote was reconstructed. Try Jul 10, 2026 first; if markets are found but quotes are missing, inspect the per-market candle errors."
        ),
    }
