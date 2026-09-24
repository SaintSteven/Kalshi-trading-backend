import pandas as pd, numpy as np, argparse
from pathlib import Path
def stats(g):
 c=g.entry_price.sum(); p=g.pnl.sum(); return pd.Series({'bets':len(g),'avg_fair':g.fair_probability.mean(),'avg_price':g.entry_price.mean(),'avg_edge_pp':g.edge_points.mean(),'actual_hit':g.won.mean(),'cal_error_pp':100*(g.won.mean()-g.fair_probability.mean()),'market_error_pp':100*(g.won.mean()-g.entry_price.mean()),'roi':p/c if c else np.nan})
def main():
 ap=argparse.ArgumentParser(); ap.add_argument('--input',required=True); ap.add_argument('--out',required=True); a=ap.parse_args(); d=pd.read_csv(a.input); o=Path(a.out); o.mkdir(parents=True,exist_ok=True)
 d['fair_bucket']=pd.cut(d.fair_probability,[0,.2,.3,.4,.5,.6,.7,.8,1.01],right=False); d['edge_bucket']=pd.cut(d.edge_points,[3,6,10,15,20,30,100],right=False); d['price_bucket']=pd.cut(d.entry_price,[0,.2,.4,.6,.8,1.01],right=False); d['threshold_bucket']=pd.cut(d.threshold,[0,30,60,90,200],right=False)
 outs=[]
 for dim in ['side','fair_bucket','edge_bucket','price_bucket','threshold_bucket','position']:
  z=d.groupby(dim,observed=True).apply(stats,include_groups=False).reset_index(); z.insert(0,'dimension',dim); z=z.rename(columns={dim:'group'}); outs.append(z)
 allz=pd.concat(outs,ignore_index=True); allz.to_csv(o/'calibration_diagnostics.csv',index=False)
 # threshold-distance proxy: z-score distance between threshold and projection using frozen SD
 d['z_distance']=(d.threshold-d.projection)/17.499131191796593; d['abs_z_bucket']=pd.cut(d.z_distance.abs(),[0,.5,1,1.5,2,10],right=False)
 zd=d.groupby(['side','abs_z_bucket'],observed=True).apply(stats,include_groups=False).reset_index(); zd.to_csv(o/'threshold_distance.csv',index=False)
 # Compare model vs market Brier/log loss on all candidate observations
 eps=1e-6; y=d.won.astype(float); f=d.fair_probability.clip(eps,1-eps); m=d.entry_price.clip(eps,1-eps)
 met=pd.DataFrame([{'n':len(d),'model_brier':np.mean((f-y)**2),'market_brier':np.mean((m-y)**2),'model_logloss':np.mean(-(y*np.log(f)+(1-y)*np.log(1-f))),'market_logloss':np.mean(-(y*np.log(m)+(1-y)*np.log(1-m))),'mean_claimed_edge_pp':d.edge_points.mean(),'mean_realized_model_edge_pp':100*np.mean(y-f)}])
 met.to_csv(o/'model_vs_market.csv',index=False)
 print(met.to_string(index=False)); print('\nCALIBRATION\n',allz.to_string(index=False)); print('\nDISTANCE\n',zd.to_string(index=False))
if __name__=='__main__':main()
