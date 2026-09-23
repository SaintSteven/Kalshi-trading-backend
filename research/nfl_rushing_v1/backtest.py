import argparse, io, math, re, time, hashlib, base64, zlib
from pathlib import Path
import numpy as np, pandas as pd, requests
from sklearn.impute import SimpleImputer
from sklearn.linear_model import Ridge
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

SERIES='KXNFLRSHYDS'; BASE='https://external-api.kalshi.com/trade-api/v2'; SCHED='https://raw.githubusercontent.com/nflverse/nfldata/master/data/games.csv'
MODEL_VERSION='NFL-RUSH-RIDGE-NORMALSD-ISO-v1'; RESIDUAL_SD=17.499131191796593
CAL_SHA='8be61f9f4442b75fbacd335b0997d4ea8e5b412930fea99d98c12040deaa1086'
CAL_B64='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'
ALIASES={'LA':'LAR','STL':'LAR','OAK':'LV','SD':'LAC','JAC':'JAX','WSH':'WAS'}; VALID={'QB','RB','FB','WR'}
BASEF=['rushing_yards','carries','rushing_tds']; FEATURES=[]
for c in BASEF: FEATURES += [f'{c}_last',f'{c}_r3',f'{c}_r5']
FEATURES += ['yards_career_mean','yards_season_mean','season_games_before','career_games_before','week','pos_QB','pos_RB','pos_FB','pos_WR']
def normteam(x): x=str(x or '').upper(); return ALIASES.get(x,x)
def normname(x): return re.sub(r'[^a-z0-9]','',str(x or '').lower())
def get(url,params=None):
 for i in range(5):
  try:
   r=requests.get(url,params=params,timeout=30,headers={'User-Agent':'nfl-rush-historical-audit/1'}); r.raise_for_status(); return r.json()
  except Exception:
   if i==4: raise
   time.sleep(.75*2**i)
def calibration():
 raw=zlib.decompress(base64.b64decode(CAL_B64)).decode(); assert hashlib.sha256(raw.encode()).hexdigest()==CAL_SHA
 x=pd.read_csv(io.StringIO(raw)); return x.raw_prob.to_numpy(float),x.calibrated_prob.to_numpy(float)
def normal_sf(z): return .5*math.erfc(z/math.sqrt(2))
def prepare(d):
 d=d.copy(); d['team']=d.get('team',d.get('recent_team','')); d=d[d.position.astype(str).str.upper().isin(VALID)].copy(); d['season']=pd.to_numeric(d.season,errors='coerce'); d['week']=pd.to_numeric(d.week,errors='coerce'); d=d[d.season.notna()&d.week.notna()]; d.season=d.season.astype(int); d.week=d.week.astype(int)
 for c in BASEF: d[c]=pd.to_numeric(d.get(c),errors='coerce')
 d.rushing_yards=d.rushing_yards.fillna(0); d=d.sort_values(['player_id','season','week']).reset_index(drop=True); g=d.groupby('player_id',group_keys=False); gs=d.groupby(['player_id','season'],group_keys=False); d['career_games_before']=g.cumcount(); d['season_games_before']=gs.cumcount()
 for c in BASEF: d[f'{c}_last']=g[c].shift(1); d[f'{c}_r3']=g[c].transform(lambda s:s.shift(1).rolling(3,min_periods=1).mean()); d[f'{c}_r5']=g[c].transform(lambda s:s.shift(1).rolling(5,min_periods=1).mean())
 d['yards_career_mean']=g.rushing_yards.transform(lambda s:s.shift(1).expanding(min_periods=1).mean()); d['yards_season_mean']=gs.rushing_yards.transform(lambda s:s.shift(1).expanding(min_periods=1).mean())
 for p in VALID:d[f'pos_{p}']=(d.position.astype(str).str.upper()==p).astype(int)
 d['time_key']=d.season*100+d.week; return d
def fit(d,key):
 tr=d[(d.time_key<key)&(d.career_games_before>=2)&(d.season_games_before>=1)]; m=make_pipeline(SimpleImputer(strategy='median'),StandardScaler(),Ridge(alpha=25.0)); m.fit(tr[FEATURES],tr.rushing_yards); return m
def feat(pr,season,week):
 h=pr[(pr.season*100+pr.week)<season*100+week].sort_values(['season','week'])
 if not len(h): return None
 row={}; latest=h.iloc[-1]
 for c in BASEF:
  v=pd.to_numeric(h[c],errors='coerce'); row[f'{c}_last']=v.iloc[-1]; row[f'{c}_r3']=v.tail(3).mean(); row[f'{c}_r5']=v.tail(5).mean()
 row['yards_career_mean']=pd.to_numeric(h.rushing_yards,errors='coerce').mean(); sh=h[h.season==season]; row['yards_season_mean']=pd.to_numeric(sh.rushing_yards,errors='coerce').mean() if len(sh) else np.nan; row['season_games_before']=len(sh); row['career_games_before']=len(h); row['week']=week; pos=str(latest.position).upper()
 for p in VALID: row[f'pos_{p}']=int(pos==p)
 return row,pos,len(sh),len(h)
def hist_markets():
 rows=[]; cur=''
 while True:
  p={'limit':1000,'series_ticker':SERIES}
  if cur:p['cursor']=cur
  z=get(BASE+'/historical/markets',p); rows+=z.get('markets',[]) or []; cur=str(z.get('cursor') or ''); print('markets',len(rows))
  if not cur:return pd.DataFrame(rows)
def parse_threshold(r):
 for c in ('floor_strike','cap_strike'):
  x=pd.to_numeric(r.get(c),errors='coerce')
  if pd.notna(x) and x>0:return float(math.ceil(x)) if abs(x%1-.5)<1e-9 else float(x)
 m=re.search(r'-(\d+(?:\.\d+)?)$',str(r.get('ticker',''))); return float(math.ceil(float(m.group(1)))) if m else np.nan
def market_team(t):
 parts=str(t).upper().split('-'); part=parts[-2] if len(parts)>=4 else ''
 for x in sorted(['ARI','ATL','BAL','BUF','CAR','CHI','CIN','CLE','DAL','DEN','DET','GB','HOU','IND','JAX','KC','LV','LAC','LAR','MIA','MIN','NE','NO','NYG','NYJ','PHI','PIT','SEA','SF','TB','TEN','WAS'],key=len,reverse=True):
  if part.startswith(x):return x
 return ''
def schedule(seasons):
 g=pd.read_csv(SCHED); g=g[pd.to_numeric(g.season,errors='coerce').isin(seasons)]; out=[]
 for _,r in g.iterrows():
  if str(r.get('game_type','REG')).upper() not in ('REG','REGULAR'):continue
  k=pd.to_datetime(f"{r.gameday} {r.gametime if pd.notna(r.gametime) else '13:00'}").tz_localize('America/New_York').tz_convert('UTC'); out.append((int(r.season),int(r.week),normteam(r.away_team),normteam(r.home_team),k))
 return out
def dollar(v):
 try:x=float(v); return x/100 if x>1 else x
 except:return np.nan
def book(ticker,kick):
 end=int((kick-pd.Timedelta(minutes=30)).timestamp()); start=end-12*3600
 try:z=get(f'{BASE}/historical/markets/{ticker}/candlesticks',{'start_ts':start,'end_ts':end,'period_interval':60})
 except:return np.nan,np.nan
 for c in reversed(z.get('candlesticks',[]) or []):
  if pd.to_numeric(c.get('end_period_ts'),errors='coerce')>end:continue
  ya=dollar((c.get('yes_ask') or {}).get('close_dollars',(c.get('yes_ask') or {}).get('close'))); yb=dollar((c.get('yes_bid') or {}).get('close_dollars',(c.get('yes_bid') or {}).get('close')))
  if pd.notna(ya) or pd.notna(yb):return ya,(1-yb if pd.notna(yb) else np.nan)
 return np.nan,np.nan
def summary(df,label):
 if df.empty:return [label,0,0,0,0,0,np.nan]
 cost=df.entry_price.sum(); pnl=df.pnl.sum(); return [label,len(df),int(df.won.sum()),len(df)-int(df.won.sum()),cost,pnl,pnl/cost if cost else np.nan]
def main():
 ap=argparse.ArgumentParser(); ap.add_argument('--output-dir',default='nfl_rushing_backtest_v1_output'); a=ap.parse_args(); out=Path(a.output_dir); out.mkdir(parents=True,exist_ok=True)
 frames=[pd.read_csv(f'https://github.com/nflverse/nflverse-data/releases/download/stats_player/stats_player_week_{y}.csv',low_memory=False) for y in [2022,2023,2024,2025]]
 d=prepare(pd.concat(frames,ignore_index=True)); cx,cy=calibration(); sched=schedule([2024,2025]); hm=hist_markets(); hm.to_csv(out/'historical_markets_raw.csv',index=False); models={}; rec=[]
 for i,m in hm.iterrows():
  occ=pd.to_datetime(m.get('occurrence_datetime') or m.get('close_time'),utc=True,errors='coerce'); team=market_team(m.get('ticker')); thr=parse_threshold(m)
  if pd.isna(occ) or not team or pd.isna(thr) or not 10<=thr<=150:continue
  games=[x for x in sched if team in (x[2],x[3]) and abs((x[4]-occ).total_seconds())<=36*3600]
  if len(games)!=1:continue
  season,week,away,home,kick=games[0]; blob=normname(' '.join(str(m.get(c,'') or '') for c in ('title','subtitle','yes_sub_title','no_sub_title','rules_primary')))
  actuals=d[(d.season==season)&(d.week==week)&(d.team.map(normteam)==team)].copy(); actuals=actuals[actuals.apply(lambda r:normname(r.get('player_display_name','')) in blob,axis=1)]
  ids=actuals.player_id.astype(str).unique().tolist()
  if len(ids)!=1:continue
  pid=ids[0]; ar=actuals[actuals.player_id.astype(str)==pid].iloc[0]; tf=feat(d[d.player_id.astype(str)==pid],season,week)
  if tf is None:continue
  f,pos,sg,cg=tf
  if cg<2 or sg<1:continue
  key=season*100+week
  if key not in models:models[key]=fit(d,key)
  mu=max(0,float(models[key].predict(pd.DataFrame([f],columns=FEATURES))[0])); raw=normal_sf((thr-mu)/RESIDUAL_SD); fy=float(np.interp(raw,cx,cy)); ya,na=book(str(m.ticker),kick); actual=float(ar.rushing_yards or 0); ay=actual>=thr
  for side,price,fair,won in [('YES',ya,fy,ay),('NO',na,1-fy,not ay)]:
   if pd.isna(price) or not 0<price<1:continue
   edge=fair-price
   if edge<.03:continue
   rec.append({'season':season,'week':week,'player_id':pid,'player':ar.get('player_display_name',''),'position':pos,'team':team,'threshold':thr,'ticker':m.ticker,'kickoff_utc':kick,'side':side,'entry_price':price,'fair_probability':fair,'edge_points':edge*100,'projection':mu,'actual_rushing_yards':actual,'won':bool(won),'pnl':(1-price if won else -price)})
  if i%100==0:print('processed',i,'records',len(rec))
  time.sleep(.015)
 led=pd.DataFrame(rec); led.to_csv(out/'market_level_backtest.csv',index=False)
 if led.empty: print('NO RESULTS'); return
 best=led.sort_values(['ticker','edge_points'],ascending=[True,False]).groupby('ticker',as_index=False).head(1); thesis=best.sort_values(['season','week','player_id','edge_points'],ascending=[True,True,True,False]).groupby(['season','week','player_id'],as_index=False).head(1); thesis.to_csv(out/'player_thesis_backtest.csv',index=False)
 rows=[summary(led[led.side=='YES'],'YES_ALL'),summary(led[led.side=='NO'],'NO_ALL'),summary(thesis,'COMBINED_ONE_BET_PER_PLAYER_GAME'),summary(thesis[thesis.season==2025],'2025_HELDOUT_ONE_BET_PER_PLAYER_GAME')]; s=pd.DataFrame(rows,columns=['portfolio','bets','wins','losses','total_cost','gross_pnl','gross_roi']); s.to_csv(out/'profitability_summary.csv',index=False)
 thesis['edge_bucket']=pd.cut(thesis.edge_points,[-np.inf,6,10,15,np.inf],labels=['3-5.99pp','6-9.99pp','10-14.99pp','15pp+'],right=False); thesis['price_bucket']=pd.cut(thesis.entry_price,[0,.2,.4,.6,.8,1.01],labels=['<20c','20-39c','40-59c','60-79c','80c+'],right=False); thesis['threshold_bucket']=pd.cut(thesis.threshold,[-np.inf,30,60,90,np.inf],labels=['<=29','30-59','60-89','90+'],right=False)
 diag=[]
 for dim in ['side','season','edge_bucket','position','price_bucket','threshold_bucket']:
  for k,g in thesis.groupby(dim,observed=True):
   z=summary(g,f'{dim}={k}'); diag.append([dim,str(k)]+z[1:])
 pd.DataFrame(diag,columns=['dimension','group','bets','wins','losses','total_cost','gross_pnl','gross_roi']).to_csv(out/'diagnostics.csv',index=False)
 print(s.to_string(index=False)); print(pd.read_csv(out/'diagnostics.csv').to_string(index=False))
if __name__=='__main__':main()
