import argparse
from pathlib import Path
import pandas as pd

def roi(g,col):
 c=g.entry_price.sum();p=g[col].sum();return pd.Series({'n':len(g),'cost':c,'pnl':p,'roi_pct':100*p/c if c else None,'win_or_positive_rate':100*(g.settled_win.mean() if col=='settlement_pnl' else (g[col]>0).mean())})

def summarize(df,keys,col):
 x=df.dropna(subset=[col]).groupby(keys,dropna=False).apply(lambda g:roi(g,col),include_groups=False).reset_index()
 return x.sort_values('roi_pct',ascending=False)

def dedup(df):
 # deterministic one thesis/player-game exposure within each side/horizon/price bucket: choose quote nearest bucket median, then ticker.
 d=df.copy();d['_mid']=d.groupby(['price_bucket'])['entry_price'].transform('median');d['_dist']=(d.entry_price-d._mid).abs()
 return d.sort_values(['thesis_key','side','entry_horizon','price_bucket','_dist','market_ticker']).drop_duplicates(['thesis_key','side','entry_horizon','price_bucket']).drop(columns=['_mid','_dist'])

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--root',required=True);ap.add_argument('--out',required=True);a=ap.parse_args();root=Path(a.root);out=Path(a.out);out.mkdir(parents=True,exist_ok=True)
 led=[];err=[];done=[]
 for p in root.rglob('executable_ledger.csv'):
  try:led.append(pd.read_csv(p))
  except:pass
 for p in root.rglob('errors.csv'):
  try:
   x=pd.read_csv(p)
   if len(x):err.append(x)
  except:pass
 for p in root.rglob('SHARD_DONE.txt'):
  try:done.append(p.read_text().strip())
  except:pass
 if not led:raise SystemExit('No shard ledgers')
 d=pd.concat(led,ignore_index=True).drop_duplicates(['market_ticker','side','entry_horizon'])
 e=pd.concat(err,ignore_index=True) if err else pd.DataFrame(columns=['ticker','series_ticker','error'])
 d.to_csv(out/'executable_ledger.csv',index=False);e.to_csv(out/'errors.csv',index=False)
 keys=['prop_class','prop_subtype','series_ticker','side','entry_horizon']
 summarize(d,keys,'settlement_pnl').to_csv(out/'broad_settlement_summary.csv',index=False)
 summarize(d,keys+['price_bucket'],'settlement_pnl').to_csv(out/'price_bucket_settlement_summary.csv',index=False)
 summarize(d[d.entry_horizon!='T-0.5h'],keys+['price_bucket'],'movement_pnl_to_30m').to_csv(out/'price_bucket_movement_summary.csv',index=False)
 byseason=summarize(d,keys+['season','price_bucket'],'settlement_pnl');byseason.to_csv(out/'season_settlement_summary.csv',index=False)
 h=d[d.holdout_2025.astype(str).str.lower().isin(['true','1'])] if d.holdout_2025.dtype==object else d[d.holdout_2025==True]
 summarize(h,keys+['price_bucket'],'settlement_pnl').to_csv(out/'holdout_2025_settlement_summary.csv',index=False)
 summarize(h[h.entry_horizon!='T-0.5h'],keys+['price_bucket'],'movement_pnl_to_30m').to_csv(out/'holdout_2025_movement_summary.csv',index=False)
 dd=dedup(d);dd.to_csv(out/'one_thesis_ledger.csv',index=False)
 summarize(dd,keys+['price_bucket'],'settlement_pnl').to_csv(out/'one_thesis_settlement_summary.csv',index=False)
 summarize(dd[dd.entry_horizon!='T-0.5h'],keys+['price_bucket'],'movement_pnl_to_30m').to_csv(out/'one_thesis_movement_summary.csv',index=False)
 qc=d[['market_ticker','event_ticker','kickoff_utc','close_time','close_minus_kickoff_min','quote_age_sec']].copy();qc.to_csv(out/'timing_qc.csv',index=False)
 pos=summarize(d,keys+['price_bucket'],'settlement_pnl');pos=pos[(pos.n>=30)&(pos.roi_pct>0)].head(30)
 mov=summarize(d[d.entry_horizon!='T-0.5h'],keys+['price_bucket'],'movement_pnl_to_30m');mov=mov[(mov.n>=30)&(mov.roi_pct>0)].head(30)
 lines=['# NFL Market Efficiency Scanner v0.03.2 — Full Validation','',f'Executable rows: **{len(d):,}**',f'Unique markets: **{d.market_ticker.nunique():,}**',f'Candlestick errors: **{len(e):,}**',f'One-thesis rows: **{len(dd):,}**','', '## Methodology','- All five prop families retained: player TDs, receiving yards, receptions, rushing yards, passing yards.','- Player TDs split into Anytime TD, First TD, Multiple TD, and other TD series.','- Horizons are anchored to nflverse regular-season kickoff, not Kalshi close_time.','- August/preseason excluded using mapped kickoff date.','- Historical/live candlestick routing follows inventory source with endpoint fallback.','- Executable proxies: YES ask; NO ask = 1-YES bid; movement exits use T-30m executable bid.','- Fine price buckets from <5c through 90c+.','- 2025 is reported separately as a chronological holdout view.','- One-thesis summaries deterministically deduplicate correlated ladders within player/game/side/horizon/price-bucket.','- Gross before fees; discovery/validation research only.','', '## Positive settlement slices (n>=30)','',pos.to_markdown(index=False) if len(pos) else 'None.','', '## Positive movement-to-T-30m slices (n>=30)','',mov.to_markdown(index=False) if len(mov) else 'None.','', '## Timing QC','',f'Median close_time minus kickoff: **{d.close_minus_kickoff_min.median():.1f} minutes**',f'Median quote age: **{d.quote_age_sec.median():.0f} seconds**','', 'Do not promote a strategy from the discovery tables alone. Require consistency in the 2025 holdout, one-thesis results, adequate sample size, and fee-aware retesting before paper trading.','', '## Shard completion notes','']+done
 (out/'SUMMARY.md').write_text('\n'.join(lines))
 print('\n'.join(lines[:80]))
if __name__=='__main__':main()
