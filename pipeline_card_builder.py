from pricing_engine import evaluate_market

def build_card_from_pipeline(markets,request,pipeline):
    rec=[evaluate_market(m,pipeline.projections.get(m.player.strip().lower()),request.minimum_edge_points) for m in markets]
    rank={"MODEL EDGE":0,"WATCH":1,"PASS":2,"INSUFFICIENT DATA":3}
    rec.sort(key=lambda x:(rank[x.decision],-(x.adjusted_edge_points if x.adjusted_edge_points is not None else -999),-x.confidence.get("overall",0),x.player,x.threshold))
    matched=sum(1 for m in markets if m.player.strip().lower() in pipeline.projections)
    return rec,matched
