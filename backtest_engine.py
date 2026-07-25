from collections import defaultdict
from backtest_metrics import mae, rmse, mean_error, brier_score, log_loss, calibration_buckets, expected_calibration_error
from backtest_models import BacktestResponse, LadderMetrics

def ladder_outcome(actual, threshold):
    return int(actual >= int(threshold.rstrip("+")))

def segment(rows, feature):
    groups = defaultdict(list)
    for row in rows:
        groups[str(row.features.get(feature))].append(row)
    output = {}
    for value, group in groups.items():
        actual = [r.actual_strikeouts for r in group]
        predicted = [r.projected_strikeouts for r in group]
        output[value] = {
            "observations": len(group),
            "mae": round(mae(actual,predicted),4),
            "rmse": round(rmse(actual,predicted),4),
            "mean_error": round(mean_error(actual,predicted),4),
        }
    return output

def run_backtest(request):
    rows = request.starts
    actual = [r.actual_strikeouts for r in rows]
    predicted = [r.projected_strikeouts for r in rows]
    warnings = []
    if len(rows) < 100:
        warnings.append("Fewer than 100 starts were supplied. Treat results as preliminary.")

    thresholds = sorted(
        {threshold for row in rows for threshold in row.ladder_probabilities},
        key=lambda value: int(value.rstrip("+")),
    )

    metrics = []
    calibration = {}
    for threshold in thresholds:
        probabilities = []
        outcomes = []
        for row in rows:
            if threshold in row.ladder_probabilities:
                probabilities.append(row.ladder_probabilities[threshold])
                outcomes.append(ladder_outcome(row.actual_strikeouts, threshold))
        metrics.append(LadderMetrics(
            threshold=threshold,
            observations=len(probabilities),
            brier_score=round(brier_score(probabilities,outcomes),5) if probabilities else None,
            log_loss=round(log_loss(probabilities,outcomes),5) if probabilities else None,
            calibration_error=round(expected_calibration_error(probabilities,outcomes),5) if probabilities else None,
        ))
        calibration[threshold] = calibration_buckets(probabilities,outcomes)

    n = len(rows)
    over = sum(1 for a,p in zip(actual,predicted) if p>a)
    under = sum(1 for a,p in zip(actual,predicted) if p<a)
    exact = sum(1 for a,p in zip(actual,predicted) if round(p)==a)

    candidates = [
        "pitcher_hand","lineup_confirmed","advanced_statcast_active",
        "sportsbook_consensus_active","weather_active","umpire_active"
    ]
    feature_segments = {
        feature: segment(rows, feature)
        for feature in candidates
        if any(feature in row.features for row in rows)
    }

    return BacktestResponse(
        model_version=request.model_version,
        observations=n,
        mae=round(mae(actual,predicted),5) if n else None,
        rmse=round(rmse(actual,predicted),5) if n else None,
        mean_error=round(mean_error(actual,predicted),5) if n else None,
        over_projection_rate=round(over/n,5) if n else None,
        under_projection_rate=round(under/n,5) if n else None,
        exact_projection_rate=round(exact/n,5) if n else None,
        ladder_metrics=metrics,
        calibration=calibration,
        feature_segments=feature_segments,
        warnings=warnings,
    )
