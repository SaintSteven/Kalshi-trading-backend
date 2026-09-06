import argparse, math
from pathlib import Path
import pandas as pd

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

def taker_fee(price, contracts=1):
    raw = 0.07 * contracts * price * (1-price)
    return math.ceil((raw - 1e-12) * 100) / 100

def max_drawdown(pnls):
    eq=0.0; peak=0.0; dd=0.0
    for x in pnls:
        eq += float(x)
        peak=max(peak,eq)
        dd=min(dd,eq-peak)
    return dd

def summarize(g):
    if not len(g):
        return pd.Series(dict(n=0,cost=0,fees=0,net_pnl=0,net_roi_pct=None,win_rate_pct=None,max_drawdown=0))
    cost=g.entry_price.sum(); fees=g.entry_fee.sum(); net=g.net_settlement_pnl.sum()
    return pd.Series(dict(n=len(g),cost=cost,fees=fees,net_pnl=net,
                          net_roi_pct=100*net/(cost+fees) if (cost+fees) else None,
                          win_rate_pct=100*g.settled_win.mean(),
                          max_drawdown=max_drawdown(g.sort_values('kickoff_dt').net_settlement_pnl)))

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--ledger',required=True); ap.add_argument('--out',required=True); ap.add_argument('--max-quote-age-sec',type=int,default=60); a=ap.parse_args()
    out=Path(a.out); out.mkdir(parents=True,exist_ok=True)
    d=pd.read_csv(a.ledger)
    d['kickoff_dt']=pd.to_datetime(d.kickoff_utc,utc=True,errors='coerce')
    d['season']=d['kickoff_dt'].dt.year

    # Coverage audit before rule selection.
    cov=(d.groupby(['season','prop_class'])
          .agg(unique_markets=('market_ticker','nunique'),rows=('market_ticker','size'),
               median_quote_age_sec=('quote_age_sec','median'),games=('event_ticker','nunique'))
          .reset_index())
    cov.to_csv(out/'season_prop_coverage.csv',index=False)
    season_cov=(d.groupby('season')
                 .agg(unique_markets=('market_ticker','nunique'),rows=('market_ticker','size'),games=('event_ticker','nunique'))
                 .reset_index().sort_values('season'))
    season_cov.to_csv(out/'season_coverage.csv',index=False)

    picked=[]
    for r in RULES:
        g=d[(d.prop_class==r['prop_class'])&(d.prop_subtype==r['prop_subtype'])&(d.side==r['side'])&(d.price_bucket==r['bucket'])&(d.entry_horizon==r['horizon'])].copy()
        g['rule']=r['rule']
        g=g[g.quote_age_sec<=a.max_quote_age_sec].copy()
        g=g.sort_values(['thesis_key','entry_price','market_ticker']).drop_duplicates(['rule','thesis_key'])
        picked.append(g)
    x=pd.concat(picked,ignore_index=True)
    if not len(x): raise SystemExit('No frozen-rule observations after QC')
    x['entry_fee']=x.entry_price.map(taker_fee)
    x['net_settlement_pnl']=x.settlement_pnl-x.entry_fee
    x.to_csv(out/'deep_history_frozen_ledger.csv',index=False)

    by_year=x.groupby(['rule','season'],dropna=False).apply(summarize,include_groups=False).reset_index()
    by_year.to_csv(out/'rule_season_summary.csv',index=False)
    overall=x.groupby('rule',dropna=False).apply(summarize,include_groups=False).reset_index()
    overall.to_csv(out/'rule_overall_summary.csv',index=False)

    # Robustness table: count seasons with adequate sample and positive net ROI.
    adequate=by_year[by_year.n>=25].copy()
    robustness=(adequate.groupby('rule')
                .agg(seasons_tested=('season','nunique'),positive_seasons=('net_roi_pct',lambda s:int((s>0).sum())),
                     total_n=('n','sum'),median_season_roi=('net_roi_pct','median'),worst_season_roi=('net_roi_pct','min'),best_season_roi=('net_roi_pct','max'))
                .reset_index())
    robustness['positive_season_share']=robustness.positive_seasons/robustness.seasons_tested
    robustness.to_csv(out/'robustness_summary.csv',index=False)

    primary=by_year[by_year.rule=='RECYDS_NO_40_49_T0.5'].sort_values('season')
    lines=['# NFL Market Efficiency v0.04 — Deep Historical Frozen-Rule Audit','',
           'This run applies the already-frozen v0.03.3 rules unchanged across every historical season with executable Kalshi candlesticks that can be mapped to a regular-season NFL kickoff.','',
           f'Quote-age QC: <= {a.max_quote_age_sec}s. One thesis per player-game/rule. Fee model unchanged from v0.03.3.','',
           '## Archive coverage by season','',season_cov.to_markdown(index=False),'',
           '## Primary receiving-yards NO rule by season','',primary.to_markdown(index=False) if len(primary) else 'No qualifying primary observations.','',
           '## Frozen-rule cross-season robustness','',robustness.to_markdown(index=False) if len(robustness) else 'No rule-season cells reached n>=25.','',
           'Interpretation guardrail: older seasons with sparse or structurally different market coverage should not be treated as equivalent to full modern seasons. No rules were changed based on these results.']
    (out/'SUMMARY.md').write_text('\n'.join(lines))
    print('\n'.join(lines))

if __name__=='__main__': main()
