from models import Market, PaperRecommendation, ProjectionResult
from probability_engine import poisson_probability_at_least

def threshold_number(value: str) -> int | None:
    stripped=value.rstrip("+").strip()
    return int(stripped) if stripped.isdigit() else None

def evaluate_market(market: Market, projection: ProjectionResult | None, minimum_edge_points: float, max_bet: float) -> PaperRecommendation:
    if projection is None or projection.status!="READY":
        return PaperRecommendation(ticker=market.ticker,player=market.player,threshold=market.threshold,side="NONE",market_price_cents=None,fair_probability=None,edge_points=None,projected_strikeouts=None,confidence=projection.confidence if projection else 0,decision="INSUFFICIENT DATA",suggested_stake=0.0,reasons=["No validated pitcher projection was supplied."])
    threshold=threshold_number(market.threshold)
    if threshold is None:
        return PaperRecommendation(ticker=market.ticker,player=market.player,threshold=market.threshold,side="NONE",market_price_cents=None,fair_probability=None,edge_points=None,projected_strikeouts=projection.projected_strikeouts,confidence=projection.confidence,decision="PASS",suggested_stake=0.0,reasons=["Could not parse the strikeout threshold."])
    yes_fair=poisson_probability_at_least(projection.projected_strikeouts,threshold); no_fair=1.0-yes_fair
    yes_edge=yes_fair*100-market.yes_ask_cents if market.yes_ask_cents is not None else -999
    no_edge=no_fair*100-market.no_ask_cents if market.no_ask_cents is not None else -999
    if yes_edge>=no_edge:
        side,price,fair,edge="YES",market.yes_ask_cents,yes_fair,yes_edge
    else:
        side,price,fair,edge="NO",market.no_ask_cents,no_fair,no_edge
    reasons=list(projection.reasons)+[f"Best raw side is {side} with {edge:.1f} percentage points of model edge."]
    if edge<0:
        decision="PASS"; reasons.append("Best available side is still priced above model fair value.")
    elif edge<minimum_edge_points:
        decision="WATCH"; reasons.append("Positive edge is below the paper-card threshold.")
    else:
        decision="MODEL EDGE"; reasons.append("Paper-only model edge. Real-money BUY labels remain disabled pending validation.")
    return PaperRecommendation(ticker=market.ticker,player=market.player,threshold=market.threshold,side=side,market_price_cents=price,fair_probability=round(fair*100,1),edge_points=round(edge,1),projected_strikeouts=projection.projected_strikeouts,confidence=projection.confidence,decision=decision,suggested_stake=0.0,reasons=reasons)
