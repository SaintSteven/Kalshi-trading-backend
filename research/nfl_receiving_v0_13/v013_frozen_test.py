import argparse
from pathlib import Path
import numpy as np
import pandas as pd

RULES = {
    'H0_NO_EDGE_6_10': lambda d: d['edge_points'].between(6,10,inclusive='left'),
    'H1_PLUS_TARGETS_7_11': lambda d: d['edge_points'].between(6,10,inclusive='left') & d['projected_targets'].between(7,11,inclusive='both'),
    'H2_PLUS_TARGET_SHARE_25_30': lambda d: d['edge_points'].between(6,10,inclusive='left') & d['target_share'].between(.25,.30,inclusive='both'),
    'H3_PLUS_PROJ_YARDS_60_90': lambda d: d['edge_points'].between(6,10,inclusive='left') & d['projected_receiving_yards'].between(60,90,inclusive='both'),
    'H4_PLUS_YPR_12_16': lambda d: d['edge_points'].between(6,10,inclusive='left') & d['yards_per_reception'].between(12,16,inclusive='both'),
    'H5_PLUS_2_OVERCONF_FLAGS': lambda d: d['edge_points'].between(6,10,inclusive='left') & (d['overconf_flag_count']>=2),
}

def find(root,name):
    hits=list(Path(root).rglob(name))
    if not hits: raise FileNotFoundError(name)
    return hits[0]

def prep(inp):
    led=pd.read_csv(find(inp,'nfl_receiving_v0_10_historical_market_ledger.csv'))
    val=pd.read_csv(find(inp,'nfl_receiving_v0_10_player_game_validation.csv'))
    vf=['season','week','player_id','projected_receiving_yards','target_share','projected_targets','yards_per_reception','catch_rate','matchup_multiplier','role_score']
    v=val[vf].drop_duplicates(['season','week','player_id'])
    d=led.merge(v,on=['season','week','player_id'],how='left')
    d=d[(d.side=='NO') & (d.backtest_decision=='PAPER') & d.actual_yes.notna()].copy()
    d['proj_minus_threshold']=d['projected_receiving_yards']-d['threshold']
    flags=pd.DataFrame({
        'targets_9_11': d['projected_targets'].between(9,11,inclusive='both'),
        'share_25_30': d['target_share'].between(.25,.30,inclusive='both'),
        'proj_75_90': d['projected_receiving_yards'].between(75,90,inclusive='both'),
        'proj_minus_20p': d['proj_minus_threshold']>20,
        'hist_hit_70p': d['history_hit_rate']>.70,
        'ypr_12_16': d['yards_per_reception'].between(12,16,inclusive='both'),
    })
    d['overconf_flag_count']=flags.sum(axis=1)
    return d

def one_per_player_game(d):
    if d.empty: return d.copy()
    x=d.sort_values(['season','week','player_id','edge_points','contract_cost'],ascending=[True,True,True,False,True])
    return x.drop_duplicates(['season','week','player_id'],keep='first')

def metrics(d):
    if d.empty: return {'n':0,'wins':0,'losses':0,'cost':0.,'pnl':0.,'roi':np.nan,'hit_rate':np.nan,'max_drawdown':np.nan}
    x=d.sort_values(['kickoff_utc','player','threshold']).copy()
    pnl=x['gross_pnl_per_contract'].astype(float); cost=x['contract_cost'].astype(float)
    cum=pnl.cumsum(); peak=cum.cummax().clip(lower=0); dd=cum-peak
    wins=int(x['side_won'].astype(bool).sum()); n=len(x)
    return {'n':n,'wins':wins,'losses':n-wins,'cost':cost.sum(),'pnl':pnl.sum(),'roi':pnl.sum()/cost.sum() if cost.sum() else np.nan,'hit_rate':wins/n,'max_drawdown':dd.min()}

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--input-dir',required=True); ap.add_argument('--output-dir',required=True); a=ap.parse_args()
    out=Path(a.output_dir); out.mkdir(parents=True,exist_ok=True)
    d=prep(a.input_dir); rows=[]
    for rule,fn in RULES.items():
        z=one_per_player_game(d[fn(d)].copy())
        for split,ss in [('FULL',z),('DISCOVERY_W1_9',z[z.week<=9]),('HOLDOUT_W10_PLUS',z[z.week>=10]),('ODD_WEEKS',z[z.week%2==1]),('EVEN_WEEKS',z[z.week%2==0])]:
            r={'rule':rule,'split':split}; r.update(metrics(ss)); rows.append(r)
    res=pd.DataFrame(rows); res.to_csv(out/'v013_rule_results.csv',index=False)
    promos=[]
    for rule in RULES:
        g=res[res.rule==rule].set_index('split'); disc=g.loc['DISCOVERY_W1_9']; hold=g.loc['HOLDOUT_W10_PLUS']; full=g.loc['FULL']
        passed=(disc.n>=20 and hold.n>=20 and full.n>=50 and disc.roi>0 and hold.roi>0 and hold.max_drawdown>=-10)
        promos.append({'rule':rule,'promote_to_2026_paper_candidate':passed,'discovery_n':disc.n,'discovery_roi':disc.roi,'holdout_n':hold.n,'holdout_roi':hold.roi,'full_n':full.n,'full_roi':full.roi,'holdout_max_drawdown':hold.max_drawdown})
    pr=pd.DataFrame(promos); pr.to_csv(out/'v013_promotion_gates.csv',index=False)
    full=res[res.split=='FULL'][['rule','n','pnl','roi','hit_rate','max_drawdown']]
    dh=res[res.split.isin(['DISCOVERY_W1_9','HOLDOUT_W10_PLUS'])][['rule','split','n','pnl','roi','hit_rate','max_drawdown']]
    lines=['# NFL Receiving v0.13 — Frozen QC Hypothesis Test','', 'Research only. Gross P/L before fees. One NO contract per player-game. Rules were specified from v0.12 findings before evaluating the chronological holdout.','', '## Full sample','',full.to_markdown(index=False),'','## Chronological discovery vs holdout','',dh.to_markdown(index=False),'','## Promotion gate','',pr.to_markdown(index=False),'','A rule passes only if discovery and holdout are both positive, discovery/holdout each have >=20 bets, full sample has >=50, and holdout max drawdown is no worse than -$10. Passing means 2026 PAPER candidate only, not real-money approval.']
    (out/'SUMMARY.md').write_text('\n'.join(lines)); print('\n'.join(lines))

if __name__=='__main__': main()
