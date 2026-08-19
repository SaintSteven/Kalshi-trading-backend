from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

from edge_models import HistoricalMarketRecord, SavedTradeSnapshot
from github_checkpoint_store import GitHubCheckpointStore
from snapshot_settlement import settle_snapshots
from v3_challenger_lab import _fit_projection
from v31_residual_edge_lab import _fit_logistic, _predict

STATE_PATH = "forward_validation/v33_state.json.gz"
PRIMARY_EDGE = 10.0
EDGE_BUCKETS = [(5.0, 7.5, "5-7.4"), (7.5, 10.0, "7.5-9.9"), (10.0, 12.5, "10-12.4"), (12.5, 15.0, "12.5-14.9"), (15.0, 1e9, "15+")]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _bucket(edge: float) -> str:
    for lo, hi, label in EDGE_BUCKETS:
        if lo <= edge < hi:
            return label
    return "<5"


def _component(conf: dict, key: str, fallback: str | None = None) -> float | None:
    v = conf.get(key)
    if v is None and fallback:
        v = conf.get(fallback)
    try:
        return float(v) if v is not None else None
    except Exception:
        return None


def fit_v31(history_records: list[dict]):
    rows = [HistoricalMarketRecord(**r) for r in history_records]
    a, b, sigma = _fit_projection(rows)
    weights = _fit_logistic(rows, a, b, sigma)
    return rows, a, b, sigma, weights


def score_recommendations(history_records: list[dict], recommendations: list[Any], game_date: str, source_job_id: str | None = None) -> dict:
    _, a, b, sigma, weights = fit_v31(history_records)
    scored = []
    for rec in recommendations:
        side = getattr(rec, "side", None)
        price = getattr(rec, "market_price_cents", None)
        if side not in {"YES", "NO"} or price is None:
            continue
        conf = getattr(rec, "confidence", {}) or {}
        fair = getattr(rec, "calibrated_fair_probability", None)
        if fair is None:
            fair = getattr(rec, "fair_probability", None)
        if fair is None:
            fair = max(0.01, min(0.99, float(price) / 100.0))
        live = SimpleNamespace(
            threshold=getattr(rec, "threshold"), side=side,
            entry_price_cents=int(price), model_probability=float(fair),
            projected_strikeouts=getattr(rec, "projected_strikeouts", None),
            confidence_skill=_component(conf, "pitcher_skill"),
            confidence_lineup=_component(conf, "lineup"),
            confidence_workload=_component(conf, "workload"),
            confidence_stability=_component(conf, "workload_stability"),
            confidence_recent=_component(conf, "recent_change"),
        )
        p, baseball_p, market_p = _predict(live, weights, a, b, sigma)
        edge = 100.0 * p - float(price)
        row = {
            "id": f"{game_date}|{getattr(rec,'ticker','')}|{side}",
            "game_date": game_date,
            "captured_at": _now(),
            "ticker": getattr(rec, "ticker", None),
            "player": getattr(rec, "player", None),
            "threshold": getattr(rec, "threshold", None),
            "side": side,
            "matchup": getattr(rec, "matchup", None),
            "game_start_display": getattr(rec, "game_start_display", None),
            "entry_price_cents": int(price),
            "market_probability": market_p,
            "baseball_probability": baseball_p,
            "v31_probability": p,
            "residual_edge_points": edge,
            "edge_bucket": _bucket(edge),
            "qualifies_5pt": edge >= 5.0,
            "primary_10pt": edge >= PRIMARY_EDGE,
            "projected_strikeouts": getattr(rec, "projected_strikeouts", None),
            "confidence": conf,
            "source_job_id": source_job_id,
            "status": "PENDING",
            "actual_strikeouts": None,
            "won": None,
            "net_profit": None,
        }
        scored.append(row)
    return {
        "version": "3.3.0",
        "fit": {"projection_intercept": a, "projection_slope": b, "projection_sigma": sigma, "residual_coefficients": weights},
        "scored": scored,
        "qualifiers": [x for x in scored if x["qualifies_5pt"]],
        "primary": [x for x in scored if x["primary_10pt"]],
    }


class ForwardValidationStore:
    def __init__(self):
        self.github = GitHubCheckpointStore()

    def _empty(self):
        return {
            "version": "3.3.0",
            "created_at": _now(),
            "updated_at": _now(),
            "candidate": "Frozen v3.1 residual-edge architecture fit only on Apr-Jul historical full-universe data",
            "primary_hypothesis": "Residual edge >=10 points will produce positive ROI on unseen forward paper trades",
            "primary_edge_points": PRIMARY_EDGE,
            "target_primary_settled": 100,
            "captures": [],
        }

    def load(self) -> dict:
        if not self.github.enabled:
            raise RuntimeError("GitHub checkpoint persistence is required for v3.3 forward validation.")
        value, _ = self.github._get_file(STATE_PATH)
        return value if isinstance(value, dict) else self._empty()

    def save(self, state: dict):
        state["updated_at"] = _now()
        self.github._put_file(STATE_PATH, state, "update v3.3 forward validation ledger")

    def append_capture(self, scored_payload: dict, game_date: str) -> dict:
        state = self.load()
        existing = {x.get("id") for x in state.get("captures", [])}
        added = 0
        for row in scored_payload.get("qualifiers", []):
            if row.get("id") in existing:
                continue
            state.setdefault("captures", []).append(row)
            existing.add(row.get("id")); added += 1
        state["last_capture_date"] = game_date
        state["fit"] = scored_payload.get("fit")
        self.save(state)
        state["added"] = added
        return state


async def settle_state(state: dict) -> tuple[dict, list[str]]:
    pending_rows = [x for x in state.get("captures", []) if x.get("status") == "PENDING"]
    snapshots = []
    by_key = {}
    for x in pending_rows:
        snap = SavedTradeSnapshot(
            player=x["player"], game_date=x["game_date"], threshold=x["threshold"], side=x["side"],
            model_probability=float(x["v31_probability"]), raw_model_probability=float(x.get("baseball_probability") or x["v31_probability"]),
            entry_price_cents=int(x["entry_price_cents"]), model_version="3.3-forward-v3.1-frozen",
            confidence=float((x.get("confidence") or {}).get("overall") or 0), adjusted_edge_points=float(x["residual_edge_points"]),
            stake=1.0, ticker=x.get("ticker"), matchup=x.get("matchup"), captured_at=x.get("captured_at"),
        )
        snapshots.append(snap)
        by_key[(snap.game_date, snap.player, snap.threshold, snap.side)] = x
    settled, _, warnings = await settle_snapshots(snapshots)
    for r in settled:
        x = by_key.get((r.game_date, r.player, r.threshold, r.side))
        if not x:
            continue
        won = 1 if ((r.actual_strikeouts >= int(str(r.threshold).rstrip('+'))) == (r.side == "YES")) else 0
        price = r.entry_price_cents / 100.0
        pnl = ((1.0 / price) * (1.0 - price)) if won else -1.0
        x.update(status="SETTLED", actual_strikeouts=r.actual_strikeouts, won=won, net_profit=pnl, settled_at=_now())
    return state, warnings


def _summ(rows: list[dict]) -> dict:
    settled = [x for x in rows if x.get("status") == "SETTLED"]
    risk = float(len(settled)); pnl = sum(float(x.get("net_profit") or 0) for x in settled)
    wins = sum(int(x.get("won") or 0) for x in settled)
    brier = None
    if settled:
        brier = sum((float(x["v31_probability"]) - int(x.get("won") or 0)) ** 2 for x in settled) / len(settled)
    return {"captured": len(rows), "settled": len(settled), "pending": len(rows)-len(settled), "wins": wins,
            "win_rate": wins/len(settled) if settled else None, "net_profit": pnl, "roi": pnl/risk if risk else None,
            "brier": brier}


def summarize_state(state: dict) -> dict:
    rows = state.get("captures", [])
    primary = [x for x in rows if x.get("primary_10pt")]
    buckets = []
    for label in ["5-7.4","7.5-9.9","10-12.4","12.5-14.9","15+"]:
        vals = [x for x in rows if x.get("edge_bucket") == label]
        buckets.append({"bucket": label, **_summ(vals)})
    daily = []
    for d in sorted({x.get("game_date") for x in rows if x.get("game_date")}, reverse=True):
        vals = [x for x in rows if x.get("game_date") == d]
        daily.append({"date": d, **_summ(vals), "primary_captured": sum(1 for x in vals if x.get("primary_10pt"))})
    return {
        "version": "3.3.0", "candidate": state.get("candidate"), "primary_hypothesis": state.get("primary_hypothesis"),
        "primary_edge_points": PRIMARY_EDGE, "target_primary_settled": state.get("target_primary_settled",100),
        "all_5pt": _summ(rows), "primary_10pt": _summ(primary), "edge_buckets": buckets, "daily": daily[:30],
        "recent_captures": sorted(rows, key=lambda x: x.get("captured_at") or "", reverse=True)[:50],
        "fit": state.get("fit"), "updated_at": state.get("updated_at"),
        "guardrails": [
            "v3.1 architecture and Apr-Jul training history are frozen for this forward test.",
            "Primary hypothesis was declared before unseen forward outcomes: residual edge >=10 points should have positive ROI.",
            "All >=5-point opportunities are still recorded so the edge gradient can be audited without changing the primary threshold.",
            "No side, ladder, or entry-price filter may be added from forward results during the validation window.",
            "Real-money promotion requires at least 100 settled >=10-point forward trades plus positive ROI and acceptable calibration/drawdown review.",
        ],
    }
