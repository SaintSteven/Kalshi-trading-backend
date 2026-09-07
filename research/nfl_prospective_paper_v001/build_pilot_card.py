"""Build a fail-closed Week 1 receiving-yards pilot card.

Research only. This does not place orders and does not override frozen timing rules.
A market can only become READY when it is inside the frozen T-30 window, the
primary NO price is 40-49c, market/model QC pass, and independent fair value is
available. Before then, otherwise interesting markets are WAIT_FOR_T30.
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

PRIMARY_MIN = 0.40
PRIMARY_MAX = 0.49
TARGET_MINUTES = 30
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
    x["primary_price_band"] = x["no_ask"].between(PRIMARY_MIN, PRIMARY_MAX, inclusive="both")
    x["inside_t30_window"] = x["minutes_to_kickoff"].between(0, TARGET_MINUTES, inclusive="both")

    def action(r):
        if not bool(r.get("market_qc_pass", False)):
            return "PASS_MARKET_QC"
        if not bool(r.get("model_available", False)):
            return "PASS_MODEL_UNAVAILABLE"
        if pd.isna(r.get("no_ask")) or pd.isna(r.get("fair_no")):
            return "PASS_MISSING_PRICE_OR_FAIR"
        if not bool(r.get("inside_t30_window", False)):
            return "WAIT_FOR_T30"
        if not bool(r.get("primary_price_band", False)):
            return "PASS_PRICE_OUTSIDE_FROZEN_BAND"
        if r.get("no_edge", -1) <= 0:
            return "PASS_NO_POSITIVE_MODEL_EDGE"
        return "READY_PAPER_ONLY"

    x["action"] = x.apply(action, axis=1)
    x["max_contracts_at_1_unit"] = (UNIT_CAP / x["no_ask"]).fillna(0).astype(int)
    x.loc[x["max_contracts_at_1_unit"] < 1, "max_contracts_at_1_unit"] = 0

    display_cols = [c for c in [
        "updated_at","game_id","game","kickoff_utc","market_ticker","player_name","player_id","threshold",
        "yes_bid","yes_ask","no_bid","no_ask","projection","fair_yes","fair_no","yes_edge","no_edge",
        "minutes_to_kickoff","primary_price_band","market_qc_pass","model_available","action","max_contracts_at_1_unit"
    ] if c in x.columns]
    out = x[display_cols].copy()
    out = out.sort_values(["action","no_edge"], ascending=[True, False], na_position="last")
    out.to_csv(OUT, index=False)

    eligible_preview = out[(out["action"] == "WAIT_FOR_T30") & out["primary_price_band"] & out["model_available"] & out["market_qc_pass"]].copy()
    eligible_preview = eligible_preview.sort_values("no_edge", ascending=False).head(12)
    ready = out[out["action"] == "READY_PAPER_ONLY"].copy()

    lines = [
        "# NFL Week 1 Pilot Card",
        "",
        f"Generated: {now.isoformat()}",
        "",
        "**Mode: PAPER ONLY / manual pilot. No real-money orders are generated.**",
        "",
        f"Frozen primary rule: receiving-yards **NO 40-49c at T-30**, one thesis per player/game. Unit cap ${UNIT_CAP:.2f}; weekly cap ${WEEKLY_CAP:.2f}.",
        "",
        f"READY now: **{len(ready)}**",
        f"Current in-band model-qualified watchlist: **{len(eligible_preview)}**",
        "",
    ]
    if len(ready):
        lines += ["## READY PAPER entries", "", ready.head(10).to_markdown(index=False), ""]
    else:
        lines += ["## Entry status", "", "No receiving-yards entries are READY at this moment. The frozen protocol requires the T-30 window; early entry is not allowed by this card.", ""]
    if len(eligible_preview):
        cols = [c for c in ["game","player_name","threshold","no_ask","fair_no","no_edge","kickoff_utc","action"] if c in eligible_preview.columns]
        lines += ["## Current watchlist (not entries)", "", eligible_preview[cols].to_markdown(index=False), ""]
    lines += [
        "## Guardrail",
        "",
        "A large current model edge does not authorize an early trade. The historical timing study did not validate systematic early entry, so the live pilot waits for the frozen target window.",
        "",
    ]
    MD.write_text("\n".join(lines))
    print(f"pilot rows={len(out)} ready={len(ready)} watch={len(eligible_preview)}")

if __name__ == "__main__":
    main()
