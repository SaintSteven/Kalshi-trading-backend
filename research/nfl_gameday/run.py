"""NFL game-day production orchestrator.

Production is intentionally independent of the prospective-paper collector.
Each stage writes a durable checkpoint before later stages run.
"""
from __future__ import annotations
import argparse, json, shutil, subprocess, sys
from datetime import datetime, timezone
from pathlib import Path
import pandas as pd

ROOT=Path(__file__).resolve().parents[1]
HERE=Path(__file__).resolve().parent
OUT=HERE/"output"; RAW=OUT/"raw"; MODELS=OUT/"models"
LEGACY=ROOT/"nfl_prospective_paper_v001"
for p in (OUT,RAW,MODELS): p.mkdir(parents=True,exist_ok=True)

def run(cmd):
    print("+"," ".join(map(str,cmd)),flush=True)
    subprocess.run(list(map(str,cmd)),check=True)

def capture():
    run([sys.executable,LEGACY/"current_collector.py","--current",RAW/"current_markets.csv","--health",RAW/"collector_health.csv"])
    m=pd.read_csv(RAW/"current_markets.csv")
    meta={"captured_at":datetime.now(timezone.utc).isoformat(),"markets":len(m),"families":m.prop_family.value_counts().to_dict() if len(m) else {}}
    (RAW/"capture.json").write_text(json.dumps(meta,indent=2))
    print(meta)

def receiving():
    run([sys.executable,LEGACY/"generate_receiving_projections.py","--markets",RAW/"current_markets.csv","--projections",MODELS/"independent_receiving_projections.csv","--mapping",MODELS/"receiving_market_mapping.csv"])
    run([sys.executable,LEGACY/"receiving_feed.py","--markets",RAW/"current_markets.csv","--projections",MODELS/"independent_receiving_projections.csv","--mapping",MODELS/"receiving_market_mapping.csv","--output",MODELS/"receiving_fair_values.csv"])

def rushing():
    run([sys.executable,LEGACY/"generate_rushing_projections.py","--markets",RAW/"current_markets.csv","--projections",MODELS/"independent_rushing_projections.csv","--mapping",MODELS/"rushing_market_mapping.csv"])
    run([sys.executable,LEGACY/"rushing_feed.py","--markets",RAW/"current_markets.csv","--projections",MODELS/"independent_rushing_projections.csv","--mapping",MODELS/"rushing_market_mapping.csv","--output",MODELS/"rushing_fair_values.csv"])

def card():
    markets=pd.read_csv(RAW/"current_markets.csv")
    rows=[]
    for family,file in [("receiving_yards","receiving_fair_values.csv"),("rushing_yards","rushing_fair_values.csv")]:
        p=MODELS/file
        if not p.exists() or p.stat().st_size==0: continue
        f=pd.read_csv(p)
        if f.empty: continue
        keep=[c for c in ["market_ticker","projection","fair_yes","fair_no","qc_status","qc_reason","model_version"] if c in f.columns]
        x=markets[markets.prop_family.eq(family)].merge(f[keep],on="market_ticker",how="inner",suffixes=("_market","_model"))
        for c in ["yes_ask","no_ask","fair_yes","fair_no"]:
            if c in x: x[c]=pd.to_numeric(x[c],errors="coerce")
        x["yes_edge"]=x.fair_yes-x.yes_ask
        x["no_edge"]=x.fair_no-x.no_ask
        for _,r in x.iterrows():
            market_qc=r.get("qc_status_market",r.get("qc_status",""))
            model_qc=r.get("qc_status_model",r.get("qc_status",""))
            if market_qc!="PASS" or model_qc not in ("PASS","MODEL_AVAILABLE"): continue
            choices=[("YES",r.get("yes_edge"),r.get("yes_ask")),("NO",r.get("no_edge"),r.get("no_ask"))]
            side,edge,price=max(choices,key=lambda z: z[1] if pd.notna(z[1]) else -999)
            if pd.isna(edge) or edge < .02: continue
            rows.append({"family":family,"game":r.get("game"),"kickoff_utc":r.get("kickoff_utc"),"player":r.get("player_name"),"ticker":r.market_ticker,"projection":r.get("projection"),"side":side,"price":price,"fair":r.get("fair_yes") if side=="YES" else r.get("fair_no"),"edge":edge,"model_version":r.get("model_version")})
    d=pd.DataFrame(rows)
    if len(d): d=d.sort_values("edge",ascending=False)
    d.to_csv(OUT/"candidates.csv",index=False)
    lines=["# NFL Game-Day Candidate Card","",f"Generated: {datetime.now(timezone.utc).isoformat()}","","Production candidate layer only. Manual injury/role and correlation QC still required.",""]
    lines += [d.head(30).to_markdown(index=False) if len(d) else "No model-qualified candidates."]
    (OUT/"CARD.md").write_text("\n".join(lines))

def health():
    stages={}
    def csvstat(path):
        try:return {"ok":True,"rows":len(pd.read_csv(path))}
        except Exception as e:return {"ok":False,"error":repr(e)}
    stages["market_capture"]=csvstat(RAW/"current_markets.csv")
    stages["collector_health"]=csvstat(RAW/"collector_health.csv")
    stages["receiving"]=csvstat(MODELS/"receiving_fair_values.csv")
    stages["rushing"]=csvstat(MODELS/"rushing_fair_values.csv")
    stages["card"]=csvstat(OUT/"candidates.csv")
    overall=stages["market_capture"].get("ok",False) and any(stages[x].get("ok",False) for x in ("receiving","rushing"))
    payload={"generated_at":datetime.now(timezone.utc).isoformat(),"overall_usable":overall,"stages":stages}
    (OUT/"health.json").write_text(json.dumps(payload,indent=2))
    lines=["# NFL Game-Day Health","",f"Overall usable: **{overall}**",""]
    for k,v in stages.items(): lines.append(f"- {k}: {'PASS' if v.get('ok') else 'FAIL'}" + (f" — {v.get('rows')} rows" if v.get('ok') else f" — {v.get('error')}"))
    (OUT/"HEALTH.md").write_text("\n".join(lines))
    print(json.dumps(payload,indent=2))

def main():
    ap=argparse.ArgumentParser();ap.add_argument("stage",choices=["capture","receiving","rushing","card","health","all"]);a=ap.parse_args()
    if a.stage=="all":
        capture()
        for fn in (receiving,rushing):
            try:fn()
            except Exception as e:print("STAGE_FAILED",fn.__name__,repr(e),file=sys.stderr)
        try:card()
        except Exception as e:print("STAGE_FAILED card",repr(e),file=sys.stderr)
        health();return
    globals()[a.stage]()

if __name__=="__main__":main()
