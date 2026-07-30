from __future__ import annotations

from collections import defaultdict
from edge_models import EdgeAnalysisRequest, EdgeAnalysisResponse, SegmentResult

PRICE_BUCKETS = [(1,20,"1-20¢"),(21,40,"21-40¢"),(41,60,"41-60¢"),(61,80,"61-80¢"),(81,99,"81-99¢")]
EDGE_BUCKETS = [(5,7.49,"5-7.4 pts"),(7.5,9.99,"7.5-9.9 pts"),(10,14.99,"10-14.9 pts"),(15,10_000,"15+ pts")]
CONF_BUCKETS = [(0,67.99,"LOW"),(68,79.99,"MEDIUM"),(80,100,"HIGH")]


def _won(r):
    threshold=int(r.threshold.rstrip('+'))
    yes=r.actual_strikeouts>=threshold
    return yes if r.side=='YES' else not yes

def _edge(r):
    return r.adjusted_edge_points if r.adjusted_edge_points is not None else r.model_probability*100-r.entry_price_cents

def _tier(r):
    if r.confidence_tier: return r.confidence_tier
    c=r.confidence
    if c is None: return 'UNRATED'
    return 'HIGH' if c>=80 else 'MEDIUM' if c>=68 else 'LOW'

def _profit(r, won):
    price=r.entry_price_cents/100
    stake=max(0.0,r.stake)
    if not stake: return 0.0,0.0
    contracts=stake/price
    return stake, (contracts*(1-price) if won else -stake)

def _name(v,buckets,other='Other'):
    for lo,hi,n in buckets:
        if lo<=v<=hi:return n
    return other

def _summ(rows, fee_rate):
    bets=len(rows); wins=sum(x['won'] for x in rows); risk=sum(x['risk'] for x in rows)
    gross=sum(x['gross'] for x in rows); fees=sum(abs(x['gross'])*fee_rate for x in rows); net=gross-fees
    edges=[x['edge'] for x in rows]; clv=[x['close']-x['price'] for x in rows if x['close'] is not None]
    return dict(bets=bets,wins=wins,losses=bets-wins,win_rate=wins/bets if bets else None,amount_risked=risk,net_profit=net,gross_profit=gross,fees=fees,roi=net/risk if risk else None,average_edge_points=sum(edges)/len(edges) if edges else None,average_clv_cents=sum(clv)/len(clv) if clv else None)

def _segment(name, rows, fee):
    s=_summ(rows,fee)
    return SegmentResult(segment=name,bets=s['bets'],wins=s['wins'],losses=s['losses'],win_rate=round(s['win_rate'],5) if s['win_rate'] is not None else None,amount_risked=round(s['amount_risked'],2),net_profit=round(s['net_profit'],2),roi=round(s['roi'],5) if s['roi'] is not None else None,average_edge_points=round(s['average_edge_points'],3) if s['average_edge_points'] is not None else None,average_clv_cents=round(s['average_clv_cents'],3) if s['average_clv_cents'] is not None else None)

def analyze_edges(request: EdgeAnalysisRequest)->EdgeAnalysisResponse:
    warnings=[]; qualified=[]
    for r in request.records:
        edge=_edge(r); confidence=r.confidence
        if edge<request.minimum_edge_points: continue
        if confidence is not None and confidence<request.minimum_confidence: continue
        won=_won(r); risk,gross=_profit(r,won)
        if risk<=0: continue
        qualified.append(dict(side=r.side,threshold=r.threshold,price=r.entry_price_cents,close=r.closing_price_cents,model=r.model_probability,edge=edge,confidence=confidence,tier=_tier(r),won=won,risk=risk,gross=gross,game_date=r.game_date,player=r.player))
    qualified.sort(key=lambda x:(x['game_date'],x['player'],x['threshold']))
    overall=_summ(qualified,request.fee_rate)
    equity=0.0; peak=0.0; max_dd=0.0
    for x in qualified:
        equity += x['gross']-abs(x['gross'])*request.fee_rate
        peak=max(peak,equity); max_dd=max(max_dd,peak-equity)
    def groups(key, names):
        d=defaultdict(list)
        for x in qualified:d[key(x)].append(x)
        return [_segment(n,d.get(n,[]),request.fee_rate) for n in names]
    by_side=groups(lambda x:x['side'],['YES','NO'])
    by_price=groups(lambda x:_name(x['price'],PRICE_BUCKETS),[b[2] for b in PRICE_BUCKETS])
    by_edge=groups(lambda x:_name(x['edge'],EDGE_BUCKETS),[b[2] for b in EDGE_BUCKETS])
    by_conf=groups(lambda x:x['tier'],['LOW','MEDIUM','HIGH','UNRATED'])
    ladders=sorted({x['threshold'] for x in qualified},key=lambda s:int(s.rstrip('+')))
    by_ladder=groups(lambda x:x['threshold'],ladders)
    if overall['bets']<50:warnings.append('Fewer than 50 settled qualifying trades were analyzed. Treat ROI as preliminary.')
    if not any(x['close'] is not None for x in qualified):warnings.append('No closing prices were supplied, so closing-line value could not be evaluated.')
    avg_model=sum(x['model'] for x in qualified)/len(qualified) if qualified else None
    avg_market=sum(x['price']/100 for x in qualified)/len(qualified) if qualified else None
    return EdgeAnalysisResponse(records_reviewed=len(request.records),qualifying_bets=overall['bets'],wins=overall['wins'],losses=overall['losses'],win_rate=round(overall['win_rate'],5) if overall['win_rate'] is not None else None,amount_risked=round(overall['amount_risked'],2),gross_profit=round(overall['gross_profit'],2),estimated_fees=round(overall['fees'],2),net_profit=round(overall['net_profit'],2),roi=round(overall['roi'],5) if overall['roi'] is not None else None,max_drawdown=round(max_dd,2),average_model_probability=round(avg_model,5) if avg_model is not None else None,average_market_probability=round(avg_market,5) if avg_market is not None else None,average_edge_points=round(overall['average_edge_points'],3) if overall['average_edge_points'] is not None else None,average_clv_cents=round(overall['average_clv_cents'],3) if overall['average_clv_cents'] is not None else None,by_side=by_side,by_price_bucket=by_price,by_edge_bucket=by_edge,by_confidence_tier=by_conf,by_ladder=by_ladder,warnings=warnings)
