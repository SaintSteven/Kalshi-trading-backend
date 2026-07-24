from collections import defaultdict
from backtest_metrics import mae,rmse,mean_error,brier_score,log_loss,calibration_buckets,expected_calibration_error
from backtest_models import BacktestResponse,LadderMetrics

def outcome(actual,threshold): return int(actual>=int(threshold.rstrip("+")))

def segment(rows,feature):
    groups=defaultdict(list)
    for r in rows: groups[str(r.features.get(feature))].append(r)
    out={}
    for value,group in groups.items():
        a=[r.actual_strikeouts for r in group]; p=[r.projected_strikeouts for r in group]
        out[value]={"observations":len(group),"mae":round(mae(a,p),4),"rmse":round(rmse(a,p),4),"mean_error":round(mean_error(a,p),4)}
    return out

def run_backtest(request):
    rows=request.starts
    actual=[r.actual_strikeouts for r in rows]
    predicted=[r.projected_strikeouts for r in rows]
    warnings=[]
    if len(rows)<100: warnings.append("Fewer than 100 starts were supplied. Treat results as preliminary.")
    thresholds=sorted({t for r in rows for t in r.ladder_probabilities},key=lambda x:int(x.rstrip("+")))
    metrics=[]; calibration={}
    for t in thresholds:
        probs=[]; outs=[]
        for r in rows:
            if t in r.ladder_probabilities:
                probs.append(r.ladder_probabilities[t]); outs.append(outcome(r.actual_strikeouts,t))
        metrics.append(LadderMetrics(threshold=t,observations=len(probs),brier_score=round(brier_score(probs,outs),5) if probs else None,log_loss=round(log_loss(probs,outs),5) if probs else None,calibration_error=round(expected_calibration_error(probs,outs),5) if probs else None))
        calibration[t]=calibration_buckets(probs,outs)
    n=len(rows)
    over=sum(1 for a,p in zip(actual,predicted) if p>a)
    under=sum(1 for a,p in zip(actual,predicted) if p<a)
    exact=sum(1 for a,p in zip(actual,predicted) if round(p)==a)
    candidates=["pitcher_hand","lineup_confirmed","advanced_statcast_active","sportsbook_consensus_active","weather_active","umpire_active"]
    segments={f:segment(rows,f) for f in candidates if any(f in r.features for r in rows)}
    return BacktestResponse(model_version=request.model_version,observations=n,mae=round(mae(actual,predicted),5) if n else None,rmse=round(rmse(actual,predicted),5) if n else None,mean_error=round(mean_error(actual,predicted),5) if n else None,over_projection_rate=round(over/n,5) if n else None,under_projection_rate=round(under/n,5) if n else None,exact_projection_rate=round(exact/n,5) if n else None,ladder_metrics=metrics,calibration=calibration,feature_segments=segments,warnings=warnings)
