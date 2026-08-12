from __future__ import annotations

import asyncio
import random
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
    """Choose the Kalshi storage tier without peeking at market outcomes."""
    if market_settled_cutoff is None:
        return "recent"
    game_day = datetime.strptime(target_date, "%Y-%m-%d").replace(tzinfo=ET)
    settlement_proxy = (game_day + timedelta(days=1, hours=12)).astimezone(UTC)
    delta = (settlement_proxy - market_settled_cutoff).total_seconds()
    if abs(delta) <= 36 * 3600:
        return "both"
    return "historical" if delta < 0 else "recent"


async def _request_json(
    client: httpx.AsyncClient,
    url: str,
    *,
    params: dict | None = None,
    max_429_retries: int = 5,
    base_backoff_seconds: float = 0.40,
) -> dict:
    """GET JSON with Kalshi-aware 429 retry behavior.

    Kalshi currently does not provide Retry-After headers for 429 responses, so
    the documented recovery pattern is exponential backoff. A small jitter avoids
    retry synchronization if Render has multiple requests in flight.
    """
    attempt = 0
    while True:
        response = await client.get(url, params=params, timeout=45)
        if response.status_code != 429:
            break
        if attempt >= max_429_retries:
            break
        delay = base_backoff_seconds * (2 ** attempt) + random.uniform(0.0, 0.15)
        await asyncio.sleep(delay)
        attempt += 1

    if response.is_error:
        detail = ""
        try:
            payload = response.json()
            detail = str(payload.get("message") or payload.get("details") or payload)
        except Exception:
            detail = response.text[:500]
        retry_text = f" after {attempt} backoff retries" if response.status_code == 429 else ""
        raise RuntimeError(
            f"HTTP {response.status_code}{retry_text} for {response.url}: {detail}".strip()
        )
    return response.json()


async def _get_cutoff(client: httpx.AsyncClient) -> dict:
    return await _request_json(client, HIST_BASE + "/historical/cutoff")


async def _collect_series_markets(client: httpx.AsyncClient, historical: bool) -> list[dict]:
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


async def _single_candles(
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
        max_429_retries=6,
        base_backoff_seconds=0.45,
    )
    return payload.get("candlesticks", [])


async def _batch_recent_candles(
    client: httpx.AsyncClient,
    tickers: list[str],
    *,
    start_ts: int,
    end_ts: int,
) -> dict[str, list[dict]]:
    """Fetch recent-tier candles in one Kalshi batch request.

    The official endpoint accepts up to 100 comma-separated market tickers and
    up to 10,000 candles total. The POC only requests <=50 markets and a narrow
    pregame window, so it stays comfortably below that response ceiling.
    """
    if not tickers:
        return {}
    payload = await _request_json(
        client,
        HIST_BASE + "/markets/candlesticks",
        params={
            "market_tickers": ",".join(tickers),
            "start_ts": start_ts,
            "end_ts": end_ts,
            "period_interval": 1,
            "include_latest_before_start": "false",
        },
        max_429_retries=6,
        base_backoff_seconds=0.55,
    )
    out: dict[str, list[dict]] = {}
    for item in payload.get("markets", []):
        ticker = str(item.get("market_ticker") or item.get("ticker") or "")
        if ticker:
            out[ticker] = item.get("candlesticks", []) or []
    return out


def _last_candle_at_or_before(candles: list[dict], target_ts: int) -> dict | None:
    eligible = [
        c for c in candles
        if isinstance(c.get("end_period_ts"), (int, float)) and int(c["end_period_ts"]) <= target_ts
    ]
    return max(eligible, key=lambda c: int(c["end_period_ts"])) if eligible else None


def _close(block: Any) -> Any:
    """Read either legacy cent close or current fixed-point dollar close."""
    if not isinstance(block, dict):
        return None
    for key in ("close", "close_dollars", "close_fp"):
        if block.get(key) not in (None, ""):
            return block.get(key)
    return None


def _finalize_row(
    *,
    market: dict,
    historical: bool,
    candles: list[dict],
    hours_before_first_pitch: float,
    retrieval_method: str,
) -> dict:
    ticker = str(market.get("ticker") or "")
    game_start = _game_start_from_ticker(ticker)
    player, threshold = _player_and_threshold(market)
    source = "historical" if historical else "live_recent"
    if not game_start:
        return {
            "ticker": ticker,
            "source": source,
            "retrieval_method": retrieval_method,
            "player": player,
            "threshold": threshold,
            "candles_retrieved": len(candles),
            "error": "Could not parse game start time from ticker.",
        }

    target = game_start - timedelta(hours=hours_before_first_pitch)
    target_ts = int(target.astimezone(UTC).timestamp())
    candle = _last_candle_at_or_before(candles, target_ts)
    if not candle:
        return {
            "ticker": ticker,
            "source": source,
            "retrieval_method": retrieval_method,
            "player": player,
            "threshold": threshold,
            "game_start_et": game_start.isoformat(),
            "entry_target_et": target.isoformat(),
            "candles_retrieved": len(candles),
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
        "source": source,
        "retrieval_method": retrieval_method,
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
        "volume": candle.get("volume") or candle.get("volume_fp"),
        "open_interest": candle.get("open_interest") or candle.get("open_interest_fp"),
        "candles_retrieved": len(candles),
        "usable_entry_quote": yes_ask is not None and no_ask is not None,
    }


async def historical_price_poc(
    target_date: str,
    *,
    hours_before_first_pitch: float = 2.0,
    max_markets: int = 12,
) -> dict:
    """Reconstruct executable pregame Kalshi K prices without look-ahead.

    v2.6.4 retrieval policy:
      * route recent vs historical using Kalshi's archive cutoff;
      * recent-tier markets use Kalshi's batch candlestick endpoint;
      * historical-tier markets use throttled individual historical requests;
      * all requests use exponential backoff on HTTP 429;
      * price parsing supports Kalshi's current fixed-point `*_dollars` fields.
    """
    if not 0.25 <= hours_before_first_pitch <= 24:
        raise ValueError("hours_before_first_pitch must be between 0.25 and 24.")
    if not 1 <= max_markets <= 50:
        raise ValueError("max_markets must be between 1 and 50.")

    token = _date_token(target_date)
    warnings: list[str] = []
    headers = {"User-Agent": "KalshiTradingPlatform/2.6.4-historical-poc"}

    async with httpx.AsyncClient(headers=headers) as client:
        market_cutoff: datetime | None = None
        try:
            cutoff_payload = await _get_cutoff(client)
            market_cutoff = _parse_iso(cutoff_payload.get("market_settled_ts"))
        except Exception as exc:
            warnings.append(f"Historical cutoff lookup failed; defaulting to recent tier first: {exc}")

        route = _target_route(target_date, market_cutoff)
        tiers = [route] if route in {"recent", "historical"} else ["recent", "historical"]
        if market_cutoff is None and "historical" not in tiers:
            tiers.append("historical")

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
                    warnings.append(
                        f"No date matches in routed {route} tier; fallback {fallback_tier} tier returned matches."
                    )
            except Exception as exc:
                warnings.append(f"Fallback {fallback_tier} market listing failed: {exc}")

        tagged.sort(key=lambda pair: str(pair[0].get("ticker") or ""))
        tagged = tagged[:max_markets]

        # Prepare each market's own no-lookahead target window.
        specs: dict[str, tuple[dict, bool, int, int]] = {}
        for market, historical in tagged:
            ticker = str(market.get("ticker") or "")
            game_start = _game_start_from_ticker(ticker)
            if not game_start:
                specs[ticker] = (market, historical, 0, 0)
                continue
            target = game_start - timedelta(hours=hours_before_first_pitch)
            target_ts = int(target.astimezone(UTC).timestamp())
            start_ts = int((target - timedelta(hours=6)).astimezone(UTC).timestamp())
            specs[ticker] = (market, historical, start_ts, target_ts)

        recent_tickers = [t for t, (_, hist, s, e) in specs.items() if not hist and s and e]
        historical_tickers = [t for t, (_, hist, s, e) in specs.items() if hist and s and e]
        candle_map: dict[str, list[dict]] = {}
        retrieval_method: dict[str, str] = {}
        candle_errors: dict[str, str] = {}
        batch_requests = 0
        individual_requests = 0
        rate_limit_fallbacks = 0

        # Let the read token bucket refill after market-listing calls, then batch.
        if recent_tickers:
            await asyncio.sleep(1.1)
            batch_start = min(specs[t][2] for t in recent_tickers)
            batch_end = max(specs[t][3] for t in recent_tickers)
            try:
                batch_requests += 1
                batch = await _batch_recent_candles(
                    client,
                    recent_tickers,
                    start_ts=batch_start,
                    end_ts=batch_end,
                )
                for ticker in recent_tickers:
                    candle_map[ticker] = batch.get(ticker, [])
                    retrieval_method[ticker] = "batch_recent"
            except Exception as exc:
                warnings.append(f"Recent batch candlestick request failed; using throttled per-market fallback: {exc}")
                rate_limit_fallbacks += 1
                # A quiet pause replenishes the token bucket before fallback calls.
                await asyncio.sleep(1.5)
                for ticker in recent_tickers:
                    market, historical, start_ts, end_ts = specs[ticker]
                    try:
                        individual_requests += 1
                        candle_map[ticker] = await _single_candles(
                            client,
                            ticker,
                            historical=historical,
                            start_ts=start_ts,
                            end_ts=end_ts,
                        )
                        retrieval_method[ticker] = "single_recent_fallback"
                    except Exception as single_exc:
                        candle_errors[ticker] = str(single_exc)
                        candle_map[ticker] = []
                        retrieval_method[ticker] = "single_recent_fallback"
                    await asyncio.sleep(0.35)

        # No documented historical batch endpoint exists, so archive-tier markets
        # are deliberately serialized with backoff to protect against 429s.
        if historical_tickers:
            await asyncio.sleep(1.1)
            for ticker in historical_tickers:
                market, historical, start_ts, end_ts = specs[ticker]
                try:
                    individual_requests += 1
                    candle_map[ticker] = await _single_candles(
                        client,
                        ticker,
                        historical=True,
                        start_ts=start_ts,
                        end_ts=end_ts,
                    )
                    retrieval_method[ticker] = "single_historical_throttled"
                except Exception as exc:
                    candle_errors[ticker] = str(exc)
                    candle_map[ticker] = []
                    retrieval_method[ticker] = "single_historical_throttled"
                await asyncio.sleep(0.35)

        rows: list[dict] = []
        for market, historical in tagged:
            ticker = str(market.get("ticker") or "")
            if ticker in candle_errors:
                player, threshold = _player_and_threshold(market)
                game_start = _game_start_from_ticker(ticker)
                row = {
                    "ticker": ticker,
                    "source": "historical" if historical else "live_recent",
                    "retrieval_method": retrieval_method.get(ticker, "unknown"),
                    "player": player,
                    "threshold": threshold,
                    "candles_retrieved": 0,
                    "error": candle_errors[ticker],
                }
                if game_start:
                    target = game_start - timedelta(hours=hours_before_first_pitch)
                    row["game_start_et"] = game_start.isoformat()
                    row["entry_target_et"] = target.isoformat()
                rows.append(row)
                continue
            rows.append(
                _finalize_row(
                    market=market,
                    historical=historical,
                    candles=candle_map.get(ticker, []),
                    hours_before_first_pitch=hours_before_first_pitch,
                    retrieval_method=retrieval_method.get(ticker, "not_requested"),
                )
            )

    usable = [r for r in rows if r.get("usable_entry_quote")]
    with_candles = [r for r in rows if int(r.get("candles_retrieved") or 0) > 0]
    cutoff_text = market_cutoff.isoformat() if market_cutoff else None
    return {
        "status": "success" if usable else "no_usable_quotes",
        "target_date": target_date,
        "series_ticker": MLB_STRIKEOUT_PREFIX,
        "storage_route": route,
        "market_settled_cutoff": cutoff_text,
        "entry_rule": (
            f"last quoted 1-minute candle at or before {hours_before_first_pitch:g} hours before first pitch; "
            "6-hour quote lookback"
        ),
        "market_records_found": len(tagged),
        "markets_checked": len(rows),
        "markets_with_candles": len(with_candles),
        "usable_entry_quotes": len(usable),
        "historical_listing_records": listing_counts["historical"],
        "live_recent_listing_records": listing_counts["recent"],
        "batch_requests": batch_requests,
        "individual_candle_requests": individual_requests,
        "rate_limit_fallbacks": rate_limit_fallbacks,
        "proof_passed": bool(usable),
        "markets": rows,
        "warnings": warnings,
        "next_step": (
            "Historical Kalshi price reconstruction is viable. Build the full leakage-safe trading backtester next."
            if usable
            else "Markets were found but no executable quote was reconstructed. Inspect candle counts and per-market errors before changing the entry rule."
        ),
    }
