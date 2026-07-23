from projection_engine import build_full_projection
from pricing_engine import evaluate_market

def build_paper_card(markets,request):
    pmap={p.player.strip().lower():build_full_projection(p) for p in request.pitchers}
    recs=[evaluate_market(m,pmap.get(m.player.strip().lower()),request.minimum_edge_points) for m in markets]
    rank={'MODEL EDGE':0,'WATCH':1,'PASS':2,'INSUFFICIENT DATA':3}
    recs.sort(key=lambda x:(rank[x.decision],-(x.adjusted_edge_points if x.adjusted_edge_points is not None else -999),-x.confidence.get('overall',0),x.player,x.threshold))
    return recs,sum(1 for m in markets if m.player.strip().lower() in pmap)
