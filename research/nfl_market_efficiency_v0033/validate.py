import argparse, math
from pathlib import Path
import pandas as pd

# Frozen after v0.03.2 discovery. Do not add/remove rules based on v0.03.3 outcomes.
RULES = [
    dict(rule='ANYTD_NO_50_59_T6', prop_class='player_tds', prop_subtype='anytime_td', side='no', bucket='50-59c', horizon='T-6h'),
    dict(rule='REC_NO_15_19_T0.5', prop_class='receptions', prop_subtype='receptions', side='no', bucket='15-19c', horizon='T-0.5h'),
    dict(rule='REC_NO_20_29_T3', prop_class='receptions', prop_subtype='receptions', side='no', bucket='20-29c', horizon='T-3h'),
    dict(rule='REC_NO_15_19_T3', prop_class='receptions', prop_subtype='receptions', side='no', bucket='15-19c', horizon='T-3h'),
    dict(rule='RUSH_YES_10_14_T3', prop_class='rushing_yards', prop_subtype='rushing_yards', side='yes', bucket='10-14c', horizon='T-3h'),
    dict(rule='RUSH_NO_15_19_T0.5', prop_class='rushing_yards', prop_subtype='rushing_yards', side='no', bucket='15-19c', horizon='T-0.5h'),
    dict(rule='RECYDS_NO_40_49_T0.5', prop_class='receiving_yards', prop_subtype='receiving_yards', side='no', bucket='40-49c', horizon='T-0.5h'),
    dict(rule='PASSYDS_NO_30_39_T6', prop_class='passing_yards', prop_subtype='passing_yards', side='no', bucket='30-39c', horizon='T-6h'),
]

# General Kalshi taker formula from fee schedule: ceil to next cent.
def taker_fee(price, contracts=1):
    raw = 0.07 * contracts * price * (1-price)
    return math.ceil((raw - 1e-12) * 100) / 100

def summarize(g):
    if not len(g):
        return pd.Series(dict(n=0,cost=0,gross_pnl=0,fees=0,net_pnl=0,gross_roi_pct=None,net_roi_pct=None,win_rate_pct=None))
    cost=g.entry_price.sum(); gross=g.settlement_pnl.sum(); fees=g.entry_fee.sum(); net=g.net_settlement_pnl.sum()
    return pd.Series(dict(n=len(g),cost=cost,gross_pnl=gross,fees=fees,net_pnl=net,
                          gross_roi_pct=100*gross/cost if cost else None,
                          net_roi_pct=100*net/(cost+fees) if (cost+fees) else None,
                          win_rate_pct=100*g.settled_win.mean()))

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--ledger',required=True); ap.add_argument('--out',required=True); ap.add_argument('--max-quote-age-sec',type=int,default=60); a=ap.parse_args()
    out=Path(a.out); out.mkdir(parents=True,exist_ok=True)
    d=pd.read_csv(a.ledger)
    d['kickoff_dt']=pd.to_datetime(d.kickoff_utc,utc=True,errors='coerce')
    picked=[]
    for r in RULES:
        g=d[(d.prop_class==r['prop_class'])&(d.prop_subtype==r['prop_subtype'])&(d.side==r['side'])&(d.price_bucket==r['bucket'])&(d.entry_horizon==r['horizon'])].copy()
        g['rule']=r['rule']
        # Strict executable quote freshness QC. v0.03.2 already requires both bid and ask present.
        g=g[g.quote_age_sec<=a.max_quote_age_sec].copy()
        # One thesis per player-game/rule. If multiple correlated ladders qualify, choose cheapest entry then ticker deterministically.
        g=g.sort_values(['thesis_key','entry_price','market_ticker']).drop_duplicates(['rule','thesis_key'])
        picked.append(g)
    x=pd.concat(picked,ignore_index=True) if picked else pd.DataFrame()
    if not len(x): raise SystemExit('No frozen-rule observations after QC')
    x['entry_fee']=x.entry_price.map(taker_fee)
    x['net_settlement_pnl']=x.settlement_pnl-x.entry_fee
    # v0.03.2 inspected 2025 already, so this is explicitly post-selection validation, not a pristine holdout.
    x['calendar_year']=x.kickoff_dt.dt.year
    # Within calendar 2025, split chronologically at median kickoff to stress stability without re-optimizing rules.
    y25=x[x.calendar_year==2025].copy()
    cutoff=y25.kickoff_dt.median() if len(y25) else pd.NaT
    x['validation_split']='other_years'
    if pd.notna(cutoff):
        x.loc[(x.calendar_year==2025)&(x.kickoff_dt<=cutoff),'validation_split']='2025_early'
        x.loc[(x.calendar_year==2025)&(x.kickoff_dt>cutoff),'validation_split']='2025_late'
    x.to_csv(out/'frozen_strategy_ledger.csv',index=False)
    overall=x.groupby('rule',dropna=False).apply(summarize,include_groups=False).reset_index().sort_values('net_roi_pct',ascending=False)
    overall.to_csv(out/'rule_summary.csv',index=False)
    bysplit=x.groupby(['rule','validation_split'],dropna=False).apply(summarize,include_groups=False).reset_index()
    bysplit.to_csv(out/'rule_split_summary.csv',index=False)
    byyear=x.groupby(['rule','calendar_year'],dropna=False).apply(summarize,include_groups=False).reset_index()
    byyear.to_csv(out/'rule_year_summary.csv',index=False)
    portfolio=x.groupby('validation_split',dropna=False).apply(summarize,include_groups=False).reset_index()
    portfolio.to_csv(out/'portfolio_split_summary.csv',index=False)
    # Conservative pass gate: >=75 observations overall, positive fee-aware ROI overall, and positive 2025-late ROI with >=25 observations.
    late=bysplit[bysplit.validation_split=='2025_late'][['rule','n','net_roi_pct']].rename(columns={'n':'late_n','net_roi_pct':'late_net_roi_pct'})
    gate=overall.merge(late,on='rule',how='left')
    gate['pass_gate']=(gate.n>=75)&(gate.net_roi_pct>0)&(gate.late_n.fillna(0)>=25)&(gate.late_net_roi_pct.fillna(-999)>0)
    gate.to_csv(out/'frozen_rule_gate.csv',index=False)
    lines=['# NFL Market Efficiency v0.03.3 — Frozen Strategy Validation','',
           'Rules were frozen from v0.03.2 before this run. No rule is added, removed, or tuned from v0.03.3 results.','',
           f'Quote-age QC: <= **{a.max_quote_age_sec}s**. One thesis per player-game/rule.','Fee model: one-contract taker entry fee = ceil(0.07 × P × (1-P)) to the next cent. Settlement itself has no modeled exit trade.','',
           '**Important:** 2025 was already examined in v0.03.2, so v0.03.3 is a post-selection robustness test, not a pristine untouched holdout. The 2025 early/late split is used as a chronological stress test.','',
           '## Frozen rule gate','',gate.to_markdown(index=False),'','Gate requires: n>=75 overall, positive fee-aware ROI overall, and 2025-late n>=25 with positive fee-aware ROI.','',
           '## Portfolio by chronological split','',portfolio.to_markdown(index=False)]
    (out/'SUMMARY.md').write_text('\n'.join(lines))
    print('\n'.join(lines))

if __name__=='__main__': main()
