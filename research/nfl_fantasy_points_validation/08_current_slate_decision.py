#!/usr/bin/env python3
"""FFPTS v1 current-slate decision pipeline.
Uses frozen historical model outputs + calibrated position residual distributions.
Kalshi prices are comparison-only and never model inputs. Research/paper only.
"""
import json
from pathlib import Path
import pandas as pd
R=Path('research/nfl_fantasy_points_validation'); O=R/'results'
model=json.loads((R/'FROZEN_MODEL_V1.json').read_text())
d=pd.read_csv(O/'00_walk_forward_predictions.csv'); d['resid']=d.actual_fp-d.pred_fp
m=pd.read_csv(O/'03_kalshi_fantasy_markets.csv')
latest=d.sort_values(['season','week']).groupby('player_name',as_index=False).tail(1)
L={str(r.player_name).lower():r for r in latest.itertuples()}
rows=[]
for x in m.itertuples():
 if pd.notna(getattr(x,'exclusion_reason',None)): continue
 name=str(x.matched_player).lower(); r=L.get(name)
 if r is None: continue
 proj=float(r.pred_fp); th=float(x.threshold); hist=d.loc[d.position==r.position,'resid'].dropna().to_numpy()
 fair=float(((hist>(th-proj)).sum()+.5)/(len(hist)+1))
 ask=float(x.yes_ask_probability) if pd.notna(x.yes_ask_probability) else None
 bid=float(x.yes_bid_probability) if pd.notna(x.yes_bid_probability) else None
 edge=fair-ask if ask is not None else None
 # Conservative v1 QC: core probabilities only; tail markets remain WATCH.
 tail=fair<.15 or fair>.85
 if ask is None: decision='PASS'; qc='NO_EXECUTABLE_QUOTE'
 elif tail: decision='WATCH'; qc='TAIL_CALIBRATION'
 elif edge>=.08: decision='PAPER'; qc='PASS'
 elif edge>=.03: decision='WATCH'; qc='MARGINAL_EDGE'
 else: decision='PASS'; qc='NO_EDGE'
 rows.append({'ticker':x.ticker,'player':x.kalshi_player_name,'position':r.position,'projection_fp':proj,'threshold':th,'fair_yes':fair,'yes_bid':bid,'yes_ask':ask,'edge_vs_ask':edge,'decision':decision,'qc':qc,'model':model.get('model_name','NFL-FFPTS-RIDGE-EMPIRICAL-v1')})
o=pd.DataFrame(rows).sort_values(['decision','edge_vs_ask'],ascending=[True,False]); o.to_csv(O/'08_current_slate_decisions.csv',index=False)
summary={'pipeline':'NFL FFPTS v1','research_only':True,'orders_placed':False,'frozen_model':model.get('model_name','NFL-FFPTS-RIDGE-EMPIRICAL-v1'),'supported_markets':len(o),'quotes_present':int(o.yes_ask.notna().sum()),'PAPER':int((o.decision=='PAPER').sum()),'WATCH':int((o.decision=='WATCH').sum()),'PASS':int((o.decision=='PASS').sum()),'method':'Frozen historical point projection plus empirical position residual threshold probability; Kalshi executable ask used only after fair probability is produced.','warning':'Current-slate v1 projection is the frozen latest-player projection path; no market-price tuning.'}
(O/'08_current_slate_summary.json').write_text(json.dumps(summary,indent=2)); print(json.dumps(summary,indent=2)); print(o.to_json(orient='records',indent=2))
if len(o)==0 or summary['quotes_present']!=len(o): raise SystemExit('Current-slate gate failed')
