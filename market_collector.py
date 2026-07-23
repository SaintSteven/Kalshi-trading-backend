import re
from datetime import datetime
from zoneinfo import ZoneInfo
import httpx
from config import KALSHI_BASE_URL, MLB_STRIKEOUT_PREFIX
from models import Market

ET = ZoneInfo("America/New_York")
DATE_RE = re.compile(rf"^{re.escape(MLB_STRIKEOUT_PREFIX)}-(\d{{2}}[A-Z]{{3}}\d{{2}})")

def kalshi_ticker_date(target_date: str | None = None) -> str:
    dt = datetime.strptime(target_date, "%Y-%m-%d").replace(tzinfo=ET) if target_date else datetime.now(ET)
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

async def pull_open_markets(client):
    markets=[]; cursor=None
    while True:
        params={"status":"open","limit":1000,"mve_filter":"exclude"}
        if cursor: params["cursor"]=cursor
        response=await client.get(f"{KALSHI_BASE_URL}/markets",params=params,timeout=30)
        response.raise_for_status()
        payload=response.json()
        markets.extend(payload.get("markets",[]))
        cursor=payload.get("cursor")
        if not cursor: return markets

async def collect_mlb_strikeout_markets(target_date=None, *, tradable_only=True, min_ask=2, max_ask=98, max_combined_ask=110):
    async with httpx.AsyncClient(headers={"User-Agent":"KalshiTradingPlatform/0.4.1"}) as client:
        raw=await pull_open_markets(client)
    selected=resolve_slate_token(raw,target_date)
    output=[]
    for item in raw:
        ticker=str(item.get("ticker",""))
        if not ticker.startswith(MLB_STRIKEOUT_PREFIX) or selected not in ticker: continue
        title=str(item.get("title",""))
        ya=_first_price(item,"yes_ask_dollars","yes_ask")
        na=_first_price(item,"no_ask_dollars","no_ask")
        tradable,reasons=evaluate_tradability(ya,na,min_ask=min_ask,max_ask=max_ask,max_combined_ask=max_combined_ask)
        output.append(Market(ticker=ticker,event_ticker=item.get("event_ticker"),title=title,player=_extract_player(title),threshold=_extract_threshold(title),yes_bid_cents=_first_price(item,"yes_bid_dollars","yes_bid"),yes_ask_cents=ya,no_bid_cents=_first_price(item,"no_bid_dollars","no_bid"),no_ask_cents=na,volume=item.get("volume_fp") or item.get("volume"),liquidity_dollars=item.get("liquidity_dollars"),close_time=item.get("close_time"),tradable=tradable,tradability_reasons=reasons))
    output.sort(key=lambda m:(m.close_time or datetime.max.replace(tzinfo=ET),m.event_ticker or "",m.player,int(m.threshold.rstrip("+")) if m.threshold.rstrip("+").isdigit() else 999))
    visible=[m for m in output if m.tradable] if tradable_only else output
    return selected, visible, output
