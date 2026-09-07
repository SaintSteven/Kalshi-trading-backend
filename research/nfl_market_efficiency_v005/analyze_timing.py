import argparse,math
from pathlib import Path
import pandas as pd

FROZEN_RULES=[
 {'rule_id':'PRIMARY_RECYDS_NO_40_49','prop_subtype':'receiving_yards','side':'no','lo':.40,'hi':.50},
 {'rule_id':'SEC_ANYTD_NO_50_59','prop_subtype':'anytime_td','side':'no','lo':.50,'hi':.60},
 {'rule_id':'SEC_REC_NO_15_19','prop_subtype':'receptions','side':'no','lo':.15,'hi':.20},
 {'rule_id':'SEC_REC_NO_20_29','prop_subtype':'receptions','side':'no','lo':.20,'hi':.30},
 {'rule_id':'SEC_RSHYDS_YES_10_14','prop_subtype':'rushing_yards','side':'yes','lo':.10,'hi':.15},
 {'rule_id':'SEC_RSHYDS_NO_15_19','prop_subtype':'rushing_yards','side':'no','lo':.15,'hi':.20},
 {'rule_id':'SEC_PASSYDS_NO_30_39','prop_subtype':'passing_yards','side':'no','lo':.30,'hi':.40},
]
WINDOW_ORDER=['T-48h','T-24h','GAME_MORNING_9ET','T-12h','T-6h','T-3h','T-1h','T-0.5h']


def fee(p):return math.ceil((.07*p*(1-p))*100-1e-12)/100

def max_dd(pnl):
    eq=0;peak=0;dd=0
    for x in pnl:
        eq+=x;peak=max(peak,eq);dd=min(dd,eq-peak)
    return dd

def summarize(g):
    if len(g)==0:return {}
    cost=g.entry_price.sum(); fees=g.entry_price.map(fee).sum(); gross=g.settlement_pnl.sum(); net=gross-fees
    order=g.sort_values(['kickoff_utc','market_ticker'])
    movement=order.movement_pnl_to_30m.dropna()
    return {'n':len(g),'avg_entry':g.entry_price.mean(),'win_rate':g.settled_win.mean(),'gross_pnl':gross,'modeled_fees':fees,'net_pnl':net,'net_roi_pct':100*net/(cost+fees) if cost+fees else None,'max_drawdown':max_dd((order.settlement_pnl-order.entry_price.map(fee)).tolist()),'movement_n':len(movement),'avg_move_to_t30_cents':100*movement.mean() if len(movement) else None,'positive_move_rate':(movement>0).mean() if len(movement) else None}

def dedup_one_thesis(df, midpoint):
    d=df.copy();d['dist']=(d.entry_price-midpoint).abs()
    return d.sort_values(['dist','quote_age_sec','market_ticker']).groupby(['entry_window','thesis_key'],as_index=False).head(1).drop(columns='dist')

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--root',required=True);ap.add_argument('--out',required=True);a=ap.parse_args()
    out=Path(a.out);out.mkdir(parents=True,exist_ok=True)
    files=list(Path(a.root).rglob('timing_ledger.csv'))
    if not files:raise SystemExit('No timing ledger files')
    df=pd.concat([pd.read_csv(f) for f in files],ignore_index=True)
    df=df[df.quote_age_sec<=60].copy()
    df['kickoff_utc']=pd.to_datetime(df.kickoff_utc,utc=True)
    df.to_csv(out/'combined_timing_ledger.csv',index=False)

    broad=[]
    for (prop,side,bucket,window),g in df.groupby(['prop_subtype','side','price_bucket','entry_window']):
        s=summarize(g);s.update({'prop_subtype':prop,'side':side,'price_bucket':bucket,'entry_window':window});broad.append(s)
    broad=pd.DataFrame(broad)
    if len(broad):broad.to_csv(out/'broad_timing_summary.csv',index=False)

    frozen_rows=[]; frozen_ledgers=[]
    for r in FROZEN_RULES:
        x=df[(df.prop_subtype==r['prop_subtype'])&(df.side==r['side'])&(df.entry_price>=r['lo'])&(df.entry_price<r['hi'])].copy()
        x=dedup_one_thesis(x,(r['lo']+r['hi'])/2)
        x['rule_id']=r['rule_id'];frozen_ledgers.append(x)
        for w,g in x.groupby('entry_window'):
            s=summarize(g);s.update({'rule_id':r['rule_id'],'prop_subtype':r['prop_subtype'],'side':r['side'],'price_band':f"{int(r['lo']*100)}-{int(r['hi']*100)-1}c",'entry_window':w});frozen_rows.append(s)
    fr=pd.DataFrame(frozen_rows)
    if len(fr):
        fr['window_rank']=fr.entry_window.map({w:i for i,w in enumerate(WINDOW_ORDER)})
        fr=fr.sort_values(['rule_id','window_rank']).drop(columns='window_rank');fr.to_csv(out/'frozen_rule_timing_summary.csv',index=False)
    if frozen_ledgers:pd.concat(frozen_ledgers,ignore_index=True).to_csv(out/'frozen_rule_timing_ledger.csv',index=False)

    eligible=fr[fr.n>=25].copy() if len(fr) else pd.DataFrame()
    best=[]
    if len(eligible):
        for rule,g in eligible.groupby('rule_id'):
            hold=g.sort_values(['net_roi_pct','n'],ascending=[False,False]).iloc[0]
            mv=g[g.movement_n>=25].sort_values(['avg_move_to_t30_cents','movement_n'],ascending=[False,False])
            m=mv.iloc[0] if len(mv) else None
            best.append({'rule_id':rule,'best_hold_window':hold.entry_window,'hold_n':int(hold.n),'hold_net_roi_pct':hold.net_roi_pct,'best_movement_entry_window':m.entry_window if m is not None else None,'movement_n':int(m.movement_n) if m is not None else 0,'avg_move_to_t30_cents':m.avg_move_to_t30_cents if m is not None else None})
    best=pd.DataFrame(best);best.to_csv(out/'best_window_by_frozen_rule.csv',index=False)

    lines=['# NFL Entry Timing Study v0.05','',f'Executable rows with quote age <=60s: **{len(df):,}**','', 'This study compares hold-to-settlement performance and executable movement to T-30m. It does not change any frozen price bands or sides. Game-day morning is 9:00 AM ET on the game date. One thesis per player/game/rule/window is retained using closest-to-band-midpoint, then freshest quote, then ticker. General taker fee is modeled as ceil-to-cent(0.07*P*(1-P)).','']
    if len(best):
        lines+=['## Best windows among frozen rules (minimum n=25)','',best.to_markdown(index=False,floatfmt='.3f'),'']
    if len(fr):
        lines+=['## Full frozen-rule timing table','',fr.to_markdown(index=False,floatfmt='.3f'),'']
    lines+=['## Interpretation guardrails','','- A high historical ROI is descriptive, not a guaranteed future edge.','- Compare sample sizes and drawdowns, not ROI alone.','- Movement results use executable entry ask versus executable T-30m bid; positive movement is not the same as settlement profitability.','- This is still mostly the partial 2025 Kalshi archive; it is not independent multi-season confirmation.','- The study should inform Week 1 timing, but should not mutate the frozen research rules after seeing 2026 outcomes.']
    (out/'SUMMARY.md').write_text('\n'.join(lines))

if __name__=='__main__':main()
