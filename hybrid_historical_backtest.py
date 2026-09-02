"""Historical proxy backtest for the automated MLB game card.

The test uses a near-close sportsbook snapshot because free sources do not retain
an intraday 11 a.m. consensus archive. Kalshi entries come from timestamped
pregame candlesticks, while team features are rebuilt sequentially from games
completed before each matchup.
"""

from __future__ import annotations

import asyncio
import math
import random
import re
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

import httpx
from pydantic import BaseModel, Field

from automatic_hybrid_card import ALIASES, ESPN_SCOREBOARD, ESPN_SUMMARY, GAME_SERIES, _novig, _norm
from config import KALSHI_BASE_URL, KALSHI_HISTORICAL_BASE_URL
from hybrid_mlb import DiscoverySignal, HybridCandidateRequest, QCCheck, evaluate_candidate

ET = ZoneInfo("America/New_York")
UTC = timezone.utc
MLB_SCHEDULE = "https://statsapi.mlb.com/api/v1/schedule"
EVENT_RE = re.compile(r"^KXMLBGAME-(\d{2}[A-Z]{3}\d{2})(\d{4})([A-Z]{4,7})$")
MLB_TEAM_CODES = {
    108: "LAA", 109: "ARI", 110: "BAL", 111: "BOS", 112: "CHC", 113: "CIN",
    114: "CLE", 115: "COL", 116: "DET", 117: "HOU", 118: "KC", 119: "LAD",
    120: "WSH", 121: "NYM", 133: "ATH", 134: "PIT", 135: "SD", 136: "SEA",
    137: "SF", 138: "STL", 139: "TB", 140: "TEX", 141: "TOR", 142: "MIN",
    143: "PHI", 144: "ATL", 145: "CWS", 146: "MIA", 147: "NYY", 158: "MIL",
}


def _to_cents(value: Any) -> int | None:
    if value in (None, ""):
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


def _close(block: Any) -> Any:
    if not isinstance(block, dict):
        return None
    for key in ("close", "close_dollars", "close_fp"):
        if block.get(key) not in (None, ""):
            return block[key]
    return None


def _last_candle_at_or_before(candles: list[dict], target_ts: int) -> dict | None:
    eligible = [row for row in candles if isinstance(row.get("end_period_ts"), (int, float)) and int(row["end_period_ts"]) <= target_ts]
    return max(eligible, key=lambda row: int(row["end_period_ts"])) if eligible else None


async def _request_json(client: httpx.AsyncClient, url: str, *, params: dict | None = None, max_429_retries: int = 5, base_backoff_seconds: float = 0.4) -> dict:
    attempt = 0
    while True:
        response = await client.get(url, params=params, timeout=60)
        if response.status_code != 429 or attempt >= max_429_retries:
            break
        await asyncio.sleep(base_backoff_seconds * (2 ** attempt) + random.uniform(0, 0.15))
        attempt += 1
    response.raise_for_status()
    return response.json()


class HybridBacktestRequest(BaseModel):
    start_date: str
    end_date: str
    max_days: int = Field(default=31, ge=1, le=31)
    unit_size: float = Field(default=1.0, gt=0, le=100)
    minimum_edge_points: float = Field(default=5.0, ge=0, le=30)
    minutes_before_first_pitch: int = Field(default=10, ge=5, le=120)
    holdout_fraction: float = Field(default=0.30, ge=0.20, le=0.50)


def _date_token(value: str) -> str:
    return datetime.strptime(value, "%Y-%m-%d").strftime("%y%b%d").upper()


def _event_date(event_ticker: str) -> date | None:
    match = EVENT_RE.match(str(event_ticker or ""))
    if not match:
        return None
    try:
        return datetime.strptime(match.group(1), "%y%b%d").date()
    except ValueError:
        return None


def _event_start(event_ticker: str) -> datetime | None:
    match = EVENT_RE.match(str(event_ticker or ""))
    if not match:
        return None
    try:
        return datetime.strptime(match.group(1) + match.group(2), "%y%b%d%H%M").replace(tzinfo=ET)
    except ValueError:
        return None


def _market_team(market: dict) -> str:
    return _norm(str(market.get("ticker") or "").rsplit("-", 1)[-1])


def _mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 4) if values else None


def _log5(a: float, b: float) -> float:
    denominator = a + b - 2 * a * b
    return (a - a * b) / denominator if denominator else 0.5


def _pct(wins: int, losses: int) -> float:
    return wins / (wins + losses) if wins + losses else 0.5


def _blank_team() -> dict:
    return {"wins": 0, "losses": 0, "home_wins": 0, "home_losses": 0, "road_wins": 0, "road_losses": 0, "runs_for": 0, "runs_against": 0, "games": 0}


def _model_probability(away: dict, home: dict) -> tuple[float, dict]:
    away_overall = _pct(away["wins"], away["losses"])
    home_overall = _pct(home["wins"], home["losses"])
    away_road = _pct(away["road_wins"], away["road_losses"])
    home_home = _pct(home["home_wins"], home["home_losses"])
    probability = 0.6 * _log5(away_overall, home_overall) + 0.4 * _log5(away_road, home_home)
    away_rpg = away["runs_for"] / away["games"] if away["games"] else None
    home_rpg = home["runs_for"] / home["games"] if home["games"] else None
    away_rapg = away["runs_against"] / away["games"] if away["games"] else None
    home_rapg = home["runs_against"] / home["games"] if home["games"] else None
    if away_rpg is not None and home_rpg is not None:
        probability += 0.04 * (away_rpg - home_rpg)
    if away_rapg is not None and home_rapg is not None:
        probability += 0.025 * (home_rapg - away_rapg)
    probability = min(0.80, max(0.20, probability))
    return round(probability, 4), {
        "away_overall": round(away_overall, 4), "home_overall": round(home_overall, 4),
        "away_road": round(away_road, 4), "home_home": round(home_home, 4),
        "away_runs_per_game": away_rpg, "home_runs_per_game": home_rpg,
        "away_runs_allowed_per_game": away_rapg, "home_runs_allowed_per_game": home_rapg,
    }


def _update_team(state: dict, *, home: bool, won: bool, runs_for: int, runs_against: int) -> None:
    state["wins" if won else "losses"] += 1
    state[("home_" if home else "road_") + ("wins" if won else "losses")] += 1
    state["runs_for"] += runs_for
    state["runs_against"] += runs_against
    state["games"] += 1


async def _collect_mlb_games(client: httpx.AsyncClient, start: date, end: date) -> dict[tuple[str, frozenset], dict]:
    season_start = date(start.year, 3, 15)
    payload = await _request_json(client, MLB_SCHEDULE, params={
        "sportId": 1, "startDate": season_start.isoformat(), "endDate": end.isoformat(),
        "hydrate": "probablePitcher", "gameType": "R",
    })
    raw = [game for day in payload.get("dates", []) for game in day.get("games", [])]
    raw.sort(key=lambda game: game.get("gameDate") or "")
    states: dict[str, dict] = defaultdict(_blank_team)
    output: dict[tuple[str, frozenset], dict] = {}
    for game in raw:
        away_row = game.get("teams", {}).get("away", {})
        home_row = game.get("teams", {}).get("home", {})
        away = MLB_TEAM_CODES.get(away_row.get("team", {}).get("id"), "")
        home = MLB_TEAM_CODES.get(home_row.get("team", {}).get("id"), "")
        game_date = str(game.get("officialDate") or "")
        if not away or not home or not game_date:
            continue
        model, detail = _model_probability(states[away], states[home])
        if start.isoformat() <= game_date <= end.isoformat():
            output[(game_date, frozenset({away, home}))] = {
                "away_code": away, "home_code": home, "away_model_probability": model,
                "model_detail": detail,
                "away_probable": (away_row.get("probablePitcher") or {}).get("fullName"),
                "home_probable": (home_row.get("probablePitcher") or {}).get("fullName"),
                "game_pk": game.get("gamePk"), "game_start_time": game.get("gameDate"),
            }
        away_score, home_score = away_row.get("score"), home_row.get("score")
        if away_score is None or home_score is None or away_score == home_score:
            continue
        away_score, home_score = int(away_score), int(home_score)
        _update_team(states[away], home=False, won=away_score > home_score, runs_for=away_score, runs_against=home_score)
        _update_team(states[home], home=True, won=home_score > away_score, runs_for=home_score, runs_against=away_score)
    return output


async def _collect_espn_games(client: httpx.AsyncClient, start: date, end: date, progress=None) -> dict[tuple[str, frozenset], dict]:
    events: list[tuple[str, dict]] = []
    current = start
    while current <= end:
        payload = await _request_json(client, ESPN_SCOREBOARD, params={"dates": current.strftime("%Y%m%d"), "limit": 100})
        events.extend((current.isoformat(), event) for event in payload.get("events", []))
        current += timedelta(days=1)
    semaphore = asyncio.Semaphore(8)
    async def fetch(item):
        day, event = item
        async with semaphore:
            try:
                summary = await _request_json(client, ESPN_SUMMARY, params={"event": event.get("id")})
            except Exception:
                summary = {}
            pick = (summary.get("pickcenter") or [None])[0] or {}
            current_probs = _novig((pick.get("awayTeamOdds") or {}).get("moneyLine"), (pick.get("homeTeamOdds") or {}).get("moneyLine"))
            moneyline = pick.get("moneyline") or {}
            open_probs = _novig(((moneyline.get("away") or {}).get("open") or {}).get("odds"), ((moneyline.get("home") or {}).get("open") or {}).get("odds"))
            # Return only the fields the backtest needs. Full ESPN summaries are
            # large, and retaining hundreds until gather completes can exhaust a
            # small Render worker.
            return day, event, current_probs, open_probs
    rows = await asyncio.gather(*(fetch(item) for item in events))
    output = {}
    for day, event, current_probs, open_probs in rows:
        competition = (event.get("competitions") or [{}])[0]
        competitors = competition.get("competitors", [])
        away = next((row for row in competitors if row.get("homeAway") == "away"), None)
        home = next((row for row in competitors if row.get("homeAway") == "home"), None)
        if not away or not home:
            continue
        away_code = _norm(away.get("team", {}).get("abbreviation"))
        home_code = _norm(home.get("team", {}).get("abbreviation"))
        winner = away_code if away.get("winner") is True else home_code if home.get("winner") is True else None
        output[(day, frozenset({away_code, home_code}))] = {
            "event_id": event.get("id"), "away_code": away_code, "home_code": home_code,
            "away_name": away.get("team", {}).get("displayName"), "home_name": home.get("team", {}).get("displayName"),
            "game_start_time": event.get("date"), "winner": winner,
            "current_probs": current_probs, "open_probs": open_probs,
        }
    return output


async def _list_markets(client: httpx.AsyncClient, start: date, end: date) -> list[tuple[dict, bool]]:
    historical_base = KALSHI_HISTORICAL_BASE_URL.rstrip("/")
    recent_base = KALSHI_BASE_URL.rstrip("/")
    found: dict[str, tuple[dict, bool]] = {}
    for historical in (False, True):
        cursor = None
        for _ in range(20):
            path = "/historical/markets" if historical else "/markets"
            params: dict[str, Any] = {"series_ticker": GAME_SERIES, "limit": 1000}
            if not historical:
                params.update({"status": "settled", "mve_filter": "exclude"})
            if cursor:
                params["cursor"] = cursor
            try:
                payload = await _request_json(client, (historical_base if historical else recent_base) + path, params=params)
            except Exception:
                break
            rows = payload.get("markets", [])
            seen_dates = []
            for market in rows:
                day = _event_date(str(market.get("event_ticker") or ""))
                if day:
                    seen_dates.append(day)
                if day and start <= day <= end:
                    found.setdefault(str(market.get("ticker")), (market, historical))
            cursor = payload.get("cursor") or None
            if not cursor or (seen_dates and max(seen_dates) < start):
                break
    return list(found.values())


async def _candles_for_markets(client: httpx.AsyncClient, tagged: list[tuple[dict, bool]], minutes_before: int, warnings: list[str]) -> dict[str, dict]:
    historical_base = KALSHI_HISTORICAL_BASE_URL.rstrip("/")
    recent_base = KALSHI_BASE_URL.rstrip("/")
    by_day: dict[date, list[tuple[dict, bool]]] = defaultdict(list)
    for item in tagged:
        day = _event_date(str(item[0].get("event_ticker") or ""))
        if day:
            by_day[day].append(item)
    output = {}
    for day, items in sorted(by_day.items()):
        recent = [market for market, historical in items if not historical]
        archive = [market for market, historical in items if historical]
        if recent:
            specs = []
            for market in recent:
                start = _event_start(str(market.get("event_ticker") or ""))
                if start:
                    target = start - timedelta(minutes=minutes_before)
                    specs.append((market, int((target - timedelta(hours=3)).astimezone(UTC).timestamp()), int(target.astimezone(UTC).timestamp())))
            # The batch endpoint also caps total returned candles at 10,000.
            # Ten contracts keeps even a spread-out MLB slate below that cap.
            specs.sort(key=lambda item: item[2])
            for chunk_start in range(0, len(specs), 10):
                chunk = specs[chunk_start:chunk_start + 10]
                if not chunk:
                    continue
                try:
                    payload = await _request_json(client, recent_base + "/markets/candlesticks", params={
                        "market_tickers": ",".join(str(m.get("ticker")) for m, _, _ in chunk),
                        "start_ts": min(s for _, s, _ in chunk), "end_ts": max(e for _, _, e in chunk),
                        "period_interval": 1, "include_latest_before_start": "false",
                    }, max_429_retries=6, base_backoff_seconds=0.5)
                    candle_map = {str(row.get("market_ticker") or row.get("ticker")): row.get("candlesticks", []) for row in payload.get("markets", [])}
                    for market, _, target_ts in chunk:
                        ticker = str(market.get("ticker"))
                        candle = _last_candle_at_or_before(candle_map.get(ticker, []), target_ts)
                        output[ticker] = {"price": _to_cents(_close((candle or {}).get("yes_ask"))), "quote_ts": (candle or {}).get("end_period_ts")}
                except Exception as exc:
                    warnings.append(f"{day}: recent Kalshi candle batch failed: {exc}")
        semaphore = asyncio.Semaphore(3)
        async def archive_one(market):
            ticker = str(market.get("ticker"))
            start = _event_start(str(market.get("event_ticker") or ""))
            if not start:
                return
            target = start - timedelta(minutes=minutes_before)
            try:
                async with semaphore:
                    payload = await _request_json(client, f"{historical_base}/historical/markets/{ticker}/candlesticks", params={
                        "start_ts": int((target - timedelta(hours=3)).astimezone(UTC).timestamp()),
                        "end_ts": int(target.astimezone(UTC).timestamp()), "period_interval": 1,
                    }, max_429_retries=6, base_backoff_seconds=0.55)
                candle = _last_candle_at_or_before(payload.get("candlesticks", []), int(target.astimezone(UTC).timestamp()))
                output[ticker] = {"price": _to_cents(_close((candle or {}).get("yes_ask"))), "quote_ts": (candle or {}).get("end_period_ts")}
            except Exception as exc:
                warnings.append(f"{ticker}: historical candle failed: {exc}")
        if archive:
            await asyncio.gather(*(archive_one(market) for market in archive))
    return output


def _summary(rows: list[dict], unit_size: float) -> dict:
    pnl = round(sum(row["profit_loss"] for row in rows), 2)
    risked = round(len(rows) * unit_size, 2)
    wins = sum(1 for row in rows if row["won"])
    equity = peak = drawdown = 0.0
    for row in sorted(rows, key=lambda item: (item["date"], item["game_start_time"], item["ticker"])):
        equity += row["profit_loss"]
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
    return {
        "bets": len(rows), "wins": wins, "losses": len(rows) - wins,
        "win_rate": round(wins / len(rows), 4) if rows else None,
        "risked": risked, "profit_loss": pnl,
        "roi": round(pnl / risked, 4) if risked else None,
        "maximum_drawdown": round(drawdown, 2),
        "average_edge_points": _mean([row["edge_points"] for row in rows]),
        "average_entry_cents": _mean([row["entry_price_cents"] for row in rows]),
    }


async def run_hybrid_historical_backtest(request: HybridBacktestRequest, progress_callback=None) -> dict:
    start = datetime.strptime(request.start_date, "%Y-%m-%d").date()
    end = datetime.strptime(request.end_date, "%Y-%m-%d").date()
    if end < start:
        raise ValueError("end_date must be on or after start_date.")
    days = (end - start).days + 1
    if days > request.max_days:
        raise ValueError(f"Requested {days} days; maximum for this run is {request.max_days}.")
    if end >= datetime.now(ET).date():
        raise ValueError("The backtest end date must be before today so every outcome is final.")
    warnings: list[str] = []
    async def emit(phase, percent, message):
        if progress_callback:
            result = progress_callback({"phase": phase, "percent": percent, "message": message})
            if asyncio.iscoroutine(result):
                await result
    headers = {"User-Agent": "KalshiTradingPlatform/3.6.0-hybrid-backtest"}
    async with httpx.AsyncClient(headers=headers, timeout=60) as client:
        await emit("features", 5, "Rebuilding team information available before each historical game…")
        mlb_task = asyncio.create_task(_collect_mlb_games(client, start, end))
        espn_task = asyncio.create_task(_collect_espn_games(client, start, end))
        markets_task = asyncio.create_task(_list_markets(client, start, end))
        mlb_games, espn_games, tagged = await asyncio.gather(mlb_task, espn_task, markets_task)
        await emit("prices", 35, f"Found {len(tagged)} historical Kalshi contracts; reconstructing executable pregame asks…")
        prices = await _candles_for_markets(client, tagged, request.minutes_before_first_pitch, warnings)
    grouped: dict[str, list[dict]] = defaultdict(list)
    for market, _ in tagged:
        grouped[str(market.get("event_ticker"))].append(market)
    bets = []
    games_evaluated = 0
    coverage = {"market_events": len(grouped), "espn_games": len(espn_games), "mlb_games": len(mlb_games), "quoted_contracts": sum(1 for row in prices.values() if row.get("price") is not None), "odds_games": 0}
    for event_ticker, pair in grouped.items():
        day_value = _event_date(event_ticker)
        if not day_value or len(pair) < 2:
            continue
        codes = frozenset(_market_team(market) for market in pair)
        key = (day_value.isoformat(), codes)
        espn = espn_games.get(key)
        mlb = mlb_games.get(key)
        if not espn or not mlb:
            continue
        away_external, home_external = espn["current_probs"]
        away_open, home_open = espn["open_probs"]
        if away_external is None or home_external is None:
            continue
        coverage["odds_games"] += 1
        games_evaluated += 1
        for market in pair:
            team = _market_team(market)
            is_away = team == espn["away_code"]
            external = away_external if is_away else home_external
            opening = away_open if is_away else home_open
            model = mlb["away_model_probability"] if is_away else round(1 - mlb["away_model_probability"], 4)
            move = (external - opening) * 100 if opening is not None else 0.0
            price = (prices.get(str(market.get("ticker"))) or {}).get("price")
            if price is None:
                continue
            pair_prices = [(prices.get(str(row.get("ticker"))) or {}).get("price") for row in pair]
            pair_sum = sum(pair_prices) if all(value is not None for value in pair_prices) else 999
            probables = (mlb.get("away_probable"), mlb.get("home_probable"))
            result = evaluate_candidate(HybridCandidateRequest(
                candidate_id=str(market.get("ticker")), market_type="GAME",
                selection=str(market.get("title") or f"{team} wins"), contract_side="YES",
                kalshi_price_cents=int(price), model_fair_probability=model,
                external_market_probability=external, minimum_edge_points=request.minimum_edge_points,
                market_move_points=round(move, 2),
                signals=[
                    DiscoverySignal(source="Historical ESPN/DraftKings closing no-vig", kind="SHARP_MARKET", independence_group="espn-dk", supports_candidate=external >= 0.5),
                    DiscoverySignal(source="Historical opening-to-close movement", kind="SHARP_MARKET", independence_group="espn-dk", supports_candidate=move >= 1.0),
                    DiscoverySignal(source="Leakage-safe rolling record/venue/run model", kind="MODEL", independence_group="rolling-team-model", supports_candidate=model >= 0.5),
                ],
                qc_checks=[
                    QCCheck(label="Historical executable Kalshi asks", status="PASS" if 2 <= price <= 98 and pair_sum <= 112 else "FAIL", note=f"Two-outcome ask sum {pair_sum} cents."),
                    QCCheck(label="Historical external no-vig line", status="PASS"),
                    QCCheck(label="Completed game outcome", status="PASS" if espn.get("winner") else "FAIL"),
                    QCCheck(label="Archived probable pitchers", status="PASS" if all(probables) else "PENDING"),
                ],
            ))
            if result.decision != "BUY":
                continue
            won = espn.get("winner") == team
            profit = request.unit_size * (100 - price) / price if won else -request.unit_size
            bets.append({
                "date": day_value.isoformat(), "ticker": market.get("ticker"), "selection": result.selection,
                "matchup": f"{espn['away_name']} at {espn['home_name']}", "game_start_time": espn["game_start_time"],
                "team_code": team, "entry_price_cents": int(price), "model_probability": model,
                "external_probability": external, "blended_probability": result.blended_fair_probability,
                "edge_points": result.raw_edge_points, "discovery_grade": result.discovery_grade,
                "won": won, "profit_loss": round(profit, 4), "move_points": round(move, 2),
            })
    bets.sort(key=lambda row: (row["date"], row["game_start_time"], row["ticker"]))
    split_date = start + timedelta(days=max(1, math.floor(days * (1 - request.holdout_fraction))))
    training = [row for row in bets if datetime.strptime(row["date"], "%Y-%m-%d").date() < split_date]
    holdout = [row for row in bets if datetime.strptime(row["date"], "%Y-%m-%d").date() >= split_date]
    by_grade = {grade: _summary([row for row in bets if row["discovery_grade"] == grade], request.unit_size) for grade in sorted({row["discovery_grade"] for row in bets})}
    buckets = [("2-39¢", 2, 39), ("40-59¢", 40, 59), ("60-79¢", 60, 79), ("80-98¢", 80, 98)]
    by_price = {label: _summary([row for row in bets if low <= row["entry_price_cents"] <= high], request.unit_size) for label, low, high in buckets}
    await emit("complete", 100, f"Backtest complete: {len(bets)} historical BUY recommendations.")
    return {
        "version": "3.6.0", "status": "complete", "mode": "historical-proxy-paper-only",
        "start_date": request.start_date, "end_date": request.end_date,
        "entry_rule": f"Last quoted Kalshi ask at or before {request.minutes_before_first_pitch} minutes before first pitch.",
        "methodology": "DraftKings closing-line proxy plus opening-to-close movement; rolling team features use only games completed earlier. Archived internet handicapper picks are not imputed.",
        "limitations": [
            "The free ESPN archive exposes opening and closing odds, not the exact intraday line seen by the live morning card.",
            "Historical team run prevention uses rolling runs allowed per game as the reproducible proxy for the live team-ERA component.",
            "Backtest performance estimates uncertainty; it cannot guarantee future profit.",
        ],
        "coverage": {**coverage, "games_evaluated": games_evaluated, "buy_recommendations": len(bets)},
        "split_date": split_date.isoformat(), "overall": _summary(bets, request.unit_size),
        "training": _summary(training, request.unit_size), "holdout": _summary(holdout, request.unit_size),
        "by_discovery_grade": by_grade, "by_price_range": by_price,
        "bets": bets, "warnings": warnings[:50],
    }
