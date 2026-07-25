from __future__ import annotations

import asyncio
import math
from datetime import timedelta

import httpx

from automatic_input_builder import automatic_input
from historical_backtest_collector import (
    actual_starters,
    aggregate,
    boxscore,
    fetch,
    game_log,
    historical_career,
    kp,
    pd,
    person,
    prior_logs,
    recent,
    schedule,
    si,
    team_hitting_as_of,
    team_k,
    workload,
    year_by_year,
)
from projection_engine import build_full_projection


def starting_lineup(boxscore_data: dict, side: str) -> list[int]:
    players = (
        boxscore_data.get("teams", {})
        .get(side, {})
        .get("players", {})
    )
    ordered: list[tuple[int, int]] = []

    for player in players.values():
        order = str(player.get("battingOrder") or "")
        player_id = player.get("person", {}).get("id")

        if len(order) == 3 and order.endswith("00") and player_id:
            ordered.append((int(order), player_id))

    ordered.sort()
    return [player_id for _, player_id in ordered[:9]]


async def hitter_game_log(
    client: httpx.AsyncClient,
    player_id: int,
    season: int,
) -> list[dict]:
    payload = await fetch(
        client,
        f"/people/{player_id}/stats",
        {
            "stats": "gameLog",
            "group": "hitting",
            "season": season,
        },
    )

    return [
        split
        for block in payload.get("stats", [])
        for split in block.get("splits", [])
    ]


def hitter_k_before(
    rows: list[dict],
    target_date,
) -> tuple[float, int]:
    strikeouts = 0
    plate_appearances = 0

    for row in rows:
        row_date = row.get("date")

        if not row_date or pd(row_date) >= target_date:
            continue

        stat = row.get("stat", {})
        strikeouts += si(stat.get("strikeOuts"))

        row_pa = si(stat.get("plateAppearances")) or (
            si(stat.get("atBats"))
            + si(stat.get("baseOnBalls"))
            + si(stat.get("hitByPitch"))
            + si(stat.get("sacFlies"))
        )
        plate_appearances += row_pa

    if plate_appearances <= 0:
        return 0.225, 0

    return strikeouts / plate_appearances, plate_appearances


async def lineup_k_before(
    client: httpx.AsyncClient,
    player_ids: list[int],
    target_date,
) -> tuple[float, int]:
    semaphore = asyncio.Semaphore(5)

    async def one(player_id: int):
        async with semaphore:
            logs = await hitter_game_log(
                client,
                player_id,
                target_date.year,
            )
            return hitter_k_before(logs, target_date)

    results = await asyncio.gather(
        *(one(player_id) for player_id in player_ids),
        return_exceptions=True,
    )

    weighted = 0.0
    total_pa = 0
    valid_hitters = 0

    for result in results:
        if isinstance(result, Exception):
            continue

        rate, plate_appearances = result

        if plate_appearances > 0:
            weighted += rate * plate_appearances
            total_pa += plate_appearances
            valid_hitters += 1

    if total_pa <= 0:
        return 0.225, valid_hitters

    return weighted / total_pa, valid_hitters


async def pitcher_raw(
    client: httpx.AsyncClient,
    starter: dict,
    opponent_rate: float,
    confirmed: bool,
) -> dict | None:
    target = pd(starter["game_date"])
    cutoff = target - timedelta(days=1)
    season = target.year

    player_info, year_rows, logs, _ = await asyncio.gather(
        person(client, starter["player_id"]),
        year_by_year(client, starter["player_id"]),
        game_log(client, starter["player_id"], season),
        team_hitting_as_of(
            client,
            starter["opponent_team_id"],
            season,
            cutoff,
        ),
    )

    previous_logs = prior_logs(logs, target)
    season_stat = aggregate(previous_logs)

    if si(season_stat.get("gamesStarted")) < 1:
        return None

    career_stat = historical_career(
        year_rows,
        season,
        season_stat,
    )
    season_rate, season_bf = kp(season_stat)
    career_rate, career_bf = kp(career_stat)
    recent_data = recent(previous_logs)
    expected_bf, floor, ceiling = workload(
        season_stat,
        previous_logs,
    )

    return {
        **starter,
        "starter_confirmed": True,
        "pitcher_hand": player_info.get(
            "pitchHand",
            {},
        ).get("code", "R"),
        "season_k_pct": season_rate,
        "career_k_pct": career_rate or season_rate,
        "recent_k_pct": (
            recent_data["recent_k_pct"] or season_rate
        ),
        "season_batters_faced": season_bf,
        "career_batters_faced": career_bf,
        "recent_batters_faced": recent_data[
            "recent_batters_faced"
        ],
        "recent_starts": recent_data["recent_starts"],
        "recent_start_batters_faced": recent_data[
            "recent_start_batters_faced"
        ],
        "expected_batters_faced": expected_bf,
        "workload_floor": floor,
        "workload_ceiling": ceiling,
        "recent_pitch_counts": recent_data[
            "recent_pitch_counts"
        ],
        "opponent_lineup_k_pct": opponent_rate,
        "league_k_pct": 0.225,
        "lineup_confirmed": confirmed,
        "starter_role": "NORMAL",
        "velocity_change_mph": 0.0,
        "whiff_rate_change": 0.0,
        "pitch_mix_change_supported": False,
        "data_warnings": [],
    }


def calculate_metrics(
    actual: list[int],
    predicted: list[float],
) -> dict:
    if not actual:
        return {
            "mae": None,
            "rmse": None,
            "mean_error": None,
        }

    observations = len(actual)

    return {
        "mae": sum(
            abs(a - p)
            for a, p in zip(actual, predicted)
        ) / observations,
        "rmse": math.sqrt(
            sum(
                (a - p) ** 2
                for a, p in zip(actual, predicted)
            )
            / observations
        ),
        "mean_error": sum(
            p - a
            for a, p in zip(actual, predicted)
        ) / observations,
    }


async def run_lineup_experiment(
    start_date: str,
    end_date: str,
    max_days: int = 2,
) -> dict:
    start = pd(start_date)
    end = pd(end_date)

    if end < start:
        raise ValueError(
            "end_date must be on or after start_date."
        )

    days = (end - start).days + 1

    if days > max_days:
        raise ValueError(
            f"Requested {days} days; maximum is {max_days}."
        )

    rows: list[dict] = []
    warnings: list[str] = []
    skipped = 0

    async with httpx.AsyncClient(
        headers={
            "User-Agent": "KalshiTradingPlatform/1.1.1"
        }
    ) as client:
        current = start

        while current <= end:
            games = await schedule(
                client,
                current.isoformat(),
            )

            for game in games:
                try:
                    game_boxscore = await boxscore(
                        client,
                        game["gamePk"],
                    )
                    starters = actual_starters(
                        game,
                        game_boxscore,
                    )

                    away_ids = starting_lineup(
                        game_boxscore,
                        "away",
                    )
                    home_ids = starting_lineup(
                        game_boxscore,
                        "home",
                    )

                    away_rate, away_valid = (
                        await lineup_k_before(
                            client,
                            away_ids,
                            current,
                        )
                    )
                    home_rate, home_valid = (
                        await lineup_k_before(
                            client,
                            home_ids,
                            current,
                        )
                    )

                    if away_valid < 7 or home_valid < 7:
                        skipped += len(starters)
                        continue

                    away_team_id = (
                        game_boxscore["teams"]["away"][
                            "team"
                        ]["id"]
                    )

                    for starter in starters:
                        is_away = (
                            starter["team_id"]
                            == away_team_id
                        )
                        lineup_rate = (
                            home_rate if is_away else away_rate
                        )

                        opponent_stat = await team_hitting_as_of(
                            client,
                            starter["opponent_team_id"],
                            current.year,
                            current - timedelta(days=1),
                        )
                        baseline_rate = team_k(
                            opponent_stat
                        )

                        baseline_raw = await pitcher_raw(
                            client,
                            starter,
                            baseline_rate,
                            False,
                        )
                        lineup_raw = await pitcher_raw(
                            client,
                            starter,
                            lineup_rate,
                            True,
                        )

                        if not baseline_raw or not lineup_raw:
                            skipped += 1
                            continue

                        baseline_projection = (
                            build_full_projection(
                                automatic_input(
                                    baseline_raw
                                )
                            )
                        )
                        lineup_projection = (
                            build_full_projection(
                                automatic_input(
                                    lineup_raw
                                )
                            )
                        )

                        rows.append(
                            {
                                "actual": starter[
                                    "actual_strikeouts"
                                ],
                                "baseline": baseline_projection[
                                    "projected_strikeouts"
                                ],
                                "lineup": lineup_projection[
                                    "projected_strikeouts"
                                ],
                            }
                        )

                except Exception as exc:
                    warnings.append(
                        f"{current} game "
                        f"{game.get('gamePk')}: {exc}"
                    )

            current += timedelta(days=1)

    actual = [row["actual"] for row in rows]
    baseline = [row["baseline"] for row in rows]
    lineup = [row["lineup"] for row in rows]

    baseline_metrics = calculate_metrics(
        actual,
        baseline,
    )
    lineup_metrics = calculate_metrics(
        actual,
        lineup,
    )

    mae_change = (
        lineup_metrics["mae"]
        - baseline_metrics["mae"]
        if baseline_metrics["mae"] is not None
        else None
    )
    rmse_change = (
        lineup_metrics["rmse"]
        - baseline_metrics["rmse"]
        if baseline_metrics["rmse"] is not None
        else None
    )

    return {
        "records_collected": len(rows),
        "records_skipped": skipped,
        "comparison": {
            "observations": len(rows),
            "baseline_mae": (
                round(baseline_metrics["mae"], 5)
                if baseline_metrics["mae"] is not None
                else None
            ),
            "lineup_mae": (
                round(lineup_metrics["mae"], 5)
                if lineup_metrics["mae"] is not None
                else None
            ),
            "mae_change": (
                round(mae_change, 5)
                if mae_change is not None
                else None
            ),
            "baseline_rmse": (
                round(baseline_metrics["rmse"], 5)
                if baseline_metrics["rmse"] is not None
                else None
            ),
            "lineup_rmse": (
                round(lineup_metrics["rmse"], 5)
                if lineup_metrics["rmse"] is not None
                else None
            ),
            "rmse_change": (
                round(rmse_change, 5)
                if rmse_change is not None
                else None
            ),
            "baseline_mean_error": (
                round(
                    baseline_metrics["mean_error"],
                    5,
                )
                if baseline_metrics[
                    "mean_error"
                ] is not None
                else None
            ),
            "lineup_mean_error": (
                round(
                    lineup_metrics["mean_error"],
                    5,
                )
                if lineup_metrics[
                    "mean_error"
                ] is not None
                else None
            ),
            "improved": (
                mae_change < 0
                if mae_change is not None
                else None
            ),
        },
        "warnings": warnings,
    }
