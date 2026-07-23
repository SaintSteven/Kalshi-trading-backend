from models import PaperRecommendation

def evaluate_market(m,p,min_edge):
    if not p or p['status']!='READY':
        return PaperRecommendation(ticker=m.ticker,player=m.player,threshold=m.threshold,side='NONE',market_price_cents=None,fair_probability=None,raw_edge_points=None,adjusted_edge_points=None,projected_strikeouts=None if not p else p['projected_strikeouts'],baseline_k_pct=None if not p else p['baseline_k_pct'],adjusted_k_pct=None if not p else p['adjusted_k_pct'],expected_batters_faced=None if not p else p['expected_batters_faced'],workload_floor=None if not p else p['workload_floor'],workload_ceiling=None if not p else p['workload_ceiling'],confidence={} if not p else p['confidence'],decision='INSUFFICIENT DATA',suggested_stake=0,reasons=['No validated projection available.'],warnings=[] if not p else p['warnings'])
    yf=p['ladder_probabilities'].get(m.threshold)
    if yf is None: side='NONE';price=fair=raw=adj=None;decision='PASS';reasons=['No simulated probability for this ladder.']
    else:
        nf=1-yf;ye=yf*100-m.yes_ask_cents if m.yes_ask_cents is not None else -999;ne=nf*100-m.no_ask_cents if m.no_ask_cents is not None else -999
        if ye>=ne:side='YES';price=m.yes_ask_cents;fair=yf*100;raw=ye
        else:side='NO';price=m.no_ask_cents;fair=nf*100;raw=ne
        adj=raw*p['confidence']['overall']/100;decision='PASS' if raw<0 else ('MODEL EDGE' if adj>=min_edge else 'WATCH');reasons=[f'Best side {side}.',f'Raw edge {raw:.1f}.',f'Adjusted edge {adj:.1f}.','Paper-only; stake remains $0.']
    return PaperRecommendation(ticker=m.ticker,player=m.player,threshold=m.threshold,side=side,market_price_cents=price,fair_probability=None if fair is None else round(fair,1),raw_edge_points=None if raw is None else round(raw,1),adjusted_edge_points=None if adj is None else round(adj,1),projected_strikeouts=p['projected_strikeouts'],baseline_k_pct=p['baseline_k_pct'],adjusted_k_pct=p['adjusted_k_pct'],expected_batters_faced=p['expected_batters_faced'],workload_floor=p['workload_floor'],workload_ceiling=p['workload_ceiling'],confidence=p['confidence'],decision=decision,suggested_stake=0,reasons=reasons,warnings=p['warnings'])
