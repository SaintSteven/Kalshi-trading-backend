from __future__ import annotations

import math

from lineup_experiment import run_lineup_experiment


def _safe_change(value: float | None, baseline: float | None) -> float | None:
    if value is None or baseline is None:
        return None
    return value - baseline


def _summary(
    experiment: str,
    observations: int,
    mae: float | None,
    rmse: float | None,
    mean_error: float | None,
    baseline_mae: float | None,
    baseline_rmse: float | None,
    status: str,
) -> dict:
    mae_change = _safe_change(mae, baseline_mae)
    rmse_change = _safe_change(rmse, baseline_rmse)

    return {
        "experiment": experiment,
        "observations": observations,
        "mae": mae,
        "rmse": rmse,
        "mean_error": mean_error,
        "mae_change_vs_baseline": (
            round(mae_change, 5)
            if mae_change is not None
            else None
        ),
        "rmse_change_vs_baseline": (
            round(rmse_change, 5)
            if rmse_change is not None
            else None
        ),
        "improved_mae": (
            mae_change < 0
            if mae_change is not None
            else None
        ),
        "improved_rmse": (
            rmse_change < 0
            if rmse_change is not None
            else None
        ),
        "status": status,
    }


async def run_model_lab(
    start_date: str,
    end_date: str,
    max_days: int = 2,
) -> dict:
    lineup_result = await run_lineup_experiment(
        start_date,
        end_date,
        max_days,
    )

    comparison = lineup_result["comparison"]
    observations = comparison["observations"]

    baseline_mae = comparison["baseline_mae"]
    baseline_rmse = comparison["baseline_rmse"]
    baseline_mean_error = comparison["baseline_mean_error"]

    lineup_mae = comparison["lineup_mae"]
    lineup_rmse = comparison["lineup_rmse"]
    lineup_mean_error = comparison["lineup_mean_error"]

    experiments = [
        _summary(
            experiment="baseline_team_k_pct",
            observations=observations,
            mae=baseline_mae,
            rmse=baseline_rmse,
            mean_error=baseline_mean_error,
            baseline_mae=baseline_mae,
            baseline_rmse=baseline_rmse,
            status="ACTIVE_BASELINE",
        ),
        _summary(
            experiment="confirmed_lineup_k_pct",
            observations=observations,
            mae=lineup_mae,
            rmse=lineup_rmse,
            mean_error=lineup_mean_error,
            baseline_mae=baseline_mae,
            baseline_rmse=baseline_rmse,
            status=(
                "KEEP_CANDIDATE"
                if comparison["improved"]
                else "REJECT_CANDIDATE"
            ),
        ),
        _summary(
            experiment="statcast_quality",
            observations=0,
            mae=None,
            rmse=None,
            mean_error=None,
            baseline_mae=baseline_mae,
            baseline_rmse=baseline_rmse,
            status="NOT_YET_IMPLEMENTED",
        ),
        _summary(
            experiment="velocity_trend",
            observations=0,
            mae=None,
            rmse=None,
            mean_error=None,
            baseline_mae=baseline_mae,
            baseline_rmse=baseline_rmse,
            status="NOT_YET_IMPLEMENTED",
        ),
        _summary(
            experiment="weather",
            observations=0,
            mae=None,
            rmse=None,
            mean_error=None,
            baseline_mae=baseline_mae,
            baseline_rmse=baseline_rmse,
            status="NOT_YET_IMPLEMENTED",
        ),
        _summary(
            experiment="umpire",
            observations=0,
            mae=None,
            rmse=None,
            mean_error=None,
            baseline_mae=baseline_mae,
            baseline_rmse=baseline_rmse,
            status="NOT_YET_IMPLEMENTED",
        ),
    ]

    completed = [
        item
        for item in experiments
        if item["mae"] is not None and item["rmse"] is not None
    ]

    best_mae = (
        min(completed, key=lambda item: item["mae"])["experiment"]
        if completed
        else None
    )
    best_rmse = (
        min(completed, key=lambda item: item["rmse"])["experiment"]
        if completed
        else None
    )

    warnings = list(lineup_result.get("warnings", []))

    if observations < 100:
        warnings.append(
            "Fewer than 100 observations were collected. "
            "Treat model comparisons as preliminary."
        )

    return {
        "baseline_name": "baseline_team_k_pct",
        "records_collected": lineup_result["records_collected"],
        "records_skipped": lineup_result["records_skipped"],
        "experiments": experiments,
        "best_experiment_by_mae": best_mae,
        "best_experiment_by_rmse": best_rmse,
        "warnings": warnings,
    }
