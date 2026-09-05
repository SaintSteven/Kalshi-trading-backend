import argparse
from pathlib import Path
import pandas as pd

def summarize(df,kind):
 out=[]
 if df.empty:return pd.DataFrame()
 keys=['prop_class','side','entry_horizon','price_bucket']
 for k,g in df.groupby(keys,dropna=False):
  pnl=g[kind].dropna();cost=g.loc[pnl.index,'entry_price']
  out.append(dict(zip(keys,k),n=len(pnl),pnl=pnl.sum(),cost=cost.sum(),roi=pnl.sum()/cost.sum() if cost.sum() else None,win_rate=(pnl>0).mean()))
 return pd.DataFrame(out)

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--root',required=True);ap.add_argument('--out',required=True);a=ap.parse_args()
 root=Path(a.root);out=Path(a.out);out.mkdir(parents=True,exist_ok=True)
 ledgers=[];errors=[]
 for p in root.rglob('executable_ledger.csv'):
  try:
   d=pd.read_csv(p)
   if len(d):ledgers.append(d)
  except Exception as e:print('ledger read error',p,e)
 for p in root.rglob('errors.csv'):
  try:
   d=pd.read_csv(p)
   if len(d):errors.append(d)
  except:pass
 if not ledgers:raise SystemExit('No shard ledgers found')
 df=pd.concat(ledgers,ignore_index=True);df=df.drop_duplicates(['market_ticker','side','entry_horizon'])
 edf=pd.concat(errors,ignore_index=True) if errors else pd.DataFrame(columns=['ticker','error'])
 df.to_csv(out/'executable_ledger.csv',index=False);edf.to_csv(out/'errors.csv',index=False)
 settle=summarize(df,'settlement_pnl');settle.to_csv(out/'settlement_summary.csv',index=False)
 move=summarize(df[df.entry_horizon!='T-0.5h'],'movement_pnl_to_30m');move.to_csv(out/'movement_summary.csv',index=False)
 broad=[]
 for typ,sd in [('SETTLEMENT',settle),('MOVEMENT_TO_30M',move)]:
  if len(sd):
   z=sd.groupby(['prop_class','side','entry_horizon']).agg(n=('n','sum'),pnl=('pnl','sum'),cost=('cost','sum')).reset_index();z['roi']=z.pnl/z.cost;z['test']=typ;broad.append(z)
 broad=pd.concat(broad,ignore_index=True) if broad else pd.DataFrame()
 broad.to_csv(out/'broad_summary.csv',index=False)
 lines=['# NFL Market Efficiency Scanner v0.03 — Parallel Executable Price Discovery','',f'Unique market-side-horizon rows: **{len(df):,}**',f'Candlestick errors: **{len(edf):,}**','', '**Prices are executable quote proxies from 1-minute candle closes: entry YES ask; entry NO ask = 1-YES bid; movement exits use the corresponding bid at T-30m. Settlement P/L is gross before fees.**','','## Broad results','',broad.sort_values('roi',ascending=False).to_markdown(index=False) if len(broad) else 'No results.','','Discovery only. Any promising slice must be frozen and retested chronologically with one thesis per player-game and fees before promotion.']
 (out/'SUMMARY.md').write_text('\n'.join(lines));print('\n'.join(lines))
if __name__=='__main__':main()
