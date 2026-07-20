import re
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx

from config import KALSHI_BASE_URL, MLB_STRIKEOUT_PREFIX
from models import Market


ET = ZoneInfo("America/New_York")


def kalshi_ticker_date(target_date: str | None = None) -> str:
    if target_date:
        parsed = datetime.strptime(target_date, "%Y-%m-%d").replace(tzinfo=ET)
    else:
        parsed = datetime.now(ET)
    return parsed.strftime("%y%b%d").upper()


def _to_cents(value: object) -> int | None:
    if value is None or value == "":
        return None

    try:
        number = float(value)
    except (TypeError, ValueError):
        return None

    if 0 <= number <= 1:
        return round(number * 100)

    if 1 < number <= 100:
        return round(number)

    return None


def _first_price(market: dict, dollar_key: str, legacy_key: str) -> int | None:
    if market.get(dollar_key) is not None:
        return _to_cents(market.get(dollar_key))
    return _to_cents(market.get(legacy_key))


def _extract_player(title: str) -> str:
    return title.split(":", 1)[0].strip() if ":" in title else title.strip()


def _extract_threshold(title: str) -> str:
    match = re.search(r"(\d+)\+", title)
    return f"{match.group(1)}+" if match else ""


async def pull_open_markets(client: httpx.AsyncClient) -> list[dict]:
    markets: list[dict] = []
    cursor: str | None = None

    while True:
        params: dict[str, object] = {
            "status": "open",
            "limit": 1000,
            "mve_filter": "exclude",
        }
        if cursor:
            params["cursor"] = cursor

        response = await client.get(
            f"{KALSHI_BASE_URL}/markets",
            params=params,
            timeout=30,
        )
        response.raise_for_status()
        payload = response.json()

        markets.extend(payload.get("markets", []))
        cursor = payload.get("cursor")
        if not cursor:
            break

    return markets


async def collect_mlb_strikeout_markets(
    target_date: str | None = None,
) -> tuple[str, list[Market]]:
    ticker_date = kalshi_ticker_date(target_date)

    async with httpx.AsyncClient(
        headers={"User-Agent": "KalshiTradingPlatform/0.2"}
    ) as client:
        raw_markets = await pull_open_markets(client)

    output: list[Market] = []

    for market in raw_markets:
        ticker = str(market.get("ticker", ""))
        if not ticker.startswith(MLB_STRIKEOUT_PREFIX):
            continue
        if ticker_date not in ticker:
            continue

        title = str(market.get("title", ""))

        output.append(
            Market(
                ticker=ticker,
                event_ticker=market.get("event_ticker"),
                title=title,
                player=_extract_player(title),
                threshold=_extract_threshold(title),
                yes_bid_cents=_first_price(market, "yes_bid_dollars", "yes_bid"),
                yes_ask_cents=_first_price(market, "yes_ask_dollars", "yes_ask"),
                no_bid_cents=_first_price(market, "no_bid_dollars", "no_bid"),
                no_ask_cents=_first_price(market, "no_ask_dollars", "no_ask"),
                volume=market.get("volume_fp") or market.get("volume"),
                liquidity_dollars=market.get("liquidity_dollars"),
                close_time=market.get("close_time"),
            )
        )

    output.sort(key=lambda x: (x.event_ticker or "", x.player, x.threshold))
    return ticker_date, output
