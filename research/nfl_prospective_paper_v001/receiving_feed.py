"""Read-only bridge for independently generated receiving probabilities.
No Kalshi price is ever used to generate a probability. Missing inputs fail closed.
"""
import argparse, json, math
from pathlib import Path
from datetime import datetime, timezone
import pandas as pd

COLUMNS=['market_ticker','game_id','player_id','threshold','model_version','generated_at','fair_yes','fair_no','projection','qc_status','qc_reason']

def build(markets, projections, mapping):
    out=[]
    for _,m in markets.iterrows():
        if m.get('prop_family')!='receiving_yards': continue
        ticker=str(m.market_ticker)
        row={k:'' for k in COLUMNS};row.update(market_ticker=ticker,game_id=m.get('game_id',''),qc_status='UNAVAILABLE',qc_reason='NO_VERIFIED_INDEPENDENT_PROJECTION')
        matches=mapping[mapping.market_ticker.eq(ticker)]
        if len(matches)!=1:
            row['qc_reason']='MARKET_MAPPING_MISSING_OR_AMBIGUOUS';out.append(row);continue
        x=matches.iloc[0];pid=str(x.player_id);threshold=float(x.threshold)
        row.update(player_id=pid,threshold=threshold)
        if str(x.game_id)!=str(m.game_id):
            row['qc_reason']='GAME_ID_MISMATCH';out.append(row);continue
        p=projections[(projections.game_id.astype(str)==str(m.game_id))&(projections.player_id.astype(str)==pid)&(pd.to_numeric(projections.threshold,errors='coerce')==threshold)]
        if len(p)!=1:
            row['qc_reason']='PROJECTION_MISSING_OR_AMBIGUOUS';out.append(row);continue
        p=p.iloc[0]
        try:
            fair=float(p.fair_yes);generated=pd.Timestamp(p.generated_at)
            if generated.tzinfo is None:raise ValueError('timestamp missing timezone')
            if not 0<=fair<=1 or not math.isfinite(fair):raise ValueError('invalid probability')
            if generated>pd.Timestamp.now(tz='UTC'):raise ValueError('future timestamp')
            if not str(p.model_version).strip():raise ValueError('missing model version')
            if str(p.get('qc_status',''))!='PASS':raise ValueError('upstream model QC not PASS')
            row.update(model_version=p.model_version,generated_at=generated.isoformat(),fair_yes=fair,fair_no=1-fair,projection=p.get('projection',''),qc_status='MODEL_AVAILABLE',qc_reason='INDEPENDENT_MODEL_RESEARCH_ONLY')
        except Exception as e:row['qc_reason']='INVALID_MODEL_INPUT: '+str(e)
        out.append(row)
    return pd.DataFrame(out,columns=COLUMNS)

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--markets',required=True);ap.add_argument('--projections',required=True);ap.add_argument('--mapping',required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
    markets=pd.read_csv(a.markets,dtype=str).fillna('')
    required=['game_id','player_id','threshold','fair_yes','model_version','generated_at','qc_status']
    if Path(a.projections).exists() and Path(a.mapping).exists():
        projections=pd.read_csv(a.projections,dtype=str).fillna('');mapping=pd.read_csv(a.mapping,dtype=str).fillna('')
        if not set(required).issubset(projections.columns):raise ValueError('Projection schema mismatch')
        if not {'market_ticker','game_id','player_id','threshold'}.issubset(mapping.columns):raise ValueError('Mapping schema mismatch')
    else:
        projections=pd.DataFrame(columns=required);mapping=pd.DataFrame(columns=['market_ticker','game_id','player_id','threshold'])
    out=build(markets,projections,mapping);Path(a.output).parent.mkdir(parents=True,exist_ok=True);out.to_csv(a.output,index=False)
    print(json.dumps({'markets':len(out),'model_available':int((out.qc_status=='MODEL_AVAILABLE').sum())}))
if __name__=='__main__':main()
