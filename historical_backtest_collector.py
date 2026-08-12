from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta
from typing import Any
import httpx

from automatic_input_builder import automatic_input
from feature_engineering import build_feature_record
from projection_engine import build_full_projection

BASE = "https://statsapi.mlb.com/api/v1"

def si(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default

def pd(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()

async def fetch(client: httpx.AsyncClient, path: str, params=None) -> dict:
    response = await client.get(BASE + path, params=params, timeout=45)
    response.raise_for_status()
    return response.json()

async def schedule(client, target_date: str) -> list[dict]:
    payload = await fetch(client, "/schedule", {"sportId": 1, "date": target_date, "hydrate": "team"})
    return [g for block in payload.get("dates", []) for g in block.get("games", []) if g.get("status", {}).get("abstractGameState") == "Final"]

async def boxscore(client, game_pk: int) -> dict:
    return await fetch(client, f"/game/{game_pk}/boxscore")

def actual_starters(game: dict, box: dict) -> list[dict]:
    out = []
    for side, opp in (("away", "home"), ("home", "away")):
        team = box.get("teams", {}).get(side, {})
        opponent = box.get("teams", {}).get(opp, {})
        for player in team.get("players", {}).values():
            pitching = player.get("stats", {}).get("pitching", {})
            if si(pitching.get("gamesStarted")) < 1:
                continue
            person = player.get("person", {})
            out.append({
                "game_pk": game.get("gamePk"),
                "game_date": game.get("officialDate"),
                "player": person.get("fullName"),
                "player_id": person.get("id"),
                "team_id": team.get("team", {}).get("id"),
                "team_name": team.get("team", {}).get("name"),
                "opponent_team_id": opponent.get("team", {}).get("id"),
                "opponent_team_name": opponent.get("team", {}).get("name"),
                "actual_strikeouts": si(pitching.get("strikeOuts")),
            })
    return out

async def person(client, player_id):
    payload = await fetch(client, f"/people/{player_id}")
    return (payload.get("people") or [{}])[0]

async def year_by_year(client, player_id):
    payload = await fetch(client, f"/people/{player_id}/stats", {"stats": "yearByYear", "group": "pitching"})
    return [s for block in payload.get("stats", []) for s in block.get("splits", [])]

async def game_log(client, player_id, season):
    payload = await fetch(client, f"/people/{player_id}/stats", {"stats": "gameLog", "group": "pitching", "season": season})
    return [s for block in payload.get("stats", []) for s in block.get("splits", [])]

async def team_hitting_as_of(client, team_id, season, cutoff):
    payload = await fetch(client, f"/teams/{team_id}/stats", {
        "stats": "byDateRange", "group": "hitting", "season": season,
        "startDate": date(season, 3, 1).strftime("%m/%d/%Y"),
        "endDate": cutoff.strftime("%m/%d/%Y"),
    })
    for block in payload.get("stats", []):
        if block.get("splits"):
            return block["splits"][0].get("stat", {})
    return {}

def aggregate(rows):
    return {
        "strikeOuts": sum(si(r.get("stat", {}).get("strikeOuts")) for r in rows),
        "battersFaced": sum(si(r.get("stat", {}).get("battersFaced")) for r in rows),
        "gamesStarted": sum(si(r.get("stat", {}).get("gamesStarted")) for r in rows),
    }

def prior_logs(rows, target):
    out = []
    for row in rows:
        row_date = row.get("date")
        stat = row.get("stat", {})
        if not row_date or pd(row_date) >= target:
            continue
        bf = si(stat.get("battersFaced")); pitches = si(stat.get("numberOfPitches"))
        if si(stat.get("gamesStarted")) >= 1 and 1 <= bf <= 45 and 1 <= pitches <= 150:
            out.append(row)
    return sorted(out, key=lambda x: x.get("date", ""))

def kp(stat):
    bf = si(stat.get("battersFaced"))
    return ((si(stat.get("strikeOuts")) / bf) if bf else 0.0, bf)

def recent(rows):
    rows = rows[-6:]
    k = sum(si(r.get("stat", {}).get("strikeOuts")) for r in rows)
    bf = sum(si(r.get("stat", {}).get("battersFaced")) for r in rows)
    return {
        "recent_k_pct": k / bf if bf else 0.0,
        "recent_batters_faced": bf,
        "recent_starts": len(rows),
        "recent_start_batters_faced": [si(r.get("stat", {}).get("battersFaced")) for r in rows],
        "recent_pitch_counts": [si(r.get("stat", {}).get("numberOfPitches")) for r in rows[-3:]],
    }

def workload(season_stat, logs):
    vals = [si(r.get("stat", {}).get("battersFaced")) for r in logs[-5:]]
    if vals:
        recent_avg = sum(vals) / len(vals)
        starts = si(season_stat.get("gamesStarted")); season_bf = si(season_stat.get("battersFaced"))
        season_avg = season_bf / starts if starts else recent_avg
        expected = recent_avg * 0.65 + season_avg * 0.35
        floor = max(10, min(vals) - 2); ceiling = min(34, max(vals) + 2)
        return round(max(floor, min(ceiling, expected)), 2), floor, ceiling
    return 22.0, 16, 28

def team_k(stat):
    pa = si(stat.get("plateAppearances")) or si(stat.get("atBats")) + si(stat.get("baseOnBalls")) + si(stat.get("hitByPitch")) + si(stat.get("sacFlies"))
    return si(stat.get("strikeOuts")) / pa if pa else 0.225

def historical_career(year_rows, season, current):
    prior = aggregate([r for r in year_rows if si(r.get("season")) < season])
    return {
        "strikeOuts": si(prior.get("strikeOuts")) + si(current.get("strikeOuts")),
        "battersFaced": si(prior.get("battersFaced")) + si(current.get("battersFaced")),
        "gamesStarted": si(prior.get("gamesStarted")) + si(current.get("gamesStarted")),
    }

async def build_record(client, starter):
    target = pd(starter["game_date"]); cutoff = target - timedelta(days=1); season = target.year
    p, years, logs, opponent = await asyncio.gather(
        person(client, starter["player_id"]),
        year_by_year(client, starter["player_id"]),
        game_log(client, starter["player_id"], season),
        team_hitting_as_of(client, starter["opponent_team_id"], season, cutoff),
    )
    logs = prior_logs(logs, target)
    season_stat = aggregate(logs)
    if si(season_stat.get("gamesStarted")) < 1:
        return None
    career_stat = historical_career(years, season, season_stat)
    sk, sbf = kp(season_stat); ck, cbf = kp(career_stat); rec = recent(logs)
    ebf, floor, ceiling = workload(season_stat, logs)
    raw = {
        **starter,
        "starter_confirmed": True,
        "pitcher_hand": p.get("pitchHand", {}).get("code", "R"),
        "season_k_pct": sk,
        "career_k_pct": ck or sk,
        "recent_k_pct": rec["recent_k_pct"] or sk,
        "season_batters_faced": sbf,
        "career_batters_faced": cbf,
        "recent_batters_faced": rec["recent_batters_faced"],
        "recent_starts": rec["recent_starts"],
        "recent_start_batters_faced": rec["recent_start_batters_faced"],
        "expected_batters_faced": ebf,
        "workload_floor": floor,
        "workload_ceiling": ceiling,
        "recent_pitch_counts": rec["recent_pitch_counts"],
        "opponent_lineup_k_pct": team_k(opponent),
        "league_k_pct": 0.225,
        "lineup_confirmed": False,
        "starter_role": "NORMAL",
        "velocity_change_mph": 0.0,
        "whiff_rate_change": 0.0,
        "pitch_mix_change_supported": False,
        "data_warnings": [],
    }
    projection = build_full_projection(automatic_input(raw))
    return {
        "player": starter["player"],
        "game_date": starter["game_date"],
        "actual_strikeouts": starter["actual_strikeouts"],
        "projected_strikeouts": projection["projected_strikeouts"],
        "ladder_probabilities": projection["ladder_probabilities"],
        "projection_details": projection,
        "features": build_feature_record(raw),
    }

async def collect_historical_starts(start_date: str, end_date: str, max_days: int = 14):
    start = pd(start_date); end = pd(end_date)
    if end < start:
        raise ValueError("end_date must be on or after start_date.")
    days = (end - start).days + 1
    if days > max_days:
        raise ValueError(f"Requested {days} days; maximum per run is {max_days}.")
    warnings = []
    async with httpx.AsyncClient(headers={"User-Agent": "KalshiTradingPlatform/1.0"}) as client:
        starters = []
        current = start
        while current <= end:
            for game in await schedule(client, current.isoformat()):
                starters.extend(actual_starters(game, await boxscore(client, game["gamePk"])))
            current += timedelta(days=1)
        semaphore = asyncio.Semaphore(4)
        async def guarded(starter):
            async with semaphore:
                try:
                    return await build_record(client, starter)
                except Exception as exc:
                    warnings.append(f"{starter.get('player')} {starter.get('game_date')}: {exc}")
                    return None
        records = await asyncio.gather(*(guarded(s) for s in starters))
    return [r for r in records if r is not None], warnings
