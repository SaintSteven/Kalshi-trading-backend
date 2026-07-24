from projection_inputs import DataQuality, PitcherModelInput

def infer_quality(raw: dict) -> DataQuality:
    career_bf = raw.get("career_batters_faced", 0)
    season_bf = raw.get("season_batters_faced", 0)
    recent_starts = raw.get("recent_starts", 0)
    warnings = raw.get("data_warnings", [])
    skill = "LOW" if career_bf < 300 else "MEDIUM" if season_bf < 200 else "HIGH"
    workload = "LOW" if warnings or recent_starts < 2 else "MEDIUM" if recent_starts < 5 else "HIGH"
    return DataQuality(pitcher_skill=skill, lineup="LOW", workload=workload, recent_change="LOW")

def automatic_input(raw: dict) -> PitcherModelInput:
    return PitcherModelInput(
        player=raw["player"], pitcher_hand=raw.get("pitcher_hand", "R"), starter_confirmed=raw.get("starter_confirmed", False),
        season_k_pct=raw.get("season_k_pct", 0.0), career_k_pct=raw.get("career_k_pct", 0.0), recent_k_pct=raw.get("recent_k_pct", 0.0),
        season_batters_faced=raw.get("season_batters_faced", 0), career_batters_faced=raw.get("career_batters_faced", 0), recent_batters_faced=raw.get("recent_batters_faced", 0),
        expected_batters_faced=raw.get("expected_batters_faced", 22.0), workload_floor=raw.get("workload_floor", 16), workload_ceiling=raw.get("workload_ceiling", 28),
        recent_pitch_counts=raw.get("recent_pitch_counts", []), opponent_lineup_k_pct=raw.get("opponent_lineup_k_pct", 0.225), league_k_pct=raw.get("league_k_pct", 0.225),
        lineup_confirmed=raw.get("lineup_confirmed", False), velocity_change_mph=raw.get("velocity_change_mph", 0.0), whiff_rate_change=raw.get("whiff_rate_change", 0.0),
        pitch_mix_change_supported=raw.get("pitch_mix_change_supported", False), starter_role=raw.get("starter_role", "NORMAL"), data_quality=infer_quality(raw),
        notes=" | ".join(raw.get("source_notes", []) + raw.get("data_warnings", [])),
    )
