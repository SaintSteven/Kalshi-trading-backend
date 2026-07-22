from models import PitcherProjectionInput, ProjectionResult
QUALITY_SCORE={"HIGH":90,"MEDIUM":72,"LOW":55}

def build_projection(data: PitcherProjectionInput) -> ProjectionResult:
    reasons=[]
    if not data.starter_confirmed:
        return ProjectionResult(player=data.player,projected_strikeouts=0.0,confidence=25,status="INSUFFICIENT_DATA",reasons=["Starter status is not confirmed."])
    if data.baseline_k_per_batter <= 0:
        return ProjectionResult(player=data.player,projected_strikeouts=0.0,confidence=20,status="INSUFFICIENT_DATA",reasons=["Baseline strikeout rate is missing or invalid."])
    mean=(data.baseline_k_per_batter*data.expected_batters_faced*data.opponent_k_multiplier*data.workload_multiplier*data.recent_form_multiplier)
    confidence=QUALITY_SCORE[data.data_quality]
    if data.expected_batters_faced < 18:
        confidence-=12; reasons.append("Short expected workload lowers confidence.")
    if abs(data.opponent_k_multiplier-1.0)>0.20:
        confidence-=5; reasons.append("Large opponent adjustment increases model uncertainty.")
    if abs(data.recent_form_multiplier-1.0)>0.15:
        confidence-=5; reasons.append("Large recent-form adjustment increases variance.")
    if not reasons: reasons.append("Projection inputs passed the basic validation checks.")
    return ProjectionResult(player=data.player,projected_strikeouts=round(mean,2),confidence=max(1,min(99,confidence)),status="READY",reasons=reasons)
