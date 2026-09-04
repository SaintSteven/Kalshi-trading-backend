import argparse
from pathlib import Path
import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.preprocessing import OneHotEncoder, StandardScaler
from sklearn.pipeline import Pipeline
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import brier_score_loss, log_loss

THRESHOLDS = [20,30,40,50,60,70,80,90,100,110,120,125,130,140,150]


def fair_col(t):
    return f"cal_p_{t}plus"


def make_long(v):
    rows=[]
    base_cols=['season','week','player_id','player','position','current_team','opponent',
               'actual_receiving_yards','projected_receiving_yards','games_used','role_certainty',
               'role_score','projected_team_attempts','target_share','projected_targets','catch_rate',
               'yards_per_reception','matchup_multiplier','team_changed_since_previous_game']
    for t in THRESHOLDS:
        c=fair_col(t)
        if c not in v.columns:
            continue
        x=v[base_cols].copy()
        x['threshold']=t
        x['distance_to_threshold']=x['projected_receiving_yards']-t
        x['v010_probability']=v[c].astype(float)
        x['actual_yes']=(x['actual_receiving_yards']>=t).astype(int)
        rows.append(x)
    return pd.concat(rows, ignore_index=True)


def metrics(y,p):
    p=np.clip(np.asarray(p,dtype=float),1e-6,1-1e-6)
    y=np.asarray(y,dtype=int)
    return {
        'n':len(y),
        'brier':brier_score_loss(y,p),
        'logloss':log_loss(y,p,labels=[0,1]),
        'mean_pred':float(p.mean()),
        'actual_rate':float(y.mean()),
        'calibration_gap_pp':float((p.mean()-y.mean())*100),
    }


def cal_bins(df, prob_col, model_name):
    d=df[['actual_yes',prob_col]].dropna().copy()
    d['bin']=pd.cut(d[prob_col], bins=np.arange(0,1.0001,.1), include_lowest=True)
    out=d.groupby('bin',observed=True).agg(
        n=('actual_yes','size'), predicted=(prob_col,'mean'), actual=('actual_yes','mean')
    ).reset_index()
    out['model']=model_name
    out['gap_pp']=(out.predicted-out.actual)*100
    return out


def slice_metrics(df, prob_col, model, field):
    outs=[]
    for val,g in df.groupby(field,dropna=False):
        if len(g)<25:
            continue
        z=metrics(g.actual_yes,g[prob_col])
        z.update(model=model, slice_field=field, slice_value=str(val))
        outs.append(z)
    return pd.DataFrame(outs)


def roi_table(g):
    if len(g)==0:
        return dict(n=0,cost=0,pnl=0,roi=np.nan,hit=np.nan)
    cost=g.entry_price.sum()
    pnl=np.where(g.actual_yes.eq(1),1-g.entry_price,-g.entry_price).sum()
    return dict(n=len(g),cost=cost,pnl=pnl,roi=pnl/cost if cost else np.nan,hit=g.actual_yes.mean())


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--input-dir',required=True)
    ap.add_argument('--output-dir',required=True)
    args=ap.parse_args()
    inp=Path(args.input_dir)
    out=Path(args.output_dir)
    out.mkdir(parents=True,exist_ok=True)

    v=pd.read_csv(inp/'nfl_receiving_v0_10_player_game_validation.csv')
    ledger=pd.read_csv(inp/'nfl_receiving_v0_10_historical_market_ledger.csv')
    long=make_long(v)

    # Strict temporal holdout: train on 2024, test on 2025.
    train=long[long.season==2024].copy()
    test=long[long.season==2025].copy()
    if train.empty or test.empty:
        raise SystemExit('Need both 2024 train and 2025 test validation rows.')

    num=['projected_receiving_yards','games_used','role_score','projected_team_attempts',
         'target_share','projected_targets','catch_rate','yards_per_reception',
         'matchup_multiplier','threshold','distance_to_threshold']
    cat=['position','role_certainty']
    pre=ColumnTransformer([
        ('num',Pipeline([('imp',SimpleImputer(strategy='median')),('scale',StandardScaler())]),num),
        ('cat',Pipeline([('imp',SimpleImputer(strategy='most_frequent')),
                         ('oh',OneHotEncoder(handle_unknown='ignore'))]),cat),
    ])
    logit=Pipeline([('pre',pre),('m',LogisticRegression(max_iter=1000,C=1.0))])
    logit.fit(train[num+cat],train.actual_yes)
    test['direct_logit_probability']=logit.predict_proba(test[num+cat])[:,1]

    # Nonlinear alternative. Kalshi price is deliberately excluded.
    tree_features=num+['position','role_certainty']
    tr=train[tree_features].copy()
    te=test[tree_features].copy()
    for c in ['position','role_certainty']:
        vals={v:i for i,v in enumerate(sorted(tr[c].dropna().astype(str).unique()))}
        tr[c]=tr[c].astype(str).map(vals).fillna(-1)
        te[c]=te[c].astype(str).map(vals).fillna(-1)
    med=tr.median(numeric_only=True)
    tr=tr.fillna(med)
    te=te.fillna(med)
    hgb=HistGradientBoostingClassifier(max_iter=180,learning_rate=.05,max_leaf_nodes=15,
                                       l2_regularization=1.0,random_state=7)
    hgb.fit(tr,train.actual_yes)
    test['direct_hgb_probability']=hgb.predict_proba(te)[:,1]

    summary=[]
    for col,name in [('v010_probability','v0.10 calibrated Monte Carlo'),
                     ('direct_logit_probability','v0.11 direct logistic'),
                     ('direct_hgb_probability','v0.11 direct HGB')]:
        z=metrics(test.actual_yes,test[col])
        z['model']=name
        z['scope']='2025_holdout_all_thresholds'
        summary.append(z)
    pd.DataFrame(summary).to_csv(out/'v011_model_comparison.csv',index=False)

    bins=pd.concat([
        cal_bins(test,'v010_probability','v0.10'),
        cal_bins(test,'direct_logit_probability','direct_logit'),
        cal_bins(test,'direct_hgb_probability','direct_hgb')
    ],ignore_index=True)
    bins.to_csv(out/'v011_calibration_bins.csv',index=False)

    slices=[]
    for col,name in [('v010_probability','v0.10'),('direct_logit_probability','direct_logit'),
                     ('direct_hgb_probability','direct_hgb')]:
        for fld in ['position','threshold','role_certainty']:
            slices.append(slice_metrics(test,col,name,fld))
    pd.concat(slices,ignore_index=True).to_csv(out/'v011_slice_metrics.csv',index=False)

    # Join 2025 market YES quotes to held-out predictions.
    yes=ledger[(ledger.season==2025)&(ledger.side=='YES')].copy()
    yes['threshold']=pd.to_numeric(yes.threshold,errors='coerce')
    pred=test[['season','week','player_id','threshold','actual_yes','v010_probability',
               'direct_logit_probability','direct_hgb_probability','position','role_certainty',
               'projected_receiving_yards','projected_targets','target_share','catch_rate',
               'yards_per_reception']].drop_duplicates(['season','week','player_id','threshold'])
    mk=yes.merge(pred,on=['season','week','player_id','threshold'],how='inner',suffixes=('','_pred'))
    mk['market_yes_probability']=mk['yes_ask_entry'].astype(float)

    disagreement=[]
    trading=[]
    for col,name in [('v010_probability','v0.10'),('direct_logit_probability','direct_logit'),
                     ('direct_hgb_probability','direct_hgb')]:
        mk[f'{name}_edge_pp']=(mk[col]-mk.market_yes_probability)*100
        d=mk.copy()
        d['edge_bucket']=pd.cut(d[f'{name}_edge_pp'],
            bins=[-1e9,0,3,6,10,15,1e9],labels=['<=0','0-3','3-6','6-10','10-15','15+'])
        for b,g in d.groupby('edge_bucket',observed=True):
            if len(g)<10:
                continue
            z=metrics(g.actual_yes,g[col])
            z.update(model=name,edge_bucket=str(b),
                     market_mean=float(g.market_yes_probability.mean()),
                     model_edge_pp=float((g[col]-g.market_yes_probability).mean()*100))
            disagreement.append(z)

        # Research-only naive YES selection at 3pp, before fees. Not production QC.
        cand=d[d[f'{name}_edge_pp']>=3].copy()
        for key,grp in [('ALL',cand),
                        ('TE',cand[cand.position=='TE']),
                        ('WR',cand[cand.position=='WR']),
                        ('RB',cand[cand.position=='RB'])]:
            r=roi_table(grp)
            r.update(model=name,slice=key)
            trading.append(r)
    pd.DataFrame(disagreement).to_csv(out/'v011_market_disagreement_calibration.csv',index=False)
    pd.DataFrame(trading).to_csv(out/'v011_research_only_yes_roi.csv',index=False)

    # Stability audit of the v0.10 slices that looked interesting.
    b=pd.read_csv(inp/'nfl_receiving_v0_10_market_level_backtest.csv')
    candidates={
        'TE_YES': b[(b.side=='YES')&(b.position=='TE')],
        'RB_NO': b[(b.side=='NO')&(b.position=='RB')],
        'NO_EDGE_6_TO_10': b[(b.side=='NO')&(b.edge_points>=6)&(b.edge_points<10)],
    }
    stab=[]
    for name,g in candidates.items():
        splits={
            'all':g,
            'weeks_1_9':g[g.week<=9],
            'weeks_10_plus':g[g.week>=10],
            'odd_weeks':g[g.week%2==1],
            'even_weeks':g[g.week%2==0],
        }
        for s,gg in splits.items():
            cost=gg.contract_cost.sum()
            pnl=gg.gross_pnl_per_contract.sum()
            stab.append(dict(candidate=name,split=s,n=len(gg),cost=cost,pnl=pnl,
                             roi=pnl/cost if cost else np.nan,
                             hit_rate=gg.side_won.mean() if len(gg) else np.nan))
    pd.DataFrame(stab).to_csv(out/'v011_candidate_stability.csv',index=False)

    comp=pd.DataFrame(summary).set_index('model')
    st=pd.DataFrame(stab)
    lines=['# NFL Receiving v0.11 Diagnostic','',
           'Train: 2024 player-games. Test: 2025 player-games. Kalshi price is never a model feature.','',
           '## Probability model comparison','',
           comp[['n','brier','logloss','mean_pred','actual_rate','calibration_gap_pp']].to_markdown(),
           '','## Candidate stability (gross, before fees)','',
           st.to_markdown(index=False),'',
           'Research only. No trades.']
    (out/'SUMMARY.md').write_text('\n'.join(lines))
    print('\n'.join(lines))


if __name__=='__main__':
    main()
