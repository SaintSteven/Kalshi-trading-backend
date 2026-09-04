import argparse, json, re, time
from pathlib import Path
import requests, pandas as pd

BASE='https://api.elections.kalshi.com/trade-api/v2'
UA={'User-Agent':'kalshi-nfl-market-efficiency-v0.02'}

def get(path, params=None):
    last=None
    for i in range(7):
        try:
            r=requests.get(BASE+path, params=params, headers=UA, timeout=45)
            if r.status_code==429 or 500 <= r.status_code < 600:
                last=RuntimeError(f'HTTP {r.status_code}: {r.text[:200]}')
                time.sleep(min(12,0.75*(2**i))); continue
            r.raise_for_status(); return r.json()
        except Exception as e:
            last=e; time.sleep(min(12,0.75*(2**i)))
    raise last or RuntimeError('request failed')

def all_sports_series():
    d=get('/series',{'category':'Sports','include_product_metadata':'true','include_volume':'true'})
    return d.get('series',[]) or []

def text_blob(s):
    return ' '.join([str(s.get('ticker','')),str(s.get('title','')),str(s.get('category','')),
                     ' '.join(map(str,s.get('tags',[]) or [])),json.dumps(s.get('product_metadata',{}) or {})]).lower()

def is_nfl(s):
    b=text_blob(s)
    return ('nfl' in b or 'national football league' in b) and not any(x in b for x in ['college football','ncaaf','cfb'])

def classify(s):
    b=text_blob(s)
    rules=[
      ('receiving_yards',['receiving yards','receiver yards','rec yds']),
      ('receptions',['receptions','catches']),
      ('rushing_yards',['rushing yards','rush yards']),
      ('passing_yards',['passing yards','pass yards']),
      ('pass_attempts',['pass attempts','passing attempts']),
      ('pass_completions',['completions','passes completed']),
      ('rush_attempts',['rush attempts','rushing attempts','carries']),
      ('passing_tds',['passing touchdowns','passing tds','touchdown passes']),
      ('player_tds',['player touchdowns','anytime touchdown','score a touchdown','touchdowns']),
      ('interceptions',['interceptions thrown','pass interceptions']),
      ('longest_reception',['longest reception']),
      ('longest_rush',['longest rush']),
    ]
    for name,terms in rules:
        if any(t in b for t in terms): return name
    if any(x in b for x in ['player','quarterback','receiver','running back','wide receiver','tight end']): return 'other_player_prop'
    return 'other_nfl'

def hist_markets(series):
    rows=[]; cur=''; pages=0
    while True:
        p={'limit':1000,'series_ticker':series}
        if cur:p['cursor']=cur
        d=get('/historical/markets',p); batch=d.get('markets',[]) or []
        rows.extend(batch); pages+=1
        cur=str(d.get('cursor') or '')
        if not cur: break
        time.sleep(.10)
    return rows,pages

def recent_settled_markets(series):
    rows=[]; cur=''; pages=0
    while True:
        p={'limit':1000,'series_ticker':series,'status':'settled'}
        if cur:p['cursor']=cur
        d=get('/markets',p); batch=d.get('markets',[]) or []
        rows.extend(batch); pages+=1
        cur=str(d.get('cursor') or '')
        if not cur: break
        time.sleep(.10)
    return rows,pages

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--out',default='nfl_market_efficiency_v002_output'); a=ap.parse_args()
    out=Path(a.out); out.mkdir(parents=True,exist_ok=True)
    sports=all_sports_series(); nfl=[s for s in sports if is_nfl(s)]
    pd.DataFrame(sports).to_json(out/'all_sports_series.json',orient='records',indent=2)
    series_rows=[]; market_rows=[]
    for i,s in enumerate(nfl,1):
        ticker=str(s.get('ticker','')); cls=classify(s)
        try:
            hm,hp=hist_markets(ticker)
            rm,rp=recent_settled_markets(ticker)
            seen={}
            for source,arr in [('historical',hm),('recent_live',rm)]:
                for m in arr:
                    mt=str(m.get('ticker',''))
                    if mt and mt not in seen: seen[mt]=(source,m)
            for mt,(source,m) in seen.items():
                market_rows.append({'series_ticker':ticker,'prop_class':cls,'source':source,
                                    'market_ticker':mt,'event_ticker':m.get('event_ticker'),
                                    'title':m.get('title'),'subtitle':m.get('subtitle'),
                                    'result':m.get('result'),'status':m.get('status'),
                                    'open_time':m.get('open_time'),'close_time':m.get('close_time'),
                                    'settlement_ts':m.get('settlement_ts'),'floor_strike':m.get('floor_strike'),
                                    'cap_strike':m.get('cap_strike'),'primary_participant_key':m.get('primary_participant_key')})
            series_rows.append({'series_ticker':ticker,'title':s.get('title'),'tags':'|'.join(map(str,s.get('tags',[]) or [])),
                                'prop_class':cls,'historical_markets':len(hm),'recent_settled_markets':len(rm),
                                'unique_settled_markets':len(seen),'historical_pages':hp,'recent_pages':rp,'status':'OK','error':''})
            print(f'[{i}/{len(nfl)}] {ticker} {cls}: historical={len(hm)} recent={len(rm)} unique={len(seen)}')
        except Exception as e:
            series_rows.append({'series_ticker':ticker,'title':s.get('title'),'tags':'|'.join(map(str,s.get('tags',[]) or [])),
                                'prop_class':cls,'historical_markets':0,'recent_settled_markets':0,'unique_settled_markets':0,
                                'historical_pages':0,'recent_pages':0,'status':'ERROR','error':repr(e)})
            print(f'ERROR {ticker}: {e}')
    sdf=pd.DataFrame(series_rows).sort_values(['unique_settled_markets','series_ticker'],ascending=[False,True])
    mdf=pd.DataFrame(market_rows)
    sdf.to_csv(out/'nfl_series_inventory.csv',index=False)
    mdf.to_csv(out/'nfl_market_inventory.csv',index=False)
    if len(mdf):
        class_summary=(mdf.groupby('prop_class').agg(series_count=('series_ticker','nunique'),market_count=('market_ticker','nunique')).reset_index().sort_values('market_count',ascending=False))
    else: class_summary=pd.DataFrame(columns=['prop_class','series_count','market_count'])
    class_summary.to_csv(out/'prop_class_summary.csv',index=False)
    # Cross-check the known receiving-yards series that previously returned ~10k archived markets.
    rec=sdf[sdf.series_ticker.astype(str).str.upper().eq('KXNFLRECYDS')]
    rec_count=int(rec.unique_settled_markets.iloc[0]) if len(rec) else 0
    lines=['# NFL Market Efficiency Scanner v0.02 — Archive Discovery','',
           f'Sports series returned: **{len(sports):,}**',f'NFL series discovered: **{len(nfl):,}**',
           f'Unique NFL settled markets inventoried: **{mdf.market_ticker.nunique() if len(mdf) else 0:,}**','',
           '## Prop-family inventory','',class_summary.to_markdown(index=False),'',
           '## Largest discovered NFL series','',sdf.head(30).to_markdown(index=False),'',
           f'Known receiving-yards cross-check (`KXNFLRECYDS`): **{rec_count:,} unique settled markets**.','',
           '**This version fixes v0.01 by discovering Sports/NFL series first and using `/historical/markets?series_ticker=...` with no `status` filter, plus recent settled live markets after the historical cutoff.**','',
           'No profitability claims are made here. The next stage uses this inventory to choose adequately sampled player-prop families, then reconstructs executable pregame YES asks and NO asks from historical candlesticks.']
    (out/'SUMMARY.md').write_text('\n'.join(lines)); print('\n'.join(lines))
    if rec_count < 5000:
        raise SystemExit(f'Archive discovery sanity check failed: KXNFLRECYDS only {rec_count} markets; expected thousands based on prior v0.10 archive retrieval.')
if __name__=='__main__': main()
