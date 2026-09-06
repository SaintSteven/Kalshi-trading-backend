from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path

CHECKPOINTS = ["T4h", "T2h", "T1h"]
SIDES = ["YES", "NO"]
BUCKET_ORDER = ["01-09","10-19","20-39","40-60","61-80","81-90","91-99"]


def fnum(v):
    try: return float(v)
    except (TypeError, ValueError): return None


def summarize_exec(rows, cp, side):
    pnl = cap = 0.0
    n = wins = 0
    for r in rows:
        p = fnum(r.get(f"{cp}_{side}_pnl")); c = fnum(r.get(f"{cp}_{side}_capital"))
        if p is None or c is None or c <= 0: continue
        n += 1; pnl += p; cap += c; wins += int(p > 0)
    return {"trades": n, "wins": wins, "capital": round(cap,2), "pnl": round(pnl,2), "roi": round(pnl/cap,4) if cap else None}


def calibration(rows, cp):
    vals = []
    for r in rows:
        mid = fnum(r.get(f"{cp}_mid")); y = fnum(r.get("yes_outcome"))
        if mid is None or y is None: continue
        p = mid/100.0
        vals.append((p,y))
    if not vals: return {"contracts":0,"brier":None,"mean_price":None,"yes_rate":None}
    return {
        "contracts": len(vals),
        "brier": round(sum((p-y)**2 for p,y in vals)/len(vals),6),
        "mean_price": round(100*sum(p for p,_ in vals)/len(vals),3),
        "yes_rate": round(sum(y for _,y in vals)/len(vals),4),
    }


def group_summary(rows, key):
    groups = defaultdict(list)
    for r in rows: groups[r.get(key) or "unknown"].append(r)
    out = {}
    for name, rs in sorted(groups.items()):
        item = {"markets":len(rs)}
        for cp in CHECKPOINTS:
            item[f"{cp}_calibration"] = calibration(rs, cp)
            for side in SIDES:
                item[f"{cp}_{side}"] = summarize_exec(rs, cp, side)
        out[name] = item
    return out


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--root", required=True); ap.add_argument("--out", required=True)
    args = ap.parse_args(); root=Path(args.root); out=Path(args.out); out.mkdir(parents=True, exist_ok=True)
    rows=[]
    for path in root.rglob("market_ledger.csv"):
        with path.open(encoding="utf-8") as f: rows.extend(csv.DictReader(f))
    if not rows: raise SystemExit("No market ledger rows found")

    fields=sorted({k for r in rows for k in r})
    with (out/"market_ledger.csv").open("w", newline="", encoding="utf-8") as f:
        w=csv.DictWriter(f, fieldnames=fields); w.writeheader(); w.writerows(rows)

    overall={"markets":len(rows),"events":len({r.get('event_ticker') for r in rows if r.get('event_ticker')}),"series":len({r.get('series_ticker') for r in rows if r.get('series_ticker')})}
    for cp in CHECKPOINTS:
        overall[f"{cp}_calibration"]=calibration(rows,cp)
        for side in SIDES: overall[f"{cp}_{side}"]=summarize_exec(rows,cp,side)

    by_family=group_summary(rows,"family")
    by_series=group_summary(rows,"series_ticker")
    by_bucket={}
    for cp in CHECKPOINTS:
        by_bucket[cp]={}
        for side in SIDES:
            by_bucket[cp][side]={}
            for bucket in BUCKET_ORDER:
                rs=[r for r in rows if r.get(f"{cp}_{side}_bucket")==bucket]
                by_bucket[cp][side][bucket]=summarize_exec(rs,cp,side)

    findings=[]
    for cp in CHECKPOINTS:
        for side in SIDES:
            for bucket,met in by_bucket[cp][side].items():
                if met["trades"]>=50 and met["roi"] is not None:
                    findings.append({"dimension":"price_bucket","checkpoint":cp,"side":side,"segment":bucket,**met})
    for fam,data in by_family.items():
        for cp in CHECKPOINTS:
            for side in SIDES:
                met=data[f"{cp}_{side}"]
                if met["trades"]>=50 and met["roi"] is not None:
                    findings.append({"dimension":"family","checkpoint":cp,"side":side,"segment":fam,**met})
    findings.sort(key=lambda x:(x.get("roi") is not None, x.get("roi") or -999, x.get("trades",0)), reverse=True)

    summary={"version":"0.01","mode":"discovery-only-top-down-mlb-market-efficiency","warning":"This is an in-sample discovery map. No segment is a betting rule until frozen and validated out of sample.","overall":overall,"by_family":by_family,"by_series":by_series,"by_executable_price_bucket":by_bucket,"candidate_findings":findings[:40]}
    (out/"summary.json").write_text(json.dumps(summary,indent=2),encoding="utf-8")

    lines=["# MLB Market Efficiency Scanner v0.01","","**DISCOVERY ONLY — NOT A BETTING SYSTEM**","",f"Markets: {overall['markets']}  |  Events: {overall['events']}  |  Series: {overall['series']}","","## Overall"]
    for cp in CHECKPOINTS:
        cal=overall[f"{cp}_calibration"]
        lines.append(f"- {cp}: {cal['contracts']} calibrated contracts, Brier {cal['brier']}; YES ROI {overall[f'{cp}_YES']['roi']}; NO ROI {overall[f'{cp}_NO']['roi']}")
    lines += ["","## Highest-signal discovery segments (minimum 50 trades)"]
    for x in findings[:20]:
        lines.append(f"- {x['dimension']} | {x['segment']} | {x['checkpoint']} {x['side']} | n={x['trades']} | ROI={x['roi']} | P/L=${x['pnl']}")
    lines += ["","Do not promote any segment from this file. Use these results only to choose a small number of hypotheses for frozen out-of-sample validation."]
    (out/"SUMMARY.md").write_text("\n".join(lines),encoding="utf-8")
    print(json.dumps({"markets":overall['markets'],"events":overall['events'],"series":overall['series'],"top_findings":findings[:10]},indent=2))

if __name__=="__main__": main()
