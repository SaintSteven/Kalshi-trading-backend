"""Build the current one-check receiving-yards research decision card.

Research only; no orders are placed. Independent fair value is produced upstream
without market price. This layer overlays executable quotes and classifies current
value for the user's single daily check. No T-30/T-60 revisit is required.
"""
from __future__ import annotations

from pathlib import Path
from datetime import datetime, timezone
import pandas as pd

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
MARKETS = DATA / "current_markets.csv"
FAIR = DATA / "receiving_fair_values.csv"
OUT = DATA / "pilot_card.csv"
MD = DATA / "PILOT_CARD.md"

CORE_MIN = 0.35
CORE_MAX = 0.65
CORE_EDGE = 0.02
MID_MIN = 0.20
MID_MAX = 0.40
MID_EDGE = 0.04
TAIL_MAX = 0.20
TAIL_EDGE = 0.08
UNIT_CAP = 1.00
WEEKLY_CAP = 5.00


def num(s):
    return pd.to_numeric(s, errors="coerce")


def main():
    if not MARKETS.exists() or not FAIR.exists():
        raise SystemExit("required market/fair-value files missing")

    m = pd.read_csv(MARKETS)
    f = pd.read_csv(FAIR)
    m = m[m.get("prop_family", "").eq("receiving_yards")].copy()
    f = f[f.get("qc_status", "").eq("MODEL_AVAILABLE")].copy()

    if m.empty:
        pd.DataFrame().to_csv(OUT, index=False)
        MD.write_text("# NFL Week 1 Pilot Card\n\nNo open receiving-yards markets found.\n")
        return

    keep = [c for c in ["market_ticker","game_id","player_id","threshold","model_version","generated_at","fair_yes","fair_no","projection","qc_status","qc_reason"] if c in f.columns]
    x = m.merge(f[keep], on="market_ticker", how="left", suffixes=("_market","_model"))

    for c in ["yes_bid","yes_ask","no_bid","no_ask","fair_yes","fair_no","threshold"]:
        if c in x.columns:
            x[c] = num(x[c])

    now = datetime.now(timezone.utc)
    x["kickoff_dt"] = pd.to_datetime(x["kickoff_utc"], utc=True, errors="coerce")
    x["minutes_to_kickoff"] = (x["kickoff_dt"] - now).dt.total_seconds() / 60.0
    x["model_available"] = x.get("qc_status_model", x.get("qc_status", "")).eq("MODEL_AVAILABLE")
    market_qc_col = "qc_status_market" if "qc_status_market" in x.columns else "qc_status"
    x["market_qc_pass"] = x.get(market_qc_col, "").eq("PASS")

    # Current executable NO entry is no_ask. Independent NO value = fair_no.
    x["no_edge"] = x["fair_no"] - x["no_ask"]
    x["yes_edge"] = x["fair_yes"] - x["yes_ask"]
    x["entry_price"] = x["no_ask"]
    x["edge_required"] = CORE_EDGE
    x.loc[x["entry_price"].between(MID_MIN, MID_MAX, inclusive="left"), "edge_required"] = MID_EDGE
    x.loc[x["entry_price"] < TAIL_MAX, "edge_required"] = TAIL_EDGE
    x["size_units"] = 1.0
    x.loc[x["entry_price"].between(MID_MIN, MID_MAX, inclusive="left"), "size_units"] = 0.5
    x.loc[x["entry_price"] < TAIL_MAX, "size_units"] = 0.25

    def action(r):
        if not bool(r.get("market_qc_pass", False)):
            return "PASS_MARKET_QC"
        if not bool(r.get("model_available", False)):
            return "PASS_MODEL_UNAVAILABLE"
        if pd.isna(r.get("no_ask")) or pd.isna(r.get("fair_no")):
            return "PASS_MISSING_PRICE_OR_FAIR"
        if r.get("minutes_to_kickoff", -1) < 0:
            return "PASS_STARTED_OR_CLOSED"
        if r.get("no_edge", -1) >= r.get("edge_required", 1):
            return "BET_NOW_PAPER"
        if r.get("no_edge", -1) > 0:
            return "WATCH"
        return "PASS_NO_EDGE"

    x["action"] = x.apply(action, axis=1)
    x["max_contracts_at_1_unit"] = (UNIT_CAP / x["no_ask"]).fillna(0).astype(int)
    x.loc[x["max_contracts_at_1_unit"] < 1, "max_contracts_at_1_unit"] = 0

    display_cols = [c for c in [
        "updated_at","game_id","game","kickoff_utc","market_ticker","player_name","player_id","threshold",
        "yes_bid","yes_ask","no_bid","no_ask","projection","fair_yes","fair_no","yes_edge","no_edge",
        "minutes_to_kickoff","entry_price","edge_required","size_units","market_qc_pass","model_available","action","max_contracts_at_1_unit"
    ] if c in x.columns]
    out = x[display_cols].copy()
    out = out.sort_values(["action","no_edge"], ascending=[True, False], na_position="last")
    out.to_csv(OUT, index=False)

    eligible_preview = out[(out["action"] == "WATCH") & out["model_available"] & out["market_qc_pass"]].copy()
    eligible_preview = eligible_preview.sort_values("no_edge", ascending=False).head(12)
    ready = out[out["action"] == "BET_NOW_PAPER"].copy()

    lines = [
        "# NFL One-Check Receiving Decision Card",
        "",
        f"Generated: {now.isoformat()}",
        "",
        "**Mode: PAPER ONLY / manual pilot. No real-money orders are generated.**",
        "",
        f"Prospective one-check hypotheses: core 35-65c needs >=2pp edge (1.0u); middle 20-40c needs >=4pp (0.5u); tail <20c needs >=8pp (0.25u). No T-30/T-60 revisit required. Unit cap ${UNIT_CAP:.2f}; weekly cap ${WEEKLY_CAP:.2f}.",
        "",
        f"READY now: **{len(ready)}**",
        f"Current in-band model-qualified watchlist: **{len(eligible_preview)}**",
        "",
    ]
    if len(ready):
        lines += ["## BET NOW — PAPER research entries", "", ready.head(10).to_markdown(index=False), ""]
    else:
        lines += ["## Entry status", "", "No receiving-yards entries currently clear the one-check model/QC/edge gates.", ""]
    if len(eligible_preview):
        cols = [c for c in ["game","player_name","threshold","no_ask","fair_no","no_edge","kickoff_utc","action"] if c in eligible_preview.columns]
        lines += ["## Current WATCH list", "", eligible_preview[cols].to_markdown(index=False), ""]
    lines += [
        "## Guardrail",
        "",
        "BET NOW means actionable during the user's current daily check after manual injury/role QC. Edge thresholds and sizing remain prospective hypotheses, not historically proven optima. Same-player ladders and correlated teammates must be collapsed into thesis-level exposure before execution.",
        "",
    ]
    MD.write_text("\n".join(lines))
    print(f"pilot rows={len(out)} ready={len(ready)} watch={len(eligible_preview)}")

if __name__ == "__main__":
    main()
