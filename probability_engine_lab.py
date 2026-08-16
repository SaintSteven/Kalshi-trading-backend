from __future__ import annotations

from collections import defaultdict
from math import sqrt
from edge_models import HistoricalMarketRecord

EPS = 1e-6


def _clip(p: float) -> float:
    return max(EPS, min(1.0-EPS, float(p)))


def _won(r: HistoricalMarketRecord) -> int:
    n = int(str(r.threshold).rstrip('+'))
    yes = int(r.actual_strikeouts >= n)
    return yes if r.side == 'YES' else 1-yes


def _market_p(r: HistoricalMarketRecord) -> float:
    return _clip(r.entry_price_cents / 100.0)


def _brier(rows, probs) -> float | None:
    if not rows: return None
    return sum((p-_won(r))**2 for r,p in zip(rows, probs))/len(rows)


def _logloss(rows, probs) -> float | None:
    from math import log
    if not rows: return None
    total=0.0
    for r,p in zip(rows, probs):
        p=_clip(p); y=_won(r)
        total += -(y*log(p)+(1-y)*log(1-p))
    return total/len(rows)


def _profit_summary(rows, probs, min_edge_points=5.0):
    bets=wins=0; risk=profit=0.0
    for r,p in zip(rows, probs):
        if p*100-r.entry_price_cents < min_edge_points: continue
        price=r.entry_price_cents/100.0
        stake=max(0.0,float(r.stake or 0))
        if stake<=0: continue
        won=_won(r); contracts=stake/price
        pnl=contracts*(1-price) if won else -stake
        bets+=1; wins+=won; risk+=stake; profit+=pnl
    return {
        'bets':bets,'wins':wins,'win_rate':wins/bets if bets else None,
        'risked':risk,'net_profit':profit,'roi':profit/risk if risk else None,
    }


def _calibration_buckets(rows, probs):
    groups=defaultdict(list)
    for r,p in zip(rows,probs):
        lo=min(90,int(p*10)*10); groups[f'{lo}-{lo+10}%'].append((r,p))
    out=[]
    for k, vals in sorted(groups.items(), key=lambda kv:int(kv[0].split('-')[0])):
        avg=sum(p for _,p in vals)/len(vals); actual=sum(_won(r) for r,_ in vals)/len(vals)
        out.append({'bucket':k,'bets':len(vals),'predicted':avg,'actual':actual,'error':actual-avg})
    return out


def _fit_affine(train):
    xs=[float(r.model_probability) for r in train]; ys=[_won(r) for r in train]
    if not xs:return (0.0,1.0)
    mx=sum(xs)/len(xs); my=sum(ys)/len(ys)
    den=sum((x-mx)**2 for x in xs)
    b=sum((x-mx)*(y-my) for x,y in zip(xs,ys))/den if den>0 else 0.0
    a=my-b*mx
    return a,b


def _fit_market_shrink(train):
    # p = market + alpha*(model-market), alpha fit by least squares to outcomes.
    ds=[]; zs=[]
    for r in train:
        m=_market_p(r); d=float(r.model_probability)-m
        ds.append(d); zs.append(_won(r)-m)
    den=sum(d*d for d in ds)
    alpha=sum(d*z for d,z in zip(ds,zs))/den if den>0 else 0.0
    # Avoid turning a reliability shrink into a market inversion.
    return max(0.0,min(1.0,alpha))


def _fit_projection_linear(train):
    rows=[r for r in train if r.projected_strikeouts is not None]
    if not rows:return (0.0,1.0,[])
    xs=[float(r.projected_strikeouts) for r in rows]; ys=[float(r.actual_strikeouts) for r in rows]
    mx=sum(xs)/len(xs); my=sum(ys)/len(ys)
    den=sum((x-mx)**2 for x in xs)
    b=sum((x-mx)*(y-my) for x,y in zip(xs,ys))/den if den>0 else 1.0
    a=my-b*mx
    residuals=[y-(a+b*x) for x,y in zip(xs,ys)]
    return a,b,residuals


def _empirical_threshold_probability(r, center, residuals):
    if not residuals:return float(r.model_probability)
    n=int(str(r.threshold).rstrip('+'))
    # Small continuity adjustment makes the discrete threshold less brittle.
    if r.side=='YES':
        needed=n-center
        return _clip(sum(1 for e in residuals if e >= needed)/len(residuals))
    needed=n-center
    return _clip(sum(1 for e in residuals if e < needed)/len(residuals))


def _candidate_probs(method, train, test):
    if method=='baseline':
        return [float(r.model_probability) for r in test], {'method':'Frozen v2.6.6 calibrated probability'}
    if method=='affine':
        a,b=_fit_affine(train)
        return [_clip(a+b*float(r.model_probability)) for r in test], {'intercept':a,'slope':b,'method':'Outcome affine calibration'}
    if method=='market_shrink':
        alpha=_fit_market_shrink(train)
        probs=[_clip(_market_p(r)+alpha*(float(r.model_probability)-_market_p(r))) for r in test]
        return probs, {'alpha':alpha,'method':'Shrink model disagreement toward executable market price'}
    if method=='projection_empirical':
        a,b,resids=_fit_projection_linear(train)
        probs=[]
        for r in test:
            if r.projected_strikeouts is None: probs.append(float(r.model_probability)); continue
            center=a+b*float(r.projected_strikeouts)
            probs.append(_empirical_threshold_probability(r,center,resids))
        return probs, {'intercept':a,'slope':b,'residual_observations':len(resids),'method':'Linear projection correction + empirical residual distribution'}
    raise ValueError(method)


def _evaluate(method, train, test, min_edge_points=5.0):
    probs, params=_candidate_probs(method,train,test)
    return {
        'method':method,'parameters':params,'test_bets':len(test),
        'avg_predicted':sum(probs)/len(probs) if probs else None,
        'actual_rate':sum(_won(r) for r in test)/len(test) if test else None,
        'brier':_brier(test,probs),'log_loss':_logloss(test,probs),
        'calibration':_calibration_buckets(test,probs),
        'simulated_5pt_edge':_profit_summary(test,probs,min_edge_points),
    }


def build_probability_lab(records: list[dict], minimum_edge_points: float=5.0):
    rows=[HistoricalMarketRecord(**r) for r in records]
    june=[r for r in rows if str(r.game_date).startswith('2026-06')]
    july=[r for r in rows if str(r.game_date).startswith('2026-07')]
    if not june or not july:
        raise ValueError('Probability Engine Lab requires both June and July 2026 diagnostic-capture records.')
    methods=['baseline','affine','market_shrink','projection_empirical']
    folds=[]
    for name,train,test in [('Fit June → Test July',june,july),('Fit July → Test June',july,june)]:
        results=[_evaluate(m,train,test,minimum_edge_points) for m in methods]
        folds.append({'fold':name,'train_bets':len(train),'test_bets':len(test),'results':results})
    summary=[]
    for m in methods:
        rs=[next(x for x in f['results'] if x['method']==m) for f in folds]
        summary.append({
            'method':m,
            'mean_brier':sum(x['brier'] for x in rs)/len(rs),
            'mean_log_loss':sum(x['log_loss'] for x in rs)/len(rs),
            'total_simulated_bets':sum(x['simulated_5pt_edge']['bets'] for x in rs),
            'total_risked':sum(x['simulated_5pt_edge']['risked'] for x in rs),
            'total_net_profit':sum(x['simulated_5pt_edge']['net_profit'] for x in rs),
            'combined_roi':(sum(x['simulated_5pt_edge']['net_profit'] for x in rs)/sum(x['simulated_5pt_edge']['risked'] for x in rs)) if sum(x['simulated_5pt_edge']['risked'] for x in rs)>0 else None,
        })
    best_brier=min(summary,key=lambda x:x['mean_brier'])['method']
    return {
        'version':'2.8.0','mode':'offline-cross-month-probability-lab',
        'records':len(rows),'june_records':len(june),'july_records':len(july),
        'minimum_edge_points':minimum_edge_points,
        'methods':{
            'baseline':'Frozen v2.6.6 selected-side probability',
            'affine':'Train-only affine outcome calibration',
            'market_shrink':'Train-only shrinkage of model disagreement toward Kalshi executable price',
            'projection_empirical':'Train-only linear projection correction plus empirical K-error distribution',
        },
        'folds':folds,'summary':summary,'best_mean_brier':best_brier,
        'guardrails':[
            'No coefficients are fit on the month being scored.',
            'June and July alternate as train/test folds.',
            'Confidence components are not used to manufacture probability in this lab.',
            'Simulated action requires candidate fair probability to exceed executable entry ask by at least the configured edge threshold.',
            'This is research-only; no candidate is promoted to live betting from these two folds alone.',
        ],
    }
