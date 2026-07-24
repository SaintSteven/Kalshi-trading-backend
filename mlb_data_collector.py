from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo
import httpx

MLB_API_BASE = "https://statsapi.mlb.com/api/v1"
ET = ZoneInfo("America/New_York")

def resolved_date(target_date: str | None = None) -> str:
    return target_date or datetime.now(ET).strftime("%Y-%m-%d")

def current_season(target_date: str | None = None) -> int:
    return datetime.strptime(resolved_date(target_date), "%Y-%m-%d").year

def safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default

async def fetch_json(client: httpx.AsyncClient, path: str, params: dict[str, Any] | None = None) -> dict:
    response = await client.get(f"{MLB_API_BASE}{path}", params=params, timeout=30)
    response.raise_for_status()
    return response.json()

async def get_schedule(client: httpx.AsyncClient, target_date: str | None = None) -> list[dict]:
    payload = await fetch_json(client, "/schedule", {"sportId": 1, "date": resolved_date(target_date), "hydrate": "probablePitcher,team"})
    return [game for date_block in payload.get("dates", []) for game in date_block.get("games", [])]

def probable_pitchers_from_schedule(games: list[dict]) -> list[dict]:
    output = []
    for game in games:
        teams = game.get("teams", {})
        for side, opponent_side in (("away", "home"), ("home", "away")):
            team_block = teams.get(side, {})
            opponent_block = teams.get(opponent_side, {})
            pitcher = team_block.get("probablePitcher")
            if not pitcher:
                continue
            output.append({
                "game_pk": game.get("gamePk"),
                "game_date": game.get("gameDate"),
                "player": pitcher.get("fullName"),
                "player_id": pitcher.get("id"),
                "team_id": team_block.get("team", {}).get("id"),
                "team_name": team_block.get("team", {}).get("name"),
                "opponent_team_id": opponent_block.get("team", {}).get("id"),
                "opponent_team_name": opponent_block.get("team", {}).get("name"),
                "starter_confirmed": True,
                "schedule_status": game.get("status", {}).get("detailedState"),
            })
    return output

async def get_person(client: httpx.AsyncClient, player_id: int) -> dict:
    payload = await fetch_json(client, f"/people/{player_id}")
    return (payload.get("people") or [{}])[0]

async def get_pitching_stats(client: httpx.AsyncClient, player_id: int, season: int) -> dict:
    payload = await fetch_json(client, f"/people/{player_id}/stats", {"stats": "season,career", "group": "pitching", "season": season})
    result = {"season": {}, "career": {}}
    for block in payload.get("stats", []):
        type_name = block.get("type", {}).get("displayName", "").lower()
        splits = block.get("splits", [])
        stat = splits[0].get("stat", {}) if splits else {}
        if "season" in type_name:
            result["season"] = stat
        elif "career" in type_name:
            result["career"] = stat
    return result

async def get_pitching_game_log(client: httpx.AsyncClient, player_id: int, season: int) -> list[dict]:
    payload = await fetch_json(client, f"/people/{player_id}/stats", {"stats": "gameLog", "group": "pitching", "season": season})
    splits = [split for block in payload.get("stats", []) for split in block.get("splits", [])]
    return sorted(splits, key=lambda split: split.get("date") or "")

async def get_team_hitting_stats(client: httpx.AsyncClient, team_id: int, season: int) -> dict:
    payload = await fetch_json(client, f"/teams/{team_id}/stats", {"stats": "season", "group": "hitting", "season": season})
    for block in payload.get("stats", []):
        splits = block.get("splits", [])
        if splits:
            return splits[0].get("stat", {})
    return {}

def calculate_k_pct(stat: dict) -> tuple[float, int]:
    strikeouts = safe_int(stat.get("strikeOuts"))
    batters_faced = safe_int(stat.get("battersFaced"))
    return ((strikeouts / batters_faced) if batters_faced else 0.0, batters_faced)

def is_start(split: dict) -> bool:
    return safe_int(split.get("stat", {}).get("gamesStarted")) >= 1

def valid_game_row(split: dict) -> bool:
    stat = split.get("stat", {})
    bf = safe_int(stat.get("battersFaced"))
    pitches = safe_int(stat.get("numberOfPitches"))
    return 1 <= bf <= 45 and 1 <= pitches <= 150

def recent_start_rows(game_log: list[dict], limit: int = 6) -> list[dict]:
    return [split for split in game_log if is_start(split) and valid_game_row(split)][-limit:]

def recent_summary(game_log: list[dict]) -> dict:
    rows = recent_start_rows(game_log, 6)
    total_k = sum(safe_int(r.get("stat", {}).get("strikeOuts")) for r in rows)
    total_bf = sum(safe_int(r.get("stat", {}).get("battersFaced")) for r in rows)
    pitch_counts = [safe_int(r.get("stat", {}).get("numberOfPitches")) for r in rows]
    bf_values = [safe_int(r.get("stat", {}).get("battersFaced")) for r in rows]
    return {
        "recent_k_pct": total_k / total_bf if total_bf else 0.0,
        "recent_batters_faced": total_bf,
        "recent_pitch_counts": pitch_counts[-3:],
        "recent_start_batters_faced": bf_values,
        "recent_starts": len(rows),
    }

def expected_batters_faced(season_stat: dict, game_log: list[dict]) -> tuple[float, int, int]:
    rows = recent_start_rows(game_log, 5)
    recent_values = [safe_int(r.get("stat", {}).get("battersFaced")) for r in rows]
    if recent_values:
        recent_avg = sum(recent_values) / len(recent_values)
        gs = safe_int(season_stat.get("gamesStarted"))
        season_bf = safe_int(season_stat.get("battersFaced"))
        season_avg = season_bf / gs if gs and season_bf else recent_avg
        expected = recent_avg * 0.65 + season_avg * 0.35
        floor = max(10, min(recent_values) - 2)
        ceiling = min(34, max(recent_values) + 2)
        return round(max(floor, min(ceiling, expected)), 2), floor, ceiling
    gs = safe_int(season_stat.get("gamesStarted"))
    season_bf = safe_int(season_stat.get("battersFaced"))
    if gs and season_bf:
        expected = max(12.0, min(32.0, season_bf / gs))
        return round(expected, 2), max(10, round(expected - 4)), min(34, round(expected + 4))
    return 22.0, 16, 28

def team_k_pct(team_stat: dict) -> tuple[float, int]:
    strikeouts = safe_int(team_stat.get("strikeOuts"))
    pa = safe_int(team_stat.get("plateAppearances"))
    if not pa:
        pa = safe_int(team_stat.get("atBats")) + safe_int(team_stat.get("baseOnBalls")) + safe_int(team_stat.get("hitByPitch")) + safe_int(team_stat.get("sacFlies"))
    return ((strikeouts / pa) if pa else 0.225, pa)

def validate_workload(expected_bf: float, floor: int, ceiling: int, pitch_counts: list[int]) -> list[str]:
    warnings = []
    if not 8 <= expected_bf <= 34:
        warnings.append("Expected batters faced failed sanity validation.")
    if floor > ceiling:
        warnings.append("Workload floor exceeds workload ceiling.")
    if any(v > 150 or v < 1 for v in pitch_counts):
        warnings.append("At least one recent pitch count failed sanity validation.")
    return warnings

async def collect_automatic_pitcher_data(target_date: str | None = None) -> list[dict]:
    season = current_season(target_date)
    async with httpx.AsyncClient(headers={"User-Agent": "KalshiTradingPlatform/0.7"}) as client:
        games = await get_schedule(client, target_date)
        output = []
        for starter in probable_pitchers_from_schedule(games):
            person = await get_person(client, starter["player_id"])
            pitching = await get_pitching_stats(client, starter["player_id"], season)
            game_log = await get_pitching_game_log(client, starter["player_id"], season)
            opponent = await get_team_hitting_stats(client, starter["opponent_team_id"], season)
            season_k_pct, season_bf = calculate_k_pct(pitching["season"])
            career_k_pct, career_bf = calculate_k_pct(pitching["career"])
            recent = recent_summary(game_log)
            opponent_k_pct, opponent_pa = team_k_pct(opponent)
            expected_bf, floor, ceiling = expected_batters_faced(pitching["season"], game_log)
            warnings = validate_workload(expected_bf, floor, ceiling, recent["recent_pitch_counts"])
            output.append({
                **starter,
                "pitcher_hand": person.get("pitchHand", {}).get("code", "R"),
                "season_k_pct": season_k_pct,
                "career_k_pct": career_k_pct or season_k_pct,
                "recent_k_pct": recent["recent_k_pct"] or season_k_pct,
                "season_batters_faced": season_bf,
                "career_batters_faced": career_bf,
                "recent_batters_faced": recent["recent_batters_faced"],
                "recent_starts": recent["recent_starts"],
                "recent_start_batters_faced": recent["recent_start_batters_faced"],
                "expected_batters_faced": expected_bf,
                "workload_floor": floor,
                "workload_ceiling": ceiling,
                "recent_pitch_counts": recent["recent_pitch_counts"],
                "opponent_lineup_k_pct": opponent_k_pct,
                "opponent_plate_appearances": opponent_pa,
                "league_k_pct": 0.225,
                "lineup_confirmed": False,
                "starter_role": "NORMAL",
                "velocity_change_mph": 0.0,
                "whiff_rate_change": 0.0,
                "pitch_mix_change_supported": False,
                "data_warnings": warnings,
                "source_notes": [
                    "Probable starter from MLB schedule feed.",
                    "Season and career pitching stats from MLB Stats API.",
                    "Recent workload uses individual pitching game-log rows.",
                    "Only valid starts with 1-45 BF and 1-150 pitches are accepted.",
                    "Opponent adjustment currently uses team season K%.",
                ],
            })
        return output
