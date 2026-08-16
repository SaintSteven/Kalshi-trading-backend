from __future__ import annotations

from edge_models import HistoricalMarketRecord
from probability_engine_lab import _evaluate

METHODS = ["baseline", "affine", "market_shrink", "projection_empirical"]

def _month(rows, month):
    return [r for r in rows if str(r.game_date).startswith(month)]

def build_walk_forward_lab(records: list[dict], minimum_edge_points: float = 5.0):
    rows = [HistoricalMarketRecord(**r) for r in records]
    months = {m: _month(rows, m) for m in ["2026-04", "2026-05", "2026-06", "2026-07"]}
    missing = [m for m, rs in months.items() if not rs]
    if missing:
        raise ValueError("Walk-forward lab requires April, May, June and July 2026 diagnostic-capture records. Missing: " + ", ".join(missing))

    fold_specs = [
        ("Fit April → Test May", months["2026-04"], months["2026-05"]),
        ("Fit Apr+May → Test June", months["2026-04"] + months["2026-05"], months["2026-06"]),
        ("Fit Apr+May+June → Test July", months["2026-04"] + months["2026-05"] + months["2026-06"], months["2026-07"]),
    ]
    folds=[]
    for name, train, test in fold_specs:
        results=[_evaluate(m, train, test, minimum_edge_points) for m in METHODS]
        folds.append({"fold":name,"train_bets":len(train),"test_bets":len(test),"results":results})

    summary=[]
    for method in METHODS:
        rs=[next(x for x in fold["results"] if x["method"]==method) for fold in folds]
        risk=sum(x["simulated_5pt_edge"]["risked"] for x in rs)
        pnl=sum(x["simulated_5pt_edge"]["net_profit"] for x in rs)
        summary.append({
            "method":method,
            "mean_brier":sum(x["brier"] for x in rs)/len(rs),
            "mean_log_loss":sum(x["log_loss"] for x in rs)/len(rs),
            "total_simulated_bets":sum(x["simulated_5pt_edge"]["bets"] for x in rs),
            "total_risked":risk,
            "total_net_profit":pnl,
            "combined_roi":pnl/risk if risk else None,
            "positive_folds":sum(1 for x in rs if (x["simulated_5pt_edge"]["roi"] or 0)>0),
        })

    return {
        "version":"2.8.1",
        "mode":"extended-walk-forward-probability-lab",
        "records":len(rows),
        "month_counts":{k:len(v) for k,v in months.items()},
        "minimum_edge_points":minimum_edge_points,
        "folds":folds,
        "summary":summary,
        "best_mean_brier":min(summary,key=lambda x:x["mean_brier"])["method"],
        "guardrails":[
            "Every scored month is strictly later than the data used to fit its candidate transformation.",
            "May is tested from April only; June from April+May; July from April+May+June.",
            "No coefficients are fit on the month being scored.",
            "Confidence components remain research variables and are not used to manufacture probability.",
            "Simulated action requires candidate fair probability to exceed executable entry ask by at least the configured edge threshold.",
            "This lab is research-only; a candidate must improve calibration and trading results across multiple walk-forward folds before promotion.",
        ],
    }
