"""One-button, free-source MLB game discovery for the hybrid research desk."""

from __future__ import annotations

import asyncio
import math
import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import httpx

from config import KALSHI_BASE_URL
from hybrid_mlb import DiscoverySignal, HybridCandidateRequest, QCCheck, evaluate_candidate

ET = ZoneInfo("America/New_York")
GAME_SERIES = "KXMLBGAME"
ESPN_SCOREBOARD = "https://site.api.espn.com/apis/site/v2/sports/baseball/mlb/scoreboard"
ESPN_SUMMARY = "https://site.api.espn.com/apis/site/v2/sports/baseball/mlb/summary"
MLB_SCHEDULE = "https://statsapi.mlb.com/api/v1/schedule"
EVENT_RE = re.compile(r"^KXMLBGAME-(\d{2}[A-Z]{3}\d{2})(\d{4})([A-Z]{4,7})$")

ALIASES = {
    "ARI": "ARI", "AZ": "ARI", "ATH": "ATH", "OAK": "ATH", "ATL": "ATL",
    "BAL": "BAL", "BOS": "BOS", "CHC": "CHC", "CHW": "CWS", "CWS": "CWS",
    "CIN": "CIN", "CLE": "CLE", "COL": "COL", "DET": "DET", "HOU": "HOU",
    "KC": "KC", "KCR": "KC", "LAA": "LAA", "LAD": "LAD", "MIA": "MIA",
    "MIL": "MIL", "MIN": "MIN", "NYM": "NYM", "NYY": "NYY", "PHI": "PHI",
    "PIT": "PIT", "SD": "SD", "SDP": "SD", "SEA": "SEA", "SF": "SF",
    "SFG": "SF", "STL": "STL", "TB": "TB", "TBR": "TB", "TEX": "TEX",
    "TOR": "TOR", "WSH": "WSH",
}


def _norm(code: str | None) -> str:
    return ALIASES.get(str(code or "").upper(), str(code or "").upper())


def _american_probability(odds) -> float | None:
    try:
        value = float(str(odds).replace("+", ""))
    except (TypeError, ValueError):
        return None
    if value == 0:
        return None
    return 100 / (value + 100) if value > 0 else -value / (-value + 100)


def _novig(away_odds, home_odds) -> tuple[float | None, float | None]:
    away = _american_probability(away_odds)
    home = _american_probability(home_odds)
    total = (away or 0) + (home or 0)
    if away is None or home is None or total <= 0:
        return None, None
    return away / total, home / total


def _record_pct(records: list[dict], name: str = "overall") -> float | None:
    row = next((r for r in records or [] if str(r.get("name", "")).lower() == name.lower()), None)
    match = re.match(r"(\d+)-(\d+)", str((row or {}).get("summary", "")))
    if not match:
        return None
    wins, losses = map(int, match.groups())
    return wins / (wins + losses) if wins + losses else None


def _record_games(records: list[dict], name: str = "overall") -> int:
    row = next((r for r in records or [] if str(r.get("name", "")).lower() == name.lower()), None)
    match = re.match(r"(\d+)-(\d+)", str((row or {}).get("summary", "")))
    return sum(map(int, match.groups())) if match else 0


def _stat(competitor: dict, abbreviation: str) -> float | None:
    row = next((s for s in competitor.get("statistics", []) if s.get("abbreviation") == abbreviation), None)
    try:
        return float((row or {}).get("displayValue"))
    except (TypeError, ValueError):
        return None


def _log5(a: float | None, b: float | None) -> float:
    a = 0.5 if a is None else min(0.95, max(0.05, a))
    b = 0.5 if b is None else min(0.95, max(0.05, b))
    denominator = a + b - 2 * a * b
    return (a - a * b) / denominator if denominator else 0.5


def _independent_team_probability(away: dict, home: dict) -> tuple[float, dict]:
    away_overall = _record_pct(away.get("records", []))
    home_overall = _record_pct(home.get("records", []))
    away_road = _record_pct(away.get("records", []), "road")
    home_home = _record_pct(home.get("records", []), "home")
    overall = _log5(away_overall, home_overall)
    venue = _log5(away_road, home_home)
    probability = 0.6 * overall + 0.4 * venue

    away_games = _record_games(away.get("records", []))
    home_games = _record_games(home.get("records", []))
    away_rpg = (_stat(away, "R") or 0) / away_games if away_games else None
    home_rpg = (_stat(home, "R") or 0) / home_games if home_games else None
    away_era, home_era = _stat(away, "ERA"), _stat(home, "ERA")
    if away_rpg is not None and home_rpg is not None:
        probability += 0.04 * (away_rpg - home_rpg)
    if away_era is not None and home_era is not None:
        probability += 0.025 * (home_era - away_era)
    probability = min(0.80, max(0.20, probability))
    return round(probability, 4), {
        "away_overall": away_overall, "home_overall": home_overall,
        "away_road": away_road, "home_home": home_home,
        "away_runs_per_game": away_rpg, "home_runs_per_game": home_rpg,
        "away_era": away_era, "home_era": home_era,
    }


def _to_cents(value) -> int | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return round(number * 100) if 0 <= number <= 1 else round(number)


def _candle_close(block) -> float | None:
    if not isinstance(block, dict):
        return None
    for key in ("close", "close_dollars", "close_fp"):
        if block.get(key) not in (None, ""):
            return block[key]
    return None


async def _collect_pregame_closes(client: httpx.AsyncClient, records: list[dict], warnings: list[str]) -> dict[str, int]:
    specs = []
    for record in records:
        ticker = str(record.get("candidate_id") or "")
        try:
            start = datetime.fromisoformat(str(record.get("game_start_time") or "").replace("Z", "+00:00"))
            target_ts = int(start.timestamp())
        except (TypeError, ValueError):
            continue
        if ticker:
            specs.append((ticker, target_ts))

    output: dict[str, int] = {}
    for offset in range(0, len(specs), 10):
        chunk = specs[offset:offset + 10]
        try:
            payload = await _fetch_json(client, f"{KALSHI_BASE_URL}/markets/candlesticks", params={
                "market_tickers": ",".join(ticker for ticker, _ in chunk),
                "start_ts": min(target for _, target in chunk) - 3 * 60 * 60,
                "end_ts": max(target for _, target in chunk),
                "period_interval": 1,
                "include_latest_before_start": "false",
            })
            targets = dict(chunk)
            for row in payload.get("markets", []):
                ticker = str(row.get("market_ticker") or row.get("ticker") or "")
                target = targets.get(ticker)
                candles = [candle for candle in row.get("candlesticks", []) if target is not None and int(candle.get("end_period_ts") or 0) <= target]
                candle = max(candles, key=lambda item: int(item.get("end_period_ts") or 0)) if candles else None
                price = _to_cents(_candle_close((candle or {}).get("yes_ask")))
                if price is not None and 1 <= price <= 99:
                    output[ticker] = price
        except Exception as exc:
            warnings.append(f"Could not capture one pregame closing-price batch: {exc}")
    return output


def _date_token(date_value: str) -> str:
    return datetime.strptime(date_value, "%Y-%m-%d").strftime("%y%b%d").upper()


def _token_date(token: str) -> datetime:
    return datetime.strptime(token, "%y%b%d").replace(tzinfo=ET)


async def _fetch_json(client: httpx.AsyncClient, url: str, **kwargs) -> dict:
    response = await client.get(url, timeout=30, **kwargs)
    response.raise_for_status()
    return response.json()


async def build_automatic_game_card(
    target_date: str | None = None,
    minimum_edge_points: float = 5.0,
    estimated_cost_cents: float = 2.0,
) -> dict:
    captured_at = datetime.now(timezone.utc).isoformat()
    today = datetime.now(ET).strftime("%Y-%m-%d")
    requested_date = target_date or today
    warnings: list[str] = []
    source_health = {
        "kalshi_game_markets": {"status": "pending", "records": 0},
        "espn_schedule": {"status": "pending", "records": 0},
        "espn_draftkings_odds": {"status": "pending", "records": 0},
        "mlb_probable_pitchers": {"status": "pending", "records": 0},
        "record_era_model": {"status": "pending", "records": 0},
    }

    async with httpx.AsyncClient(headers={"User-Agent": "KalshiResearchDashboard/3.7.0"}) as client:
        try:
            kalshi_payload = await _fetch_json(client, f"{KALSHI_BASE_URL}/markets", params={"series_ticker": GAME_SERIES, "status": "open", "limit": 1000, "mve_filter": "exclude"})
            markets = kalshi_payload.get("markets", [])
            source_health["kalshi_game_markets"] = {"status": "ok", "records": len(markets)}
        except Exception as exc:
            markets = []
            source_health["kalshi_game_markets"] = {"status": "failed", "records": 0, "detail": str(exc)}

        tokens = sorted({m.group(1) for item in markets for m in [EVENT_RE.match(str(item.get("event_ticker", "")))] if m}, key=_token_date)
        wanted = _date_token(requested_date)
        if wanted not in tokens and target_date is None:
            future = [t for t in tokens if _token_date(t).date() >= datetime.now(ET).date()]
            wanted = future[0] if future else wanted
        selected_date = _token_date(wanted).strftime("%Y-%m-%d")
        slate_markets = [m for m in markets if str(m.get("event_ticker", "")).startswith(f"{GAME_SERIES}-{wanted}")]

        try:
            scoreboard = await _fetch_json(client, ESPN_SCOREBOARD, params={"dates": selected_date.replace("-", ""), "limit": 100})
            events = scoreboard.get("events", [])
            source_health["espn_schedule"] = {"status": "ok", "records": len(events)}
        except Exception as exc:
            events = []
            source_health["espn_schedule"] = {"status": "failed", "records": 0, "detail": str(exc)}

        try:
            mlb = await _fetch_json(client, MLB_SCHEDULE, params={"sportId": 1, "date": selected_date, "hydrate": "probablePitcher"})
            probable_games = [g for d in mlb.get("dates", []) for g in d.get("games", [])]
            source_health["mlb_probable_pitchers"] = {"status": "ok", "records": len(probable_games)}
        except Exception as exc:
            probable_games = []
            source_health["mlb_probable_pitchers"] = {"status": "failed", "records": 0, "detail": str(exc)}

        semaphore = asyncio.Semaphore(8)
        async def summary(event_id: str):
            async with semaphore:
                try:
                    return event_id, await _fetch_json(client, ESPN_SUMMARY, params={"event": event_id})
                except Exception:
                    return event_id, {}
        summaries = dict(await asyncio.gather(*(summary(str(e.get("id"))) for e in events)))

    probable_by_names = {}
    for game in probable_games:
        away = game.get("teams", {}).get("away", {})
        home = game.get("teams", {}).get("home", {})
        probable_by_names[(away.get("team", {}).get("name"), home.get("team", {}).get("name"))] = (
            (away.get("probablePitcher") or {}).get("fullName"), (home.get("probablePitcher") or {}).get("fullName")
        )

    event_map = {}
    for event in events:
        competition = (event.get("competitions") or [{}])[0]
        competitors = competition.get("competitors", [])
        away = next((c for c in competitors if c.get("homeAway") == "away"), None)
        home = next((c for c in competitors if c.get("homeAway") == "home"), None)
        if not away or not home:
            continue
        event_map[frozenset({_norm(away.get("team", {}).get("abbreviation")), _norm(home.get("team", {}).get("abbreviation"))})] = (event, away, home)

    grouped = {}
    for market in slate_markets:
        grouped.setdefault(str(market.get("event_ticker")), []).append(market)

    candidates = []
    odds_records = 0
    model_records = 0
    for event_ticker, pair in grouped.items():
        codes = {_norm(str(m.get("ticker", "")).rsplit("-", 1)[-1]) for m in pair}
        mapped = event_map.get(frozenset(codes))
        if not mapped or len(pair) < 2:
            continue
        event, away, home = mapped
        summary_data = summaries.get(str(event.get("id")), {})
        pick = (summary_data.get("pickcenter") or [None])[0]
        current_away = ((pick or {}).get("awayTeamOdds") or {}).get("moneyLine")
        current_home = ((pick or {}).get("homeTeamOdds") or {}).get("moneyLine")
        current_probs = _novig(current_away, current_home)
        moneyline = (pick or {}).get("moneyline") or {}
        open_probs = _novig(((moneyline.get("away") or {}).get("open") or {}).get("odds"), ((moneyline.get("home") or {}).get("open") or {}).get("odds"))
        if all(p is not None for p in current_probs):
            odds_records += 1

        away_model, model_detail = _independent_team_probability(away, home)
        model_records += 1
        model_probs = (away_model, round(1 - away_model, 4))
        away_name, home_name = away.get("team", {}).get("displayName"), home.get("team", {}).get("displayName")
        probables = probable_by_names.get((away_name, home_name), (None, None))

        start_text = event.get("date")
        try:
            start = datetime.fromisoformat(str(start_text).replace("Z", "+00:00"))
            pregame = start > datetime.now(timezone.utc)
        except (TypeError, ValueError):
            pregame = False
        pair_sum = sum(_to_cents(m.get("yes_ask_dollars") if m.get("yes_ask_dollars") is not None else m.get("yes_ask")) or 999 for m in pair)

        for market in pair:
            team_code = _norm(str(market.get("ticker", "")).rsplit("-", 1)[-1])
            is_away = team_code == _norm(away.get("team", {}).get("abbreviation"))
            index = 0 if is_away else 1
            team = away if is_away else home
            external = current_probs[index]
            model_probability = model_probs[index]
            open_probability = open_probs[index]
            move = (external - open_probability) * 100 if external is not None and open_probability is not None else 0.0
            price = _to_cents(market.get("yes_ask_dollars") if market.get("yes_ask_dollars") is not None else market.get("yes_ask"))
            if price is None:
                continue
            signals = [
                DiscoverySignal(source="ESPN/DraftKings no-vig moneyline", kind="SHARP_MARKET", independence_group="espn-dk", supports_candidate=bool(external is not None and external >= 0.5)),
                DiscoverySignal(source="Opening-to-current market movement", kind="SHARP_MARKET", independence_group="espn-dk", supports_candidate=move >= 1.0),
                DiscoverySignal(source="Odds-blind record/venue/ERA model", kind="MODEL", independence_group="record-era-model", supports_candidate=model_probability >= 0.5),
            ]
            qc = [
                QCCheck(label="Executable Kalshi game asks", status="PASS" if 2 <= price <= 98 and pair_sum <= 112 else "FAIL", note=f"Two-outcome ask sum {pair_sum} cents."),
                QCCheck(label="External no-vig moneyline", status="PASS" if external is not None else "PENDING"),
                QCCheck(label="Pregame market", status="PASS" if pregame else "FAIL"),
                QCCheck(label="Probable pitchers", status="PASS" if all(probables) else "PENDING", note=f"{probables[0] or 'TBD'} vs {probables[1] or 'TBD'}"),
            ]
            result = evaluate_candidate(HybridCandidateRequest(
                candidate_id=str(market.get("ticker")), market_type="GAME",
                selection=str(market.get("title") or f"{team.get('team', {}).get('displayName')} wins"),
                contract_side="YES", kalshi_price_cents=price,
                model_fair_probability=model_probability,
                external_market_probability=external,
                signals=signals, qc_checks=qc, minimum_edge_points=minimum_edge_points,
                market_move_points=round(move, 2),
                pricing_policy="MARKET_FIRST_V37",
                estimated_cost_cents=estimated_cost_cents,
            ))
            row = result.model_dump()
            row.update({
                "ticker": market.get("ticker"), "event_ticker": event_ticker,
                "matchup": f"{away_name} at {home_name}", "game_start_time": start_text,
                "team_code": team_code,
                "away_code": _norm(away.get("team", {}).get("abbreviation")),
                "home_code": _norm(home.get("team", {}).get("abbreviation")),
                "probable_pitchers": {"away": probables[0], "home": probables[1]},
                "model_detail": model_detail, "external_moneyline": current_away if is_away else current_home,
                "opening_external_probability": open_probability, "market_move_points": round(move, 2),
                "effective_entry_price_cents": min(99.0, round(price + estimated_cost_cents, 2)),
            })
            if row["decision"] != "PASS" or row["raw_edge_points"] >= 0:
                candidates.append(row)

    source_health["espn_draftkings_odds"] = {"status": "ok" if odds_records else "unavailable", "records": odds_records}
    source_health["record_era_model"] = {"status": "ok" if model_records else "unavailable", "records": model_records}
    candidates.sort(key=lambda r: ({"BUY": 0, "WATCH": 1, "PASS": 2}[r["decision"]], -r["raw_edge_points"]))
    if not candidates:
        warnings.append("No game candidates survived price, mapping, and pregame checks for the selected slate.")
    if any(v["status"] not in {"ok"} for v in source_health.values()):
        warnings.append("One or more free sources were unavailable; affected candidates were downgraded rather than promoted.")

    return {
        "version": "3.7.0", "mode": "paper-only", "snapshot_id": f"auto-{selected_date}-{int(datetime.now(timezone.utc).timestamp())}",
        "strategy": "MARKET_FIRST_V37", "estimated_cost_cents": estimated_cost_cents,
        "captured_at": captured_at, "requested_date": requested_date, "selected_date": selected_date,
        "markets_reviewed": len(slate_markets), "games_mapped": len(grouped), "source_health": source_health,
        "candidates": candidates[:16], "warnings": warnings,
    }


def _record_game_date(record: dict) -> str | None:
    value = record.get("game_start_time") or record.get("saved_at")
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo:
            parsed = parsed.astimezone(ET)
        return parsed.strftime("%Y-%m-%d")
    except (TypeError, ValueError):
        match = re.match(r"(\d{4}-\d{2}-\d{2})", str(value))
        return match.group(1) if match else None


async def settle_automatic_records(records: list[dict]) -> dict:
    """Settle timestamped paper candidates from free ESPN final scores.

    Stakes are interpreted as dollars risked buying YES at the captured ask.
    Records without a completed matching game remain pending and unchanged.
    """
    updated = [dict(record) for record in records]
    dates = sorted({date for record in updated if record.get("automatic") and record.get("profit_loss") is None for date in [_record_game_date(record)] if date})
    scoreboards: dict[str, dict] = {}
    warnings: list[str] = []
    closing_prices: dict[str, int] = {}
    async with httpx.AsyncClient(headers={"User-Agent": "KalshiResearchDashboard/3.7.0"}) as client:
        for date in dates:
            try:
                scoreboards[date] = await _fetch_json(client, ESPN_SCOREBOARD, params={"dates": date.replace("-", ""), "limit": 100})
            except Exception as exc:
                warnings.append(f"Could not fetch final scores for {date}: {exc}")
        closing_prices = await _collect_pregame_closes(
            client,
            [record for record in updated if record.get("automatic") and record.get("profit_loss") is None],
            warnings,
        )

    completed_by_date: dict[str, list[dict]] = {}
    for date, scoreboard in scoreboards.items():
        games = []
        for event in scoreboard.get("events", []):
            competition = (event.get("competitions") or [{}])[0]
            if not ((event.get("status") or {}).get("type") or {}).get("completed"):
                continue
            competitors = competition.get("competitors", [])
            codes = {_norm((row.get("team") or {}).get("abbreviation")) for row in competitors}
            winner = next((_norm((row.get("team") or {}).get("abbreviation")) for row in competitors if row.get("winner") is True), None)
            games.append({"codes": codes, "winner": winner, "event_id": event.get("id"), "start_time": event.get("date")})
        completed_by_date[date] = games

    settled_now = 0
    for record in updated:
        if not record.get("automatic") or record.get("profit_loss") is not None:
            continue
        date = _record_game_date(record)
        team_code = _norm(record.get("team_code"))
        expected_codes = {_norm(record.get("away_code")), _norm(record.get("home_code"))} - {""}
        matches = [game for game in completed_by_date.get(date or "", []) if team_code in game["codes"] and (len(expected_codes) < 2 or game["codes"] == expected_codes)]
        if len(matches) > 1 and record.get("game_start_time"):
            matches = [game for game in matches if game.get("start_time") == record.get("game_start_time")]
        if len(matches) != 1 or not matches[0].get("winner"):
            continue
        won = matches[0]["winner"] == team_code
        try:
            price = float(record.get("effective_entry_price_cents") or record.get("entry_price_cents"))
            stake = float(record.get("stake") or 1)
        except (TypeError, ValueError):
            continue
        if not 0 < price < 100 or stake <= 0:
            continue
        profit = stake * (100 - price) / price if won else -stake
        close_price = closing_prices.get(str(record.get("candidate_id") or ""))
        raw_entry = float(record.get("entry_price_cents") or price)
        record.update({
            "result": "WIN" if won else "LOSS",
            "profit_loss": round(profit, 4),
            "settled_at": datetime.now(timezone.utc).isoformat(),
            "settlement_source": "ESPN final score",
            "settlement_event_id": matches[0]["event_id"],
            "close_price_cents": close_price,
            "clv_cents": round(close_price - raw_entry, 2) if close_price is not None else None,
            "net_clv_cents": round(close_price - price, 2) if close_price is not None else None,
        })
        settled_now += 1

    settled = [r for r in updated if r.get("automatic") and r.get("profit_loss") is not None]
    total_staked = sum(float(r.get("stake") or 0) for r in settled)
    total_profit = sum(float(r.get("profit_loss") or 0) for r in settled)
    wins = sum(1 for r in settled if r.get("result") == "WIN")
    clv_values = [float(r["clv_cents"]) for r in settled if r.get("clv_cents") is not None]
    net_clv_values = [float(r["net_clv_cents"]) for r in settled if r.get("net_clv_cents") is not None]
    return {
        "version": "3.7.0",
        "records": updated,
        "settled_now": settled_now,
        "summary": {
            "tracked": sum(1 for r in updated if r.get("automatic")),
            "settled": len(settled),
            "pending": sum(1 for r in updated if r.get("automatic") and r.get("profit_loss") is None),
            "wins": wins,
            "losses": len(settled) - wins,
            "win_rate": round(wins / len(settled), 4) if settled else None,
            "total_staked": round(total_staked, 2),
            "profit_loss": round(total_profit, 2),
            "roi": round(total_profit / total_staked, 4) if total_staked else None,
            "average_clv_cents": round(sum(clv_values) / len(clv_values), 2) if clv_values else None,
            "average_net_clv_cents": round(sum(net_clv_values) / len(net_clv_values), 2) if net_clv_values else None,
            "clv_observations": len(clv_values),
        },
        "warnings": warnings,
        "methodology_note": "Prospective paper ROI from timestamped entry asks and final outcomes. v3.7 uses an effective entry price for estimated costs and captures the final pregame ask for closing-line value when available.",
    }
