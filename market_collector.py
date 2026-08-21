import asyncio
import re
import time
from datetime import datetime
from zoneinfo import ZoneInfo
import httpx
from config import KALSHI_BASE_URL, MLB_STRIKEOUT_PREFIX
from models import Market

ET = ZoneInfo("America/New_York")
CACHE_TTL_SECONDS = 300
STALE_CACHE_TTL_SECONDS = 1800
_RAW_MARKET_CACHE: dict[str, object] = {"fetched_at": 0.0, "markets": []}


class KalshiRateLimitError(RuntimeError):
    """Raised when Kalshi continues to rate-limit requests after retries."""

    def __init__(self, retry_after_seconds: int = 60):
        super().__init__("Kalshi market data is temporarily rate-limited.")
        self.retry_after_seconds = max(1, int(retry_after_seconds))


def _cached_markets(*, allow_stale: bool = False) -> list[dict] | None:
    fetched_at = float(_RAW_MARKET_CACHE.get("fetched_at") or 0)
    markets = _RAW_MARKET_CACHE.get("markets") or []
    max_age = STALE_CACHE_TTL_SECONDS if allow_stale else CACHE_TTL_SECONDS
    if markets and time.monotonic() - fetched_at <= max_age:
        return list(markets)
    return None


def _save_market_cache(markets: list[dict]) -> None:
    _RAW_MARKET_CACHE["fetched_at"] = time.monotonic()
    _RAW_MARKET_CACHE["markets"] = list(markets)
DATE_RE = re.compile(rf"^{re.escape(MLB_STRIKEOUT_PREFIX)}-(\d{{2}}[A-Z]{{3}}\d{{2}})")
GAME_RE = re.compile(
    rf"^{re.escape(MLB_STRIKEOUT_PREFIX)}-(\d{{2}}[A-Z]{{3}}\d{{2}})(\d{{4}})([A-Z]{{4,6}})-"
)

TEAM_NAMES = {
    "ARI": "Arizona Diamondbacks", "AZ": "Arizona Diamondbacks", "ATH": "Athletics", "ATL": "Atlanta Braves",
    "BAL": "Baltimore Orioles", "BOS": "Boston Red Sox", "CHC": "Chicago Cubs",
    "CIN": "Cincinnati Reds", "CLE": "Cleveland Guardians", "COL": "Colorado Rockies",
    "CWS": "Chicago White Sox", "DET": "Detroit Tigers", "HOU": "Houston Astros",
    "KC": "Kansas City Royals", "KCR": "Kansas City Royals", "LAA": "Los Angeles Angels",
    "LAD": "Los Angeles Dodgers", "MIA": "Miami Marlins", "MIL": "Milwaukee Brewers",
    "MIN": "Minnesota Twins", "NYM": "New York Mets", "NYY": "New York Yankees",
    "OAK": "Athletics", "PHI": "Philadelphia Phillies", "PIT": "Pittsburgh Pirates",
    "SD": "San Diego Padres", "SDP": "San Diego Padres", "SEA": "Seattle Mariners",
    "SF": "San Francisco Giants", "SFG": "San Francisco Giants", "STL": "St. Louis Cardinals",
    "TB": "Tampa Bay Rays", "TBR": "Tampa Bay Rays", "TEX": "Texas Rangers",
    "TOR": "Toronto Blue Jays", "WSH": "Washington Nationals",
}


def _game_details_from_ticker(ticker: str) -> dict:
    match = GAME_RE.search(ticker)
    if not match:
        return {
            "away_team": None, "away_team_name": None, "home_team": None,
            "home_team_name": None, "matchup": None, "game_start_time": None,
            "game_start_display": None, "game_status": "UNKNOWN",
        }

    date_token, hhmm, team_token = match.groups()
    away = home = None
    team_codes = sorted(TEAM_NAMES, key=len, reverse=True)
    for away_candidate in team_codes:
        if not team_token.startswith(away_candidate):
            continue
        home_candidate = team_token[len(away_candidate):]
        if home_candidate in TEAM_NAMES:
            away, home = away_candidate, home_candidate
            break
    if not away or not home:
        return {
            "away_team": None, "away_team_name": None, "home_team": None,
            "home_team_name": None, "matchup": None, "game_start_time": None,
            "game_start_display": None, "game_status": "UNKNOWN",
        }

    try:
        start = datetime.strptime(date_token + hhmm, "%y%b%d%H%M").replace(tzinfo=ET)
        now = datetime.now(ET)
        status = "UPCOMING" if start > now else "LIVE"
        display = start.strftime("%a %b %-d · %-I:%M %p ET")
    except (TypeError, ValueError):
        start = None
        status = "UNKNOWN"
        display = None

    away_name = TEAM_NAMES.get(away, away)
    home_name = TEAM_NAMES.get(home, home)
    return {
        "away_team": away,
        "away_team_name": away_name,
        "home_team": home,
        "home_team_name": home_name,
        "matchup": f"{away_name} at {home_name}",
        "game_start_time": start,
        "game_start_display": display,
        "game_status": status,
    }


def normalize_target_date(target_date: str | None = None) -> str | None:
    """Normalize optional API date input.

    Swagger may display placeholder strings such as "string" or users may
    accidentally submit "null" as text. Treat those placeholders as an omitted
    date, while rejecting other malformed values with a clear error.
    """
    if target_date is None:
        return None

    value = str(target_date).strip()
    if not value or value.lower() in {"null", "none", "string"}:
        return None

    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError as exc:
        raise ValueError(
            "date must use YYYY-MM-DD format or be omitted"
        ) from exc

    return value


def kalshi_ticker_date(target_date: str | None = None) -> str:
    normalized = normalize_target_date(target_date)
    dt = (
        datetime.strptime(normalized, "%Y-%m-%d").replace(tzinfo=ET)
        if normalized
        else datetime.now(ET)
    )
    return dt.strftime("%y%b%d").upper()

def token_to_dt(token: str) -> datetime:
    return datetime.strptime(token, "%y%b%d").replace(tzinfo=ET)

def extract_ticker_date_token(ticker: str) -> str | None:
    match = DATE_RE.search(ticker)
    return match.group(1) if match else None

def resolve_slate_token(raw_markets: list[dict], target_date: str | None = None) -> str:
    requested = kalshi_ticker_date(target_date)
    tokens = sorted({
        token for item in raw_markets
        for token in [extract_ticker_date_token(str(item.get("ticker","")))]
        if token
    }, key=token_to_dt)
    if target_date is not None or requested in tokens:
        return requested
    upcoming = [t for t in tokens if token_to_dt(t) >= token_to_dt(requested)]
    if upcoming:
        return upcoming[0]
    return tokens[-1] if tokens else requested

def _to_cents(value):
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

def _first_price(market, dollar_key, legacy_key):
    return _to_cents(market.get(dollar_key)) if market.get(dollar_key) is not None else _to_cents(market.get(legacy_key))

def _extract_player(title):
    return title.split(":",1)[0].strip() if ":" in title else title.strip()

def _extract_threshold(title):
    match = re.search(r"(\d+)\+", title)
    return f"{match.group(1)}+" if match else ""

def evaluate_tradability(yes_ask, no_ask, *, min_ask=2, max_ask=98, max_combined_ask=110):
    reasons=[]
    if yes_ask is None: reasons.append("Missing YES ask.")
    if no_ask is None: reasons.append("Missing NO ask.")
    if yes_ask is not None and not min_ask <= yes_ask <= max_ask: reasons.append("YES ask outside tradable range.")
    if no_ask is not None and not min_ask <= no_ask <= max_ask: reasons.append("NO ask outside tradable range.")
    if yes_ask is not None and no_ask is not None and yes_ask + no_ask > max_combined_ask: reasons.append("Combined asks above sanity limit.")
    return not reasons, reasons

async def pull_open_markets(client, *, force_refresh: bool = False, trace_callback=None):
    if not force_refresh:
        cached = _cached_markets()
        if cached is not None:
            return cached

    markets=[]; cursor=None
    retry_delays=(2,5)
    page=0
    while True:
        page += 1
        # Scope the live pull to the MLB strikeout series. The prior implementation
        # paginated through every open Kalshi market and filtered locally, which is
        # unnecessary for forward MLB capture and can make the request extremely heavy.
        params={"series_ticker":MLB_STRIKEOUT_PREFIX,"status":"open","limit":1000,"mve_filter":"exclude"}
        if trace_callback:
            trace_callback("request_begin", {"page": page, "cursor": bool(cursor), "markets_so_far": len(markets), "series_ticker": MLB_STRIKEOUT_PREFIX})
        if cursor: params["cursor"]=cursor

        response = None
        for attempt in range(len(retry_delays) + 1):
            response=await client.get(f"{KALSHI_BASE_URL}/markets",params=params,timeout=30)
            if trace_callback:
                trace_callback("response_received", {"page": page, "attempt": attempt + 1, "status_code": response.status_code, "content_length": response.headers.get("content-length")})
            if response.status_code != 429:
                break
            if attempt < len(retry_delays):
                header_delay = response.headers.get("Retry-After")
                try:
                    delay = max(retry_delays[attempt], float(header_delay)) if header_delay else retry_delays[attempt]
                except (TypeError, ValueError):
                    delay = retry_delays[attempt]
                await asyncio.sleep(min(delay, 10))

        if response is None:
            raise RuntimeError("Kalshi market request did not return a response.")

        if response.status_code == 429:
            stale = _cached_markets(allow_stale=True)
            if stale is not None:
                return stale
            retry_after = response.headers.get("Retry-After")
            try:
                retry_after_seconds = int(float(retry_after)) if retry_after else 60
            except (TypeError, ValueError):
                retry_after_seconds = 60
            raise KalshiRateLimitError(retry_after_seconds)

        response.raise_for_status()
        if trace_callback:
            trace_callback("response_status_ok", {"page": page, "status_code": response.status_code})
        payload=response.json()
        page_markets = payload.get("markets",[])
        if trace_callback:
            trace_callback("response_parsed", {"page": page, "page_markets": len(page_markets), "has_cursor": bool(payload.get("cursor"))})
        markets.extend(page_markets)
        cursor=payload.get("cursor")
        if trace_callback:
            trace_callback("page_accumulated", {"page": page, "markets_total": len(markets), "has_cursor": bool(cursor)})
        if not cursor:
            _save_market_cache(markets)
            if trace_callback:
                trace_callback("pull_complete", {"pages": page, "markets_total": len(markets)})
            return markets

async def collect_mlb_strikeout_markets(target_date=None, *, tradable_only=True, min_ask=2, max_ask=98, max_combined_ask=110, force_refresh=False, trace_callback=None):
    target_date = normalize_target_date(target_date)
    async with httpx.AsyncClient(headers={"User-Agent":"KalshiTradingPlatform/0.4.1"}) as client:
        if trace_callback:
            trace_callback("client_ready", {"target_date": target_date, "force_refresh": force_refresh})
        raw=await pull_open_markets(client, force_refresh=force_refresh, trace_callback=trace_callback)
        if trace_callback:
            trace_callback("raw_markets_ready", {"raw_markets": len(raw)})
    selected=resolve_slate_token(raw,target_date)
    if trace_callback:
        trace_callback("slate_resolved", {"selected_slate": selected, "raw_markets": len(raw)})
    output=[]
    for item in raw:
        ticker=str(item.get("ticker",""))
        if not ticker.startswith(MLB_STRIKEOUT_PREFIX) or selected not in ticker: continue
        title=str(item.get("title",""))
        ya=_first_price(item,"yes_ask_dollars","yes_ask")
        na=_first_price(item,"no_ask_dollars","no_ask")
        tradable,reasons=evaluate_tradability(ya,na,min_ask=min_ask,max_ask=max_ask,max_combined_ask=max_combined_ask)
        game = _game_details_from_ticker(ticker)
        output.append(Market(
            ticker=ticker,
            event_ticker=item.get("event_ticker"),
            title=title,
            player=_extract_player(title),
            threshold=_extract_threshold(title),
            **game,
            yes_bid_cents=_first_price(item,"yes_bid_dollars","yes_bid"),
            yes_ask_cents=ya,
            no_bid_cents=_first_price(item,"no_bid_dollars","no_bid"),
            no_ask_cents=na,
            volume=item.get("volume_fp") or item.get("volume"),
            liquidity_dollars=item.get("liquidity_dollars"),
            close_time=item.get("close_time"),
            tradable=tradable,
            tradability_reasons=reasons,
        ))
    output.sort(key=lambda m:(m.close_time or datetime.max.replace(tzinfo=ET),m.event_ticker or "",m.player,int(m.threshold.rstrip("+")) if m.threshold.rstrip("+").isdigit() else 999))
    visible=[m for m in output if m.tradable] if tradable_only else output
    if trace_callback:
        trace_callback("market_objects_built", {"all_series_markets": len(output), "visible_tradable": len(visible), "selected_slate": selected})
    return selected, visible, output
