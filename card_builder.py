from models import Market, PaperCardRequest
from pricing_engine import evaluate_market
from projection_engine import build_projection

def build_paper_card(markets: list[Market], request: PaperCardRequest):
    projection_map={item.player.strip().lower():build_projection(item) for item in request.projections}
    recommendations=[evaluate_market(m,projection_map.get(m.player.strip().lower()),request.minimum_edge_points,request.max_bet) for m in markets]
    recommendations.sort(key=lambda x:(x.decision!="MODEL EDGE",-(x.edge_points if x.edge_points is not None else -999),-x.confidence,x.player))
    matched=sum(1 for m in markets if m.player.strip().lower() in projection_map)
    return recommendations,matched
