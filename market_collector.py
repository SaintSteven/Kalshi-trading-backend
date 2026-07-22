import re
from datetime import datetime
from zoneinfo import ZoneInfo
import httpx
from config import KALSHI_BASE_URL, MLB_STRIKEOUT_PREFIX
from models import Market

ET = ZoneInfo("America/New_York")

def kalshi_ticker_date(target_date: str | None = None) -> str:
    parsed = datetime.strptime(target_date, "%Y-%m-%d").replace(tzinfo=ET) if target_date else datetime.now(ET)
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
    return _to_cents(market.get(dollar_key)) if market.get(dollar_key) is not None else _to_cents(market.get(legacy_key))

def _extract_player(title: str) -> str:
    return title.split(":", 1)[0].strip() if ":" in title else title.strip()

def _extract_threshold(title: str) -> str:
    match = re.search(r"(\d+)\+", title)
    return f"{match.group(1)}+" if match else ""

def evaluate_tradability(yes_ask: int | None, no_ask: int | None, *, min_ask: int = 2, max_ask: int = 98, max_combined_ask: int = 110) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    if yes_ask is None:
        reasons.append("Missing YES ask.")
    if no_ask is None:
        reasons.append("Missing NO ask.")
    if yes_ask is not None and not min_ask <= yes_ask <= max_ask:
        reasons.append(f"YES ask outside {min_ask}-{max_ask}¢ tradable range.")
    if no_ask is not None and not min_ask <= no_ask <= max_ask:
        reasons.append(f"NO ask outside {min_ask}-{max_ask}¢ tradable range.")
    if yes_ask is not None and no_ask is not None and yes_ask + no_ask > max_combined_ask:
        reasons.append(f"Combined asks total {yes_ask + no_ask}¢, above {max_combined_ask}¢ sanity limit.")
    return len(reasons) == 0, reasons

async def pull_open_markets(client: httpx.AsyncClient) -> list[dict]:
    markets, cursor = [], None
    while True:
        params = {"status": "open", "limit": 1000, "mve_filter": "exclude"}
        if cursor:
            params["cursor"] = cursor
        response = await client.get(f"{KALSHI_BASE_URL}/markets", params=params, timeout=30)
        response.raise_for_status()
        payload = response.json()
        markets.extend(payload.get("markets", []))
        cursor = payload.get("cursor")
        if not cursor:
            return markets

async def collect_mlb_strikeout_markets(target_date: str | None = None, *, tradable_only: bool = True, min_ask: int = 2, max_ask: int = 98, max_combined_ask: int = 110) -> tuple[str, list[Market], list[Market]]:
    ticker_date = kalshi_ticker_date(target_date)
    async with httpx.AsyncClient(headers={"User-Agent": "KalshiTradingPlatform/0.3"}) as client:
        raw_markets = await pull_open_markets(client)

    all_markets: list[Market] = []
    for market in raw_markets:
        ticker = str(market.get("ticker", ""))
        if not ticker.startswith(MLB_STRIKEOUT_PREFIX) or ticker_date not in ticker:
            continue
        title = str(market.get("title", ""))
        yes_ask = _first_price(market, "yes_ask_dollars", "yes_ask")
        no_ask = _first_price(market, "no_ask_dollars", "no_ask")
        tradable, reasons = evaluate_tradability(yes_ask, no_ask, min_ask=min_ask, max_ask=max_ask, max_combined_ask=max_combined_ask)
        all_markets.append(Market(
            ticker=ticker,
            event_ticker=market.get("event_ticker"),
            title=title,
            player=_extract_player(title),
            threshold=_extract_threshold(title),
            yes_bid_cents=_first_price(market, "yes_bid_dollars", "yes_bid"),
            yes_ask_cents=yes_ask,
            no_bid_cents=_first_price(market, "no_bid_dollars", "no_bid"),
            no_ask_cents=no_ask,
            volume=market.get("volume_fp") or market.get("volume"),
            liquidity_dollars=market.get("liquidity_dollars"),
            close_time=market.get("close_time"),
            tradable=tradable,
            tradability_reasons=reasons,
        ))

    def threshold_value(m: Market) -> int:
        value = m.threshold.rstrip("+")
        return int(value) if value.isdigit() else 999

    all_markets.sort(key=lambda m: (m.close_time or datetime.max.replace(tzinfo=ET), m.event_ticker or "", m.player, threshold_value(m)))
    visible = [m for m in all_markets if m.tradable] if tradable_only else all_markets
    return ticker_date, visible, all_markets
