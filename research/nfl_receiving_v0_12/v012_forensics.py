import argparse
from pathlib import Path
import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler
from sklearn.metrics import brier_score_loss, log_loss

FEATURES_NUM = [
    "projected_receiving_yards","games_used","role_score","projected_team_attempts",
    "target_share","projected_targets","catch_rate","yards_per_reception",
    "matchup_multiplier","history_games","history_hit_rate","threshold",
    "proj_minus_threshold","market_mid_yes","disagree_pp",
]
FEATURES_CAT = ["position","role_certainty","threshold_normalization"]

def find_file(root, name):
    hits=list(Path(root).rglob(name))
    if not hits:
        raise FileNotFoundError(name)
    return hits[0]

def make_base(input_dir):
    ledger=pd.read_csv(find_file(input_dir,"nfl_receiving_v0_10_historical_market_ledger.csv"))
    val=pd.read_csv(find_file(input_dir,"nfl_receiving_v0_10_player_game_validation.csv"))
    vf=["season","week","player_id","projected_receiving_yards","games_used","role_score",
        "projected_team_attempts","target_share","projected_targets","catch_rate",
        "yards_per_reception","matchup_multiplier"]
    v=val[vf].drop_duplicates(["season","week","player_id"])
    df=ledger.merge(v,on=["season","week","player_id"],how="left")
    m=df[(df["side"]=="YES") & df["actual_yes"].notna() &
         df["yes_ask_entry"].notna() & df["no_ask_entry"].notna()].copy()
    m["yes_bid_entry"]=1-m["no_ask_entry"]
    m["market_mid_yes"]=(m["yes_ask_entry"]+m["yes_bid_entry"])/2
    m["disagree_pp"]=(m["fair_yes_probability"]-m["market_mid_yes"])*100
    m["proj_minus_threshold"]=m["projected_receiving_yards"]-m["threshold"]
    m["model_brier_row"]=(m["fair_yes_probability"]-m["actual_yes"])**2
    m["market_brier_row"]=(m["market_mid_yes"]-m["actual_yes"])**2
    m["model_beats_market"]=(m["model_brier_row"]<m["market_brier_row"]).astype(int)
    return m

def bucket_report(df, col, bins, min_n=20):
    z=df.copy()
    z["bucket"]=pd.cut(z[col],bins=bins,include_lowest=True)
    g=(z.groupby("bucket",observed=True)
       .agg(n=("actual_yes","size"), model_pred=("fair_yes_probability","mean"),
            market_ref=("market_mid_yes","mean"), actual_rate=("actual_yes","mean"),
            mean_disagree_pp=("disagree_pp","mean"), model_brier=("model_brier_row","mean"),
            market_brier=("market_brier_row","mean"), model_win_rate=("model_beats_market","mean"))
       .reset_index())
    g["model_cal_gap_pp"]=(g["model_pred"]-g["actual_rate"])*100
    g["market_cal_gap_pp"]=(g["market_ref"]-g["actual_rate"])*100
    g["brier_advantage_model_minus_market"]=g["model_brier"]-g["market_brier"]
    g["feature"]=col
    g["eligible_n"]=g["n"]>=min_n
    return g

def fit_correction(train,test):
    num=[c for c in FEATURES_NUM if c in train.columns]
    cat=[c for c in FEATURES_CAT if c in train.columns]
    pre=ColumnTransformer([
        ("num",Pipeline([("imp",SimpleImputer(strategy="median")),("scale",StandardScaler())]),num),
        ("cat",Pipeline([("imp",SimpleImputer(strategy="most_frequent")),
                        ("oh",OneHotEncoder(handle_unknown="ignore"))]),cat),
    ])
    model=Pipeline([("pre",pre),("lr",LogisticRegression(max_iter=2000,C=0.5))])
    model.fit(train[num+cat],train["actual_yes"].astype(int))
    return model.predict_proba(test[num+cat])[:,1]

def score(name,y,p):
    p=np.clip(np.asarray(p,float),1e-6,1-1e-6)
    y=np.asarray(y,int)
    return {"model":name,"n":len(y),"brier":brier_score_loss(y,p),
            "logloss":log_loss(y,p),"mean_pred":p.mean(),"actual_rate":y.mean(),
            "calibration_gap_pp":(p.mean()-y.mean())*100}

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--input-dir",required=True)
    ap.add_argument("--output-dir",required=True)
    args=ap.parse_args()
    out=Path(args.output_dir); out.mkdir(parents=True,exist_ok=True)
    m=make_base(args.input_dir)
    yes=m[m["disagree_pp"]>=3].copy()

    specs={
      "target_share":[-np.inf,.10,.15,.20,.25,.30,np.inf],
      "projected_targets":[-np.inf,3,5,7,9,11,np.inf],
      "yards_per_reception":[-np.inf,8,10,12,14,16,np.inf],
      "projected_receiving_yards":[-np.inf,30,45,60,75,90,np.inf],
      "proj_minus_threshold":[-np.inf,-20,-10,0,10,20,np.inf],
      "history_hit_rate":[-np.inf,.10,.25,.40,.55,.70,np.inf],
      "threshold":[-np.inf,40,60,80,100,120,np.inf],
      "market_mid_yes":[-np.inf,.20,.40,.60,.80,np.inf],
      "disagree_pp":[3,6,10,15,20,np.inf],
    }
    reports=[]
    for col,bins in specs.items():
        r=bucket_report(yes,col,bins)
        r["direction"]="YES_MODEL_ABOVE_MARKET"
        reports.append(r)
    pd.concat(reports,ignore_index=True).to_csv(out/"v012_yes_disagreement_feature_slices.csv",index=False)

    cats=[]
    for col in ["position","role_certainty"]:
        g=(yes.groupby(col,dropna=False)
           .agg(n=("actual_yes","size"),model_pred=("fair_yes_probability","mean"),
                market_ref=("market_mid_yes","mean"),actual_rate=("actual_yes","mean"),
                model_brier=("model_brier_row","mean"),market_brier=("market_brier_row","mean"),
                model_win_rate=("model_beats_market","mean")).reset_index())
        g["feature"]=col
        g["bucket"]=g[col].astype(str)
        g["model_cal_gap_pp"]=(g.model_pred-g.actual_rate)*100
        g["market_cal_gap_pp"]=(g.market_ref-g.actual_rate)*100
        g["brier_advantage_model_minus_market"]=g.model_brier-g.market_brier
        cats.append(g[["feature","bucket","n","model_pred","market_ref","actual_rate",
                       "model_cal_gap_pp","market_cal_gap_pp","model_brier","market_brier",
                       "brier_advantage_model_minus_market","model_win_rate"]])
    pd.concat(cats,ignore_index=True).to_csv(out/"v012_yes_disagreement_categorical_slices.csv",index=False)

    train=m[m.week<=9].copy()
    test=m[m.week>=10].copy()
    p_corr=fit_correction(train,test)
    comp=[
        score("v0.10_fair_yes",test.actual_yes,test.fair_yes_probability),
        score("kalshi_quote_mid_reference",test.actual_yes,test.market_mid_yes),
        score("v0.12_feature_correction_train_w1_9",test.actual_yes,p_corr),
    ]
    pd.DataFrame(comp).to_csv(out/"v012_late_season_holdout_model_comparison.csv",index=False)
    test=test.copy(); test["v012_corrected_probability"]=p_corr
    td=test[test.disagree_pp>=3]
    if len(td):
        corr=test.loc[td.index,"v012_corrected_probability"]
        dc=[
            score("v0.10_fair_yes",td.actual_yes,td.fair_yes_probability),
            score("kalshi_quote_mid_reference",td.actual_yes,td.market_mid_yes),
            score("v0.12_feature_correction_train_w1_9",td.actual_yes,corr),
        ]
        pd.DataFrame(dc).to_csv(out/"v012_late_season_yes_disagreement_comparison.csv",index=False)

    slices=pd.read_csv(out/"v012_yes_disagreement_feature_slices.csv")
    strong=slices[slices.n>=40].sort_values(["model_cal_gap_pp","n"],ascending=[False,False]).head(30)
    strong.to_csv(out/"v012_largest_yes_overconfidence_slices.csv",index=False)

    lines=["# NFL Receiving v0.12 — Disagreement Forensics","",
           "Research only. Projection engine remains frozen. Kalshi quote is used only as a benchmark/selection diagnostic, never as a projection feature.","",
           f"Quoted settled markets analyzed: {len(m):,}. YES model-above-market disagreements >=3pp: {len(yes):,}.","",
           "## Late-season holdout (train weeks 1-9, test weeks 10+)","",
           pd.DataFrame(comp).to_markdown(index=False),"",
           "## Largest YES overconfidence slices (minimum n=40)","",
           strong[["feature","bucket","n","model_pred","market_ref","actual_rate","model_cal_gap_pp","model_win_rate"]].head(15).to_markdown(index=False),"",
           "Interpretation rule: negative model-vs-market Brier difference favors the independent model; positive favors the market reference.",
           "No trading rule is promoted by this diagnostic alone."]
    (out/"SUMMARY.md").write_text("\n".join(lines))
    print("\n".join(lines))

if __name__=="__main__":
    main()
