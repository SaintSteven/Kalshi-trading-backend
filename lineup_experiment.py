from __future__ import annotations
import asyncio, math
from datetime import timedelta
import httpx

from automatic_input_builder import automatic_input
from historical_backtest_collector import (
    actual_starters_from_boxscore, aggregate_pitching_rows, fetch_json,
    get_boxscore, get_person, get_pitching_game_log, get_schedule,
    get_team_hitting_as_of, get_year_by_year_pitching,
    historical_career_stat, k_pct, parse_date, prior_game_logs,
    recent_summary, safe_int, team_k_pct, workload,
)
from projection_engine import build_full_projection


def starting_lineup(boxscore: dict, side: str) -> list[int]:
    players = boxscore.get("teams", {}).get(side, {}).get("players", {})
    ordered = []
    for player in players.values():
        order = str(player.get("battingOrder") or "")
        pid = player.get("person", {}).get("id")
        if len(order) == 3 and order.endswith("00") and pid:
            ordered.append((int(order), pid))
    ordered.sort()
    return [pid for _, pid in ordered[:9]]


async def hitter_game_log(client, player_id: int, season: int) -> list[dict]:
    payload = await fetch_json(client, f"/people/{player_id}/stats", {
        "stats": "gameLog", "group": "hitting", "season": season,
    })
    return [s for block in payload.get("stats", []) for s in block.get("splits", [])]


def hitter_k_before(rows: list[dict], target_date):
    k = pa = 0
    for row in rows:
        row_date = row.get("date")
        if not row_date or parse_date(row_date) >= target_date:
            continue
        stat = row.get("stat", {})
        k += safe_int(stat.get("strikeOuts"))
        row_pa = safe_int(stat.get("plateAppearances")) or (
            safe_int(stat.get("atBats")) + safe_int(stat.get("baseOnBalls")) +
            safe_int(stat.get("hitByPitch")) + safe_int(stat.get("sacFlies"))
        )
        pa += row_pa
    return (k / pa if pa else 0.225), pa


async def lineup_k_before(client, player_ids: list[int], target_date):
    sem = asyncio.Semaphore(5)
    async def one(pid):
        async with sem:
            return hitter_k_before(await hitter_game_log(client, pid, target_date.year), target_date)
    results = await asyncio.gather(*(one(pid) for pid in player_ids), return_exceptions=True)
    weighted = total_pa = valid = 0
    for result in results:
        if isinstance(result, Exception):
            continue
        rate, pa = result
        if pa > 0:
            weighted += rate * pa
            total_pa += pa
            valid += 1
    return (weighted / total_pa if total_pa else 0.225), valid


async def pitcher_raw(client, starter: dict, opponent_rate: float, confirmed: bool):
    target = parse_date(starter["game_date"])
    cutoff = target - timedelta(days=1)
    season = target.year
    person, year_rows, logs, _ = await asyncio.gather(
        get_person(client, starter["player_id"]),
        get_year_by_year_pitching(client, starter["player_id"]),
        get_pitching_game_log(client, starter["player_id"], season),
        get_team_hitting_as_of(client, starter["opponent_team_id"], season, cutoff),
    )
    prior = prior_game_logs(logs, target)
    season_stat = aggregate_pitching_rows(prior)
    if safe_int(season_stat.get("gamesStarted")) < 1:
        return None
    career_stat = historical_career_stat(year_rows, season, season_stat)
    season_rate, season_bf = k_pct(season_stat)
    career_rate, career_bf = k_pct(career_stat)
    recent = recent_summary(prior)
    expected_bf, floor, ceiling = workload(season_stat, prior)
    return {
        **starter, "starter_confirmed": True,
        "pitcher_hand": person.get("pitchHand", {}).get("code", "R"),
        "season_k_pct": season_rate, "career_k_pct": career_rate or season_rate,
        "recent_k_pct": recent["recent_k_pct"] or season_rate,
        "season_batters_faced": season_bf, "career_batters_faced": career_bf,
        "recent_batters_faced": recent["recent_batters_faced"],
        "recent_starts": recent["recent_starts"],
        "recent_start_batters_faced": recent["recent_start_batters_faced"],
        "expected_batters_faced": expected_bf, "workload_floor": floor,
        "workload_ceiling": ceiling, "recent_pitch_counts": recent["recent_pitch_counts"],
        "opponent_lineup_k_pct": opponent_rate, "league_k_pct": 0.225,
        "lineup_confirmed": confirmed, "starter_role": "NORMAL",
        "velocity_change_mph": 0.0, "whiff_rate_change": 0.0,
        "pitch_mix_change_supported": False, "data_warnings": [],
    }


def calc(actual, predicted):
    if not actual:
        return {"mae": None, "rmse": None, "mean_error": None}
    n = len(actual)
    return {
        "mae": sum(abs(a-p) for a,p in zip(actual,predicted))/n,
        "rmse": math.sqrt(sum((a-p)**2 for a,p in zip(actual,predicted))/n),
        "mean_error": sum(p-a for a,p in zip(actual,predicted))/n,
    }


async def run_lineup_experiment(start_date: str, end_date: str, max_days: int = 2):
    start, end = parse_date(start_date), parse_date(end_date)
    if end < start:
        raise ValueError("end_date must be on or after start_date.")
    days = (end-start).days + 1
    if days > max_days:
        raise ValueError(f"Requested {days} days; maximum is {max_days}.")

    rows, warnings, skipped = [], [], 0
    async with httpx.AsyncClient(headers={"User-Agent": "KalshiTradingPlatform/1.1"}) as client:
        current = start
        while current <= end:
            for game in await get_schedule(client, current.isoformat()):
                try:
                    box = await get_boxscore(client, game["gamePk"])
                    starters = actual_starters_from_boxscore(game, box)
                    away_ids = starting_lineup(box, "away")
                    home_ids = starting_lineup(box, "home")
                    away_rate, away_valid = await lineup_k_before(client, away_ids, current)
                    home_rate, home_valid = await lineup_k_before(client, home_ids, current)
                    if away_valid < 7 or home_valid < 7:
                        skipped += len(starters)
                        continue
                    away_team_id = box["teams"]["away"]["team"]["id"]
                    for starter in starters:
                        is_away = starter["team_id"] == away_team_id
                        lineup_rate = home_rate if is_away else away_rate
                        opp_stat = await get_team_hitting_as_of(
                            client, starter["opponent_team_id"], current.year, current-timedelta(days=1)
                        )
                        baseline_rate = team_k_pct(opp_stat)
                        base_raw = await pitcher_raw(client, starter, baseline_rate, False)
                        line_raw = await pitcher_raw(client, starter, lineup_rate, True)
                        if not base_raw or not line_raw:
                            skipped += 1
                            continue
                        base_proj = build_full_projection(automatic_input(base_raw))
                        line_proj = build_full_projection(automatic_input(line_raw))
                        rows.append({
                            "actual": starter["actual_strikeouts"],
                            "baseline": base_proj["projected_strikeouts"],
                            "lineup": line_proj["projected_strikeouts"],
                        })
                except Exception as exc:
                    warnings.append(f"{current} game {game.get('gamePk')}: {exc}")
            current += timedelta(days=1)

    actual = [r["actual"] for r in rows]
    baseline = [r["baseline"] for r in rows]
    lineup = [r["lineup"] for r in rows]
    b, l = calc(actual, baseline), calc(actual, lineup)
    mae_change = l["mae"]-b["mae"] if b["mae"] is not None else None
    rmse_change = l["rmse"]-b["rmse"] if b["rmse"] is not None else None
    return {
        "records_collected": len(rows), "records_skipped": skipped,
        "comparison": {
            "observations": len(rows),
            "baseline_mae": round(b["mae"],5) if b["mae"] is not None else None,
            "lineup_mae": round(l["mae"],5) if l["mae"] is not None else None,
            "mae_change": round(mae_change,5) if mae_change is not None else None,
            "baseline_rmse": round(b["rmse"],5) if b["rmse"] is not None else None,
            "lineup_rmse": round(l["rmse"],5) if l["rmse"] is not None else None,
            "rmse_change": round(rmse_change,5) if rmse_change is not None else None,
            "baseline_mean_error": round(b["mean_error"],5) if b["mean_error"] is not None else None,
            "lineup_mean_error": round(l["mean_error"],5) if l["mean_error"] is not None else None,
            "improved": mae_change < 0 if mae_change is not None else None,
        },
        "warnings": warnings,
    }
