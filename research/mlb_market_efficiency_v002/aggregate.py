from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path


def fnum(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def aggregate(root: Path, out: Path):
    out.mkdir(parents=True, exist_ok=True)
    files = list(root.rglob("market_ledger.csv"))
    rows = []
    coverage = []
    for p in files:
        if p.stat().st_size:
            with p.open(encoding="utf-8") as f:
                rows.extend(csv.DictReader(f))
    for p in root.rglob("coverage.json"):
        try:
            coverage.append(json.loads(p.read_text(encoding="utf-8")))
        except Exception:
            pass

    dedup = {}
    for r in rows:
        t = r.get("ticker")
        if t:
            dedup[t] = r
    rows = list(dedup.values())
    series = sorted({r.get("series_ticker") for r in rows if r.get("series_ticker")})
    families = sorted({r.get("family") for r in rows if r.get("family")})
    dates = sorted({r.get("game_date") for r in rows if r.get("game_date")})
    events = {r.get("event_ticker") for r in rows if r.get("event_ticker")}

    summary = {
        "version": "0.02",
        "mode": "discovery-only-top-down-mlb-market-efficiency-corrected",
        "warning": "Descriptive discovery only. No segment is a betting rule until frozen out-of-sample validation.",
        "quality": {
            "markets": len(rows), "events": len(events), "series": len(series), "families": len(families),
            "distinct_game_dates": len(dates), "first_game_date": dates[0] if dates else None, "last_game_date": dates[-1] if dates else None,
            "duplicate_tickers_removed": max(0, sum(1 for _ in []) + len(dedup) - len(rows)),
        },
        "series_counts": {}, "family_counts": {}, "checkpoints": {}, "candidate_segments": [],
    }
    for s in series:
        summary["series_counts"][s] = sum(1 for r in rows if r.get("series_ticker") == s)
    for fam in families:
        summary["family_counts"][fam] = sum(1 for r in rows if r.get("family") == fam)

    findings = []
    for cp in ("T4h", "T2h", "T1h"):
        calib = []
        for r in rows:
            mid = fnum(r.get(f"{cp}_mid")); y = fnum(r.get("yes_outcome"))
            if mid is not None and y is not None:
                p = mid / 100.0; calib.append((p, y))
        summary["checkpoints"][cp] = {
            "contracts": len(calib),
            "brier": round(sum((p-y)**2 for p,y in calib)/len(calib), 6) if calib else None,
        }
        for side in ("YES", "NO"):
            cap = pnl = 0.0; n = wins = 0
            for r in rows:
                c = fnum(r.get(f"{cp}_{side}_capital")); x = fnum(r.get(f"{cp}_{side}_pnl"))
                if c is None or x is None: continue
                n += 1; cap += c; pnl += x; wins += int(x > 0)
            summary["checkpoints"][cp][side] = {"trades": n, "wins": wins, "capital": round(cap,2), "pnl": round(pnl,2), "roi": round(pnl/cap,4) if cap else None}

        for dim in ("family", "series_ticker"):
            segments = sorted({r.get(dim) for r in rows if r.get(dim)})
            for seg in segments:
                for side in ("YES", "NO"):
                    cap = pnl = 0.0; n = wins = 0
                    for r in rows:
                        if r.get(dim) != seg: continue
                        c=fnum(r.get(f"{cp}_{side}_capital")); x=fnum(r.get(f"{cp}_{side}_pnl"))
                        if c is None or x is None: continue
                        n += 1; cap += c; pnl += x; wins += int(x > 0)
                    if n >= 100 and cap:
                        findings.append({"dimension":dim,"segment":seg,"checkpoint":cp,"side":side,"trades":n,"wins":wins,"capital":round(cap,2),"pnl":round(pnl,2),"roi":round(pnl/cap,4)})
    findings.sort(key=lambda x: x["roi"], reverse=True)
    summary["candidate_segments"] = findings[:30]

    # Hard quality checks. These prevent another silently partial scan from being treated as complete.
    q = summary["quality"]
    checks = {
        "series_ge_10": q["series"] >= 10,
        "families_ge_8": q["families"] >= 8,
        "game_dates_ge_45": q["distinct_game_dates"] >= 45,
        "markets_ge_5000": q["markets"] >= 5000,
        "starts_in_july": bool(q["first_game_date"] and q["first_game_date"] <= "2026-07-05"),
        "reaches_august": bool(q["last_game_date"] and q["last_game_date"] >= "2026-08-25"),
    }
    summary["quality_gate"] = checks
    summary["quality_gate_pass"] = all(checks.values())

    keys = sorted({k for r in rows for k in r})
    with (out/"market_ledger.csv").open("w",newline="",encoding="utf-8") as f:
        w=csv.DictWriter(f,fieldnames=keys); w.writeheader(); w.writerows(rows)
    (out/"summary.json").write_text(json.dumps(summary,indent=2),encoding="utf-8")
    lines=["# MLB Market Efficiency v0.02", "", "**DISCOVERY ONLY — REAL MONEY OFF**", "",
           f"Markets: {q['markets']} | Events: {q['events']} | Series: {q['series']} | Families: {q['families']} | Game dates: {q['distinct_game_dates']}",
           f"Coverage: {q['first_game_date']} through {q['last_game_date']}", "",
           f"## Data-quality gate: {'PASS' if summary['quality_gate_pass'] else 'FAIL'}"]
    for k,v in checks.items(): lines.append(f"- {k}: {'PASS' if v else 'FAIL'}")
    lines += ["", "## Checkpoints"]
    for cp,d in summary["checkpoints"].items():
        lines.append(f"- {cp}: n={d['contracts']} Brier={d['brier']} | YES ROI={d['YES']['roi']} | NO ROI={d['NO']['roi']}")
    lines += ["", "## Highest discovery segments (min 100 executable trades)"]
    for x in findings[:20]:
        lines.append(f"- {x['dimension']} {x['segment']} | {x['checkpoint']} {x['side']} | n={x['trades']} | ROI={x['roi']} | P/L=${x['pnl']}")
    lines += ["", "Do not promote any segment from this discovery run. Freeze hypotheses first, then validate out of sample."]
    (out/"SUMMARY.md").write_text("\n".join(lines)+"\n",encoding="utf-8")
    print(json.dumps({"quality":q,"quality_gate":checks,"pass":summary["quality_gate_pass"],"top":findings[:10]},indent=2))
    if not summary["quality_gate_pass"]:
        raise SystemExit("Data-quality gate failed; refusing to label scan complete")


def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--root",required=True); ap.add_argument("--out",required=True); a=ap.parse_args()
    aggregate(Path(a.root),Path(a.out))

if __name__ == "__main__": main()
