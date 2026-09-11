"""NFL receiving historical validation v0.11."""
from pathlib import Path
import json, sys, numpy as np, pandas as pd
ROOT=Path(sys.argv[1] if len(sys.argv)>1 else "nfl_receiving_backtest_v0_10_output")
OUT=Path(sys.argv[2] if len(sys.argv)>2 else "research/nfl_receiving_validation_v011/results")
OUT.mkdir(parents=True,exist_ok=True)
ALIASES={"prob":["fair_probability","fair_yes","model_probability","calibrated_probability","yes_probability"],"threshold":["threshold","strike","yard_threshold"],"actual":["actual_receiving_yards","actual_yards","receiving_yards","actual"],"outcome":["yes_result","result_yes","settled_yes","outcome","hit"],"yes_price":["yes_entry","yes_ask","entry_yes","entry_probability","yes_price"],"no_price":["no_entry","no_ask","entry_no","no_price"],"player":["player","player_name","player_match"],"game":["game_id","game","matchup"],"team":["team","player_team"],"season":["season","validation_season"],"week":["week","game_week"],"position":["position","pos"]}
def pick(cols,names):
    low={c.lower():c for c in cols}
    return next((low[n] for n in names if n in low),None)
def num(s): return pd.to_numeric(s,errors="coerce")
def brier(p,y): return float(np.mean((p-y)**2)) if len(p) else None
def logloss(p,y):
    if not len(p): return None
    p=np.clip(np.asarray(p,float),1e-6,1-1e-6); y=np.asarray(y,float)
    return float(-np.mean(y*np.log(p)+(1-y)*np.log(1-p)))
def load_best():
    inv=[]; best=None; score=-1
    for f in ROOT.glob("*.csv"):
        try: d=pd.read_csv(f)
        except Exception: continue
        mp={k:pick(d.columns,v) for k,v in ALIASES.items()}
        sc=sum(mp[k] is not None for k in ["prob","threshold"])+2*sum(mp[k] is not None for k in ["actual","outcome"])
        inv.append({"file":f.name,"rows":len(d),"score":sc,"columns":"|".join(d.columns)})
        if len(d) and sc>score: best=(f,d,mp); score=sc
    pd.DataFrame(inv).to_csv(OUT/"csv_inventory.csv",index=False)
    return best
def normalize(best):
    if not best:return None,{}
    f,d,mp=best; x=pd.DataFrame(index=d.index)
    for k,c in mp.items():
        if c:x[k]=d[c]
    if "prob" not in x or "threshold" not in x:return None,mp
    x["prob"]=num(x["prob"]); x.loc[x.prob>1,"prob"]=x.loc[x.prob>1,"prob"]/100; x["threshold"]=num(x["threshold"])
    if "actual" in x:
        x["actual"]=num(x["actual"]); x["y"]=(x.actual>=x.threshold).astype(float)
    elif "outcome" in x:
        if x["outcome"].dtype==object:x["y"]=x["outcome"].astype(str).str.lower().map({"true":1,"yes":1,"win":1,"1":1,"false":0,"no":0,"loss":0,"0":0})
        else:x["y"]=num(x["outcome"])
    else:return None,mp
    for p in ["yes_price","no_price"]:
        if p in x:
            x[p]=num(x[p]);x.loc[x[p]>1,p]=x.loc[x[p]>1,p]/100
    x=x[np.isfinite(x.prob)&np.isfinite(x.y)&(x.prob>0)&(x.prob<1)].copy();x["source_file"]=f.name
    return x,mp
def calibration(x):
    z=x.copy();z["bin"]=pd.cut(z.prob,np.arange(0,1.0001,.1),include_lowest=True,right=False)
    o=z.groupby("bin",observed=True).agg(n=("y","size"),pred=("prob","mean"),actual=("y","mean")).reset_index();o["gap_pp"]=(o.actual-o.pred)*100;o.to_csv(OUT/"calibration_by_probability.csv",index=False)
def core_tail(x):
    z=x.copy();z["zone"]=pd.cut(z.prob,[0,.2,.35,.65,.8,1],labels=["tail_low_<20","low_20_35","core_35_65","high_65_80","tail_high_80+"],include_lowest=True)
    rows=[]
    for zone,g in z.groupby("zone",observed=True):rows.append({"zone":zone,"n":len(g),"pred":g.prob.mean(),"actual":g.y.mean(),"gap_pp":(g.y.mean()-g.prob.mean())*100,"brier":brier(g.prob,g.y),"log_loss":logloss(g.prob,g.y)})
    pd.DataFrame(rows).to_csv(OUT/"core_vs_tail.csv",index=False)
def side_rows(x):
    if "yes_price" not in x:return pd.DataFrame()
    rows=[]
    for _,a in x.iterrows():
        yp=a.get("yes_price",np.nan);np_=a.get("no_price",np.nan)
        if not np.isfinite(np_) and np.isfinite(yp):np_=1-yp
        if not np.isfinite(yp) or not np.isfinite(np_):continue
        ey=a.prob-yp;en=(1-a.prob)-np_
        if ey>=en:side,price,fair,hit,edge="YES",yp,a.prob,a.y,ey
        else:side,price,fair,hit,edge="NO",np_,1-a.prob,1-a.y,en
        r=a.to_dict();r.update(side=side,price=price,fair_side=fair,hit_side=hit,edge=edge);rows.append(r)
    return pd.DataFrame(rows)
def pnl(stake,price,hit):
    return stake*((1-price)/price) if hit>=.5 else -stake
def strategies(x):
    s=side_rows(x)
    if s.empty:return False
    key=[c for c in ["season","week","game","player"] if c in s.columns]
    if "player" not in key:return False
    rec=[]
    for _,g in s.groupby(key,dropna=False):
        me=g.sort_values("edge",ascending=False).iloc[0]
        if me.edge>=.04:rec.append({"strategy":"max_edge_single","stake":1.0,"pnl":pnl(1,me.price,me.hit_side),"price":me.price,"edge":me.edge})
        cg=g[(g.price>=.35)&(g.price<=.65)&(g.edge>=.02)].copy()
        if cg.empty:continue
        cg["score"]=cg.edge-.20*(cg.price-.5).abs();core=cg.sort_values("score",ascending=False).iloc[0]
        rec.append({"strategy":"core_only","stake":1.0,"pnl":pnl(1,core.price,core.hit_side),"price":core.price,"edge":core.edge})
        rec.append({"strategy":"ladder","stake":1.0,"pnl":pnl(1,core.price,core.hit_side),"price":core.price,"edge":core.edge})
        if core.side=="YES":
            above=g[(g.side=="YES")&(g.threshold>core.threshold)]
            mid=above[(above.price>=.20)&(above.price<.40)&(above.edge>=.04)].sort_values("edge",ascending=False)
            tail=above[(above.price<.20)&(above.edge>=.08)].sort_values("edge",ascending=False)
            if not mid.empty:
                r=mid.iloc[0];rec.append({"strategy":"ladder","stake":.5,"pnl":pnl(.5,r.price,r.hit_side),"price":r.price,"edge":r.edge})
            if not tail.empty:
                r=tail.iloc[0];rec.append({"strategy":"ladder","stake":.25,"pnl":pnl(.25,r.price,r.hit_side),"price":r.price,"edge":r.edge})
    rr=pd.DataFrame(rec)
    if rr.empty:return False
    sm=rr.groupby("strategy").agg(legs=("pnl","size"),stake=("stake","sum"),pnl=("pnl","sum"),avg_price=("price","mean"),avg_edge=("edge","mean")).reset_index();sm["roi"]=sm.pnl/sm.stake
    rr.to_csv(OUT/"execution_strategy_legs.csv",index=False);sm.to_csv(OUT/"execution_strategy_comparison.csv",index=False);return True
best=load_best();x,mp=normalize(best);report={"status":"OK","source":best[0].name if best else None,"mapping":mp}
if x is None or x.empty:report.update(status="RAW_ROW_DATA_NOT_FOUND",note="No discoverable row-level probability+outcome CSV; see csv_inventory.csv.")
else:
    x.to_csv(OUT/"normalized_validation_rows.csv",index=False);report.update(rows=len(x),brier=brier(x.prob,x.y),log_loss=logloss(x.prob,x.y),mean_pred=float(x.prob.mean()),actual_rate=float(x.y.mean()));calibration(x);core_tail(x);report["strategy_comparison_available"]=strategies(x)
(OUT/"summary.json").write_text(json.dumps(report,indent=2,default=str))
(OUT/"REPORT.md").write_text("# NFL receiving validation v0.11\n\nStatus: **"+report["status"]+"**\n\nGenerated from frozen v0.10 historical outputs. No live rules are changed automatically.\n")
print(json.dumps(report,indent=2,default=str))
