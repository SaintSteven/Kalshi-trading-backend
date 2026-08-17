"""MLB K Model v3 challenger research lab.

v3 is intentionally NOT a live model.  It evaluates a new architecture on the
full historical recommendation universe captured by v3.0.0:

    corrected K distribution -> selected-side baseball probability
    -> Kalshi executable-price prior -> posterior fair probability
    -> edge qualification

All fitting is train-only inside chronological walk-forward folds.
"""
from __future__ import annotations

from math import erf, exp, log, sqrt
from edge_models import HistoricalMarketRecord
from probability_engine_lab import _brier, _logloss, _market_p, _profit_summary, _won, _clip

EPS = 1e-6


def _month(rows, month):
    return [r for r in rows if str(r.game_date).startswith(month)]


def _fit_projection(train):
    rows=[r for r in train if r.projected_strikeouts is not None]
    if not rows:
        return 0.0,1.0,2.0
    xs=[float(r.projected_strikeouts) for r in rows]
    ys=[float(r.actual_strikeouts) for r in rows]
    mx=sum(xs)/len(xs); my=sum(ys)/len(ys)
    den=sum((x-mx)**2 for x in xs)
    slope=sum((x-mx)*(y-my) for x,y in zip(xs,ys))/den if den>0 else 1.0
    intercept=my-slope*mx
    residuals=[y-(intercept+slope*x) for x,y in zip(xs,ys)]
    sigma=sqrt(sum(e*e for e in residuals)/max(1,len(residuals)))
    sigma=max(0.75,min(4.0,sigma))
    return intercept,slope,sigma


def _normal_cdf(z):
    return 0.5*(1.0+erf(z/sqrt(2.0)))


def _baseball_probability(r, intercept, slope, sigma):
    if r.projected_strikeouts is None:
        return float(r.model_probability)
    n=int(str(r.threshold).rstrip('+'))
    center=intercept+slope*float(r.projected_strikeouts)
    # Continuity correction: actual strikeouts are integer-valued.
    yes=1.0-_normal_cdf((n-0.5-center)/sigma)
    return _clip(yes if r.side=='YES' else 1.0-yes)


def _logit(p):
    p=_clip(p)
    return log(p/(1.0-p))


def _sigmoid(x):
    if x>=0:
        z=exp(-x); return 1.0/(1.0+z)
    z=exp(x); return z/(1.0+z)


def _blend(baseball, market, market_weight):
    # Kalshi is treated as an informative prior rather than a price to subtract
    # only after the model has already declared certainty.
    return _clip(_sigmoid((1.0-market_weight)*_logit(baseball)+market_weight*_logit(market)))


def _fit_market_weight(train, baseball_probs):
    # One transparent scalar, constrained to [0,1], fit on train only.
    best=(1e9,0.5)
    for i in range(21):
        w=i/20.0
        probs=[_blend(pb,_market_p(r),w) for r,pb in zip(train,baseball_probs)]
        score=_brier(train,probs)
        if score is not None and score<best[0]:
            best=(score,w)
    return best[1]


def _evaluate_fold(name, train, test, edge):
    intercept,slope,sigma=_fit_projection(train)
    train_base=[_baseball_probability(r,intercept,slope,sigma) for r in train]
    market_weight=_fit_market_weight(train,train_base)
    test_base=[_baseball_probability(r,intercept,slope,sigma) for r in test]
    posterior=[_blend(pb,_market_p(r),market_weight) for r,pb in zip(test,test_base)]
    market=[_market_p(r) for r in test]
    baseline=[float(r.model_probability) for r in test]

    def block(label, probs):
        sim=_profit_summary(test,probs,edge)
        return {
            'method':label,
            'brier':_brier(test,probs),
            'log_loss':_logloss(test,probs),
            'avg_predicted':sum(probs)/len(probs) if probs else None,
            'actual_rate':sum(_won(r) for r in test)/len(test) if test else None,
            'simulated_edge':sim,
        }
    return {
        'fold':name,'train_records':len(train),'test_records':len(test),
        'fit':{'projection_intercept':intercept,'projection_slope':slope,'projection_sigma':sigma,'market_prior_weight':market_weight},
        'results':[block('frozen_v2',baseline),block('market_only',market),block('v3_baseball_distribution',test_base),block('v3_market_prior_posterior',posterior)],
    }


def build_v3_challenger_lab(records: list[dict], minimum_edge_points: float=5.0):
    rows=[HistoricalMarketRecord(**r) for r in records]
    months={m:_month(rows,m) for m in ['2026-04','2026-05','2026-06','2026-07']}
    missing=[m for m,v in months.items() if not v]
    if missing:
        raise ValueError('v3 Challenger Lab requires April-July full-universe records. Missing: '+', '.join(missing))
    specs=[
        ('Fit April → Test May',months['2026-04'],months['2026-05']),
        ('Fit Apr+May → Test June',months['2026-04']+months['2026-05'],months['2026-06']),
        ('Fit Apr+May+June → Test July',months['2026-04']+months['2026-05']+months['2026-06'],months['2026-07']),
    ]
    folds=[_evaluate_fold(n,tr,te,minimum_edge_points) for n,tr,te in specs]
    methods=['frozen_v2','market_only','v3_baseball_distribution','v3_market_prior_posterior']
    summary=[]
    for method in methods:
        rs=[next(x for x in f['results'] if x['method']==method) for f in folds]
        risk=sum(x['simulated_edge']['risked'] for x in rs); pnl=sum(x['simulated_edge']['net_profit'] for x in rs)
        summary.append({
            'method':method,
            'mean_brier':sum(x['brier'] for x in rs)/len(rs),
            'mean_log_loss':sum(x['log_loss'] for x in rs)/len(rs),
            'edge_bets':sum(x['simulated_edge']['bets'] for x in rs),
            'risked':risk,'net_profit':pnl,'roi':pnl/risk if risk else None,
            'positive_folds':sum(1 for x in rs if (x['simulated_edge']['roi'] or 0)>0),
        })
    return {
        'version':'3.0.0','mode':'full-universe-walk-forward-challenger','records':len(rows),
        'month_counts':{k:len(v) for k,v in months.items()},'minimum_edge_points':minimum_edge_points,
        'folds':folds,'summary':summary,
        'guardrails':[
            'v3 is a research challenger only; the production/frozen v2 benchmark is unchanged.',
            'Unlike earlier labs, v3 is evaluated on every captured evaluable recommendation, not only v2 qualifiers.',
            'Projection correction, residual uncertainty and Kalshi-prior weight are fit only on dates earlier than the scored month.',
            'Kalshi executable ask is treated as an informative prior; no ladder, price bucket or side is hand-filtered from observed ROI.',
            'A candidate is not eligible for paper/live promotion unless it improves calibration and produces positive trading results across multiple chronological folds with a meaningful bet count.',
        ],
    }
