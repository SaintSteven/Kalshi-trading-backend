from mlb_data_collector import expected_batters_faced, recent_start_rows, recent_summary, validate_workload

def row(bf, pitches, strikeouts=5):
    return {"date": "2026-07-01", "stat": {"gamesStarted": 1, "battersFaced": bf, "numberOfPitches": pitches, "strikeOuts": strikeouts}}

def test_aggregated_rows_are_rejected():
    accepted = recent_start_rows([row(157,626), row(24,96)])
    assert len(accepted) == 1 and accepted[0]["stat"]["battersFaced"] == 24

def test_recent_summary_uses_individual_starts():
    result = recent_summary([row(22,91,6), row(25,101,7), row(23,95,5)])
    assert result["recent_pitch_counts"] == [91,101,95]
    assert result["recent_batters_faced"] == 70

def test_expected_workload_is_realistic():
    logs=[row(22,91),row(25,101),row(23,95),row(24,98),row(21,88)]
    expected,floor,ceiling=expected_batters_faced({"gamesStarted":10,"battersFaced":235},logs)
    assert 20 <= expected <= 26 and floor <= expected <= ceiling and ceiling <= 34

def test_sanity_validation():
    assert validate_workload(24,19,29,[91,101,95]) == []
    assert len(validate_workload(157,155,36,[626])) == 3
