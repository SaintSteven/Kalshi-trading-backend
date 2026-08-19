"""v3.1 residual-edge research lab: can baseball features predict Kalshi residual error?"""
from math import exp, log
from edge_models import HistoricalMarketRecord
from probability_engine_lab import _brier,_logloss,_market_p,_profit_summary,_won,_clip
from v3_challenger_lab import _fit_projection,_baseball_probability

def _logit(p):
 p=_clip(p); return log(p/(1-p))
def _sigmoid(x): return 1/(1+exp(-max(-35,min(35,x))))
def _month(rows,m): return [r for r in rows if str(r.game_date).startswith(m)]

def _features(r, intercept, slope, sigma):
 m=_market_p(r); b=_baseball_probability(r,intercept,slope,sigma)
 # All features are available pregame. No realized outcome/ROI-derived buckets.
 vals=[1.0,_logit(b)-_logit(m)]
 for v in [r.confidence_skill,r.confidence_lineup,r.confidence_workload,r.confidence_stability,r.confidence_recent]:
  vals.append(((float(v)-75.0)/15.0) if v is not None else 0.0)
 return vals,b,m

def _fit_logistic(train, intercept, slope, sigma, l2=2.0, steps=500, lr=.04):
 X=[]; y=[]
 for r in train:
  x,_,_=_features(r,intercept,slope,sigma); X.append(x); y.append(float(_won(r)))
 w=[0.0]*len(X[0])
 for _ in range(steps):
  g=[0.0]*len(w)
  for x,yy in zip(X,y):
   p=_sigmoid(sum(a*b for a,b in zip(w,x))); e=p-yy
   for j in range(len(w)): g[j]+=e*x[j]
  n=max(1,len(X))
  for j in range(len(w)):
   reg=0 if j==0 else l2*w[j]
   w[j]-=lr*(g[j]+reg)/n
 return w

def _predict(r,w,intercept,slope,sigma):
 x,b,m=_features(r,intercept,slope,sigma)
 return _clip(_sigmoid(sum(a*z for a,z in zip(w,x)))),b,m

def _eval(name,train,test,edge):
 a,b,s=_fit_projection(train); w=_fit_logistic(train,a,b,s)
 pred=[]; base=[]; market=[]; frozen=[]
 for r in test:
  p,pb,pm=_predict(r,w,a,b,s); pred.append(p);base.append(pb);market.append(pm);frozen.append(float(r.model_probability))
 def block(label,probs):
  sim=_profit_summary(test,probs,edge)
  return {'method':label,'brier':_brier(test,probs),'log_loss':_logloss(test,probs),'avg_predicted':sum(probs)/len(probs),'actual_rate':sum(_won(r) for r in test)/len(test),'simulated_edge':sim}
 return {'fold':name,'train_records':len(train),'test_records':len(test),'fit':{'projection_sigma':s,'coefficients':w},'results':[block('market_only',market),block('frozen_v2',frozen),block('v3_baseball_distribution',base),block('v31_residual_edge',pred)]}

def build_v31_residual_edge_lab(records,minimum_edge_points=5.0):
 rows=[HistoricalMarketRecord(**r) for r in records]
 ms={m:_month(rows,m) for m in ['2026-04','2026-05','2026-06','2026-07']}
 if any(not v for v in ms.values()): raise ValueError('v3.1 requires April-July full-universe records.')
 folds=[_eval('Fit April → Test May',ms['2026-04'],ms['2026-05'],minimum_edge_points),_eval('Fit Apr+May → Test June',ms['2026-04']+ms['2026-05'],ms['2026-06'],minimum_edge_points),_eval('Fit Apr+May+June → Test July',ms['2026-04']+ms['2026-05']+ms['2026-06'],ms['2026-07'],minimum_edge_points)]
 methods=['market_only','frozen_v2','v3_baseball_distribution','v31_residual_edge']; summary=[]
 for m in methods:
  rs=[next(x for x in f['results'] if x['method']==m) for f in folds]; risk=sum(x['simulated_edge']['risked'] for x in rs); pnl=sum(x['simulated_edge']['net_profit'] for x in rs)
  summary.append({'method':m,'mean_brier':sum(x['brier'] for x in rs)/3,'mean_log_loss':sum(x['log_loss'] for x in rs)/3,'edge_bets':sum(x['simulated_edge']['bets'] for x in rs),'risked':risk,'net_profit':pnl,'roi':pnl/risk if risk else None,'positive_folds':sum(1 for x in rs if (x['simulated_edge']['roi'] or 0)>0)})
 return {'version':'3.1.0','mode':'residual-edge-walk-forward','records':len(rows),'month_counts':{k:len(v) for k,v in ms.items()},'minimum_edge_points':minimum_edge_points,'folds':folds,'summary':summary,'guardrails':['Research only; no production/live model is changed.','Kalshi executable ask is the baseline. v3.1 must add predictive value beyond the market, not merely reproduce it.','Only pregame/no-lookahead features are used. Realized projection error and ROI-derived ladder/price filters are excluded.','All fitting is train-only in strict chronological folds.','Promotion requires better calibration than Kalshi, positive aggregate ROI, positive ROI in at least 2/3 folds, and meaningful trade volume.']}
