import asyncio

import automatic_hybrid_card
from automatic_hybrid_card import _american_probability, _independent_team_probability, _novig, settle_automatic_records


def competitor(overall, split_name, split, runs, era):
    return {
        "records": [{"name": "overall", "summary": overall}, {"name": split_name, "summary": split}],
        "statistics": [{"abbreviation": "R", "displayValue": str(runs)}, {"abbreviation": "ERA", "displayValue": str(era)}],
    }


def test_american_probability():
    assert round(_american_probability(-150), 3) == 0.6
    assert round(_american_probability(150), 3) == 0.4


def test_novig_probabilities_sum_to_one():
    away, home = _novig(148, -179)
    assert round(away + home, 8) == 1
    assert home > away


def test_independent_model_favors_stronger_team():
    away = competitor("80-58", "road", "38-31", 680, 3.60)
    home = competitor("62-76", "home", "35-34", 550, 4.70)
    probability, detail = _independent_team_probability(away, home)
    assert probability > 0.5
    assert detail["away_era"] == 3.6


def test_independent_model_handles_malformed_record():
    away = competitor("bad", "road", "bad", 10, 3.60)
    home = competitor("10-10", "home", "5-5", 80, 4.20)
    probability, _ = _independent_team_probability(away, home)
    assert 0.2 <= probability <= 0.8


def test_settlement_uses_final_score_and_entry_risk(monkeypatch):
    async def fake_fetch(_client, _url, **_kwargs):
        return {"events": [{
            "id": "game-1",
            "date": "2026-08-31T23:05Z",
            "status": {"type": {"completed": True}},
            "competitions": [{"competitors": [
                {"winner": True, "team": {"abbreviation": "NYY"}},
                {"winner": False, "team": {"abbreviation": "BOS"}},
            ]}],
        }]}

    monkeypatch.setattr(automatic_hybrid_card, "_fetch_json", fake_fetch)
    payload = asyncio.run(settle_automatic_records([{
        "automatic": True,
        "candidate_id": "KXMLBGAME-TEST-NYY",
        "team_code": "NYY",
        "away_code": "NYY",
        "home_code": "BOS",
        "game_start_time": "2026-08-31T23:05Z",
        "entry_price_cents": 60,
        "stake": 1,
        "profit_loss": None,
    }]))
    record = payload["records"][0]
    assert record["result"] == "WIN"
    assert record["profit_loss"] == 0.6667
    assert payload["summary"]["roi"] == 0.6667
