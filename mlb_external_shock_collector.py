"""Prospective MLB external-information shock collector.

Research-only. Uses free/public MLB Stats API data plus live Kalshi KXMLBKS quotes.
It does NOT backfill or infer historical lineup-announcement times. Instead it creates
an auditable forward dataset of official information changes and the Kalshi market
state around those changes.

Signals currently collected without paid/private APIs:
- probable starting pitcher changes
- first confirmed starting lineup appearance
- subsequent starting-lineup changes

The collector is designed to run repeatedly (for example every 15 minutes) and append
snapshots/events to JSONL files. A later analyzer can ask whether Kalshi reprices after
these official information shocks.
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from config import KALSHI_BASE_URL, MLB_STRIKEOUT_PREFIX
from historical_market_poc import _player_and_threshold, _request_json

UTC = timezone.utc
MLB_API = "https://statsapi.mlb.com/api/v1"
DATA_DIR = Path(os.getenv("MLB_SHOCK_DATA_DIR", "research_data/mlb_external_shocks"))
STATE_PATH = DATA_DIR / "state.json"
SNAPSHOT_PATH = DATA_DIR / "snapshots.jsonl"
EVENT_PATH = DATA_DIR / "events.jsonl"


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _norm(value: str | None) -> str:
    return "".join(ch.lower() for ch in str(value or "") if ch.isalnum())


def _load_state() -> dict:
    if not STATE_PATH.exists():
        return {"games": {}, "last_run_at": None}
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"games": {}, "last_run_at": None}


def _write_state(state: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")


def _append_jsonl(path: Path, rows: list[dict]) -> None:
    if not rows:
        return
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True, default=str) + "\n")


async def _mlb_json(client: httpx.AsyncClient, path: str, params: dict | None = None) -> dict:
    response = await client.get(MLB_API + path, params=params, timeout=30)
    response.raise_for_status()
    return response.json()


def _person_name(obj: Any) -> str | None:
    if isinstance(obj, dict):
        return obj.get("fullName") or obj.get("name")
    return None


def _lineup_from_boxscore(payload: dict, side: str) -> list[str]:
    team = ((payload.get("teams") or {}).get(side) or {})
    players = team.get("players") or {}
    ordered: list[tuple[int, str]] = []
    for row in players.values():
        order = row.get("battingOrder")
        person = row.get("person") or {}
        name = person.get("fullName")
        if order is None or not name:
            continue
        try:
            ordered.append((int(order), str(name)))
        except (TypeError, ValueError):
            continue
    ordered.sort()
    # MLB battingOrder may include substitutions later. Before first pitch, a nine-player
    # ordered list is a strong confirmation signal. Keep only the first unique nine.
    out: list[str] = []
    seen = set()
    for _, name in ordered:
        key = _norm(name)
        if key and key not in seen:
            out.append(name)
            seen.add(key)
        if len(out) == 9:
            break
    return out


async def fetch_mlb_state(client: httpx.AsyncClient, target_date: str) -> list[dict]:
    schedule = await _mlb_json(
        client,
        "/schedule",
        params={"sportId": 1, "date": target_date, "hydrate": "probablePitcher"},
    )
    games: list[dict] = []
    for date_row in schedule.get("dates", []):
        for game in date_row.get("games", []):
            game_pk = int(game.get("gamePk"))
            teams = game.get("teams") or {}
            away = (teams.get("away") or {}).get("team") or {}
            home = (teams.get("home") or {}).get("team") or {}
            away_prob = _person_name((teams.get("away") or {}).get("probablePitcher"))
            home_prob = _person_name((teams.get("home") or {}).get("probablePitcher"))
            away_lineup: list[str] = []
            home_lineup: list[str] = []
            try:
                box = await _mlb_json(client, f"/game/{game_pk}/boxscore")
                away_lineup = _lineup_from_boxscore(box, "away")
                home_lineup = _lineup_from_boxscore(box, "home")
            except Exception:
                pass
            games.append({
                "game_pk": game_pk,
                "game_date": game.get("gameDate"),
                "status": ((game.get("status") or {}).get("abstractGameState") or ""),
                "away_team": away.get("name"),
                "home_team": home.get("name"),
                "away_probable_pitcher": away_prob,
                "home_probable_pitcher": home_prob,
                "away_lineup": away_lineup,
                "home_lineup": home_lineup,
                "away_lineup_confirmed": len(away_lineup) == 9,
                "home_lineup_confirmed": len(home_lineup) == 9,
            })
    return games


async def fetch_kalshi_strikeouts(client: httpx.AsyncClient) -> list[dict]:
    base = KALSHI_BASE_URL.rstrip("/")
    markets: list[dict] = []
    cursor: str | None = None
    while True:
        params: dict[str, Any] = {
            "series_ticker": MLB_STRIKEOUT_PREFIX,
            "status": "open",
            "limit": 1000,
            "mve_filter": "exclude",
        }
        if cursor:
            params["cursor"] = cursor
        payload = await _request_json(client, base + "/markets", params=params)
        markets.extend(payload.get("markets", []))
        cursor = payload.get("cursor") or None
        if not cursor:
            break
    out = []
    for market in markets:
        player, threshold = _player_and_threshold(market)
        yes_bid = market.get("yes_bid")
        yes_ask = market.get("yes_ask")
        no_bid = market.get("no_bid")
        no_ask = market.get("no_ask")
        out.append({
            "ticker": market.get("ticker"),
            "event_ticker": market.get("event_ticker"),
            "title": market.get("title"),
            "player": player,
            "threshold": threshold,
            "yes_bid": yes_bid,
            "yes_ask": yes_ask,
            "no_bid": no_bid,
            "no_ask": no_ask,
            "close_time": market.get("close_time"),
            "status": market.get("status"),
        })
    return out


def _market_rows_for_pitcher(markets: list[dict], pitcher: str | None) -> list[dict]:
    key = _norm(pitcher)
    if not key:
        return []
    return [m for m in markets if _norm(m.get("player")) == key]


def _event(event_type: str, game: dict, side: str, before: Any, after: Any, markets: list[dict], observed_at: str) -> dict:
    pitcher = game.get("away_probable_pitcher") if side == "away" else game.get("home_probable_pitcher")
    if event_type.startswith("lineup_"):
        # A batting-lineup shock affects the opposing starting pitcher's strikeout ladder.
        pitcher = game.get("home_probable_pitcher") if side == "away" else game.get("away_probable_pitcher")
    return {
        "observed_at": observed_at,
        "event_type": event_type,
        "game_pk": game.get("game_pk"),
        "game_date": game.get("game_date"),
        "away_team": game.get("away_team"),
        "home_team": game.get("home_team"),
        "side": side,
        "before": before,
        "after": after,
        "affected_pitcher": pitcher,
        "kalshi_contracts_at_detection": _market_rows_for_pitcher(markets, pitcher),
    }


def detect_events(previous: dict, games: list[dict], markets: list[dict], observed_at: str) -> list[dict]:
    events: list[dict] = []
    old_games = previous.get("games") or {}
    for game in games:
        key = str(game["game_pk"])
        old = old_games.get(key)
        if not old:
            continue
        for side in ("away", "home"):
            prob_key = f"{side}_probable_pitcher"
            if old.get(prob_key) and game.get(prob_key) and _norm(old.get(prob_key)) != _norm(game.get(prob_key)):
                events.append(_event("probable_pitcher_change", game, side, old.get(prob_key), game.get(prob_key), markets, observed_at))

            lineup_key = f"{side}_lineup"
            confirmed_key = f"{side}_lineup_confirmed"
            old_lineup = old.get(lineup_key) or []
            new_lineup = game.get(lineup_key) or []
            old_confirmed = bool(old.get(confirmed_key))
            new_confirmed = bool(game.get(confirmed_key))
            if not old_confirmed and new_confirmed:
                events.append(_event("lineup_first_confirmed", game, side, old_lineup, new_lineup, markets, observed_at))
            elif old_confirmed and new_confirmed and [_norm(x) for x in old_lineup] != [_norm(x) for x in new_lineup]:
                events.append(_event("lineup_change_after_confirmation", game, side, old_lineup, new_lineup, markets, observed_at))
    return events


async def run_collector(target_date: str | None = None) -> dict:
    observed_at = _now_iso()
    target_date = target_date or datetime.now(UTC).date().isoformat()
    previous = _load_state()
    headers = {"User-Agent": "KalshiTradingPlatform/4.3.0-external-shock-research"}
    async with httpx.AsyncClient(headers=headers, timeout=45) as client:
        games, markets = await asyncio.gather(
            fetch_mlb_state(client, target_date),
            fetch_kalshi_strikeouts(client),
        )

    events = detect_events(previous, games, markets, observed_at)
    snapshot = {
        "observed_at": observed_at,
        "target_date": target_date,
        "games": games,
        "kalshi_markets": markets,
        "event_count": len(events),
    }
    _append_jsonl(SNAPSHOT_PATH, [snapshot])
    _append_jsonl(EVENT_PATH, events)
    state = {"last_run_at": observed_at, "games": {str(g["game_pk"]): g for g in games}}
    _write_state(state)
    return {
        "status": "complete",
        "mode": "prospective-external-shock-collector",
        "observed_at": observed_at,
        "target_date": target_date,
        "games": len(games),
        "open_kalshi_strikeout_markets": len(markets),
        "events_detected": len(events),
        "event_types": [e["event_type"] for e in events],
        "data_dir": str(DATA_DIR),
        "research_only": True,
        "notes": [
            "This collector is forward-only; it does not infer historical lineup announcement times.",
            "Lineup shocks are mapped to the opposing probable starter's strikeout ladder.",
            "Sportsbook divergence is intentionally not fabricated; it requires a lawful odds feed/API key and can be added as an optional adapter later.",
        ],
    }


if __name__ == "__main__":
    print(json.dumps(asyncio.run(run_collector()), indent=2))
