from automatic_hybrid_card import _american_probability, _independent_team_probability, _novig


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
