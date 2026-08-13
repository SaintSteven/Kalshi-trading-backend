from __future__ import annotations
from collections import defaultdict
from math import sqrt
from edge_models import HistoricalMarketRecord, EdgeAnalysisRequest
from edge_engine import analyze_edges


def _won(r: HistoricalMarketRecord) -> bool:
    n=int(str(r.threshold).rstrip('+'))
    yes=r.actual_strikeouts>=n
    return yes if r.side=='YES' else not yes

def _summary(rows):
    if not rows: return {'bets':0,'wins':0,'win_rate':None,'net_profit':0.0,'roi':None,'avg_predicted':None,'actual_rate':None,'calibration_error':None}
    a=analyze_edges(EdgeAnalysisRequest(records=rows,minimum_edge_points=0,minimum_confidence=0,fee_rate=0)).model_dump()
    avg=sum(r.model_probability for r in rows)/len(rows)
    actual=sum(_won(r) for r in rows)/len(rows)
    return {'bets':len(rows),'wins':sum(_won(r) for r in rows),'win_rate':actual,'net_profit':a.get('net_profit',0),'roi':a.get('roi'),'avg_predicted':avg,'actual_rate':actual,'calibration_error':actual-avg}

def _bucket(v, cuts, labels):
    for i,c in enumerate(cuts):
        if v < c:return labels[i]
    return labels[-1]

def _segments(rows, fn):
    g=defaultdict(list)
    for r in rows:g[fn(r)].append(r)
    return [{'segment':k,**_summary(v)} for k,v in sorted(g.items(),key=lambda kv:str(kv[0]))]

def _calibration(rows):
    g=defaultdict(list)
    for r in rows:
        p=r.model_probability
        lo=int(p*10)*10
        if lo>=100:lo=90
        g[f'{lo}-{lo+10}%'].append(r)
    out=[]
    for k,v in sorted(g.items(),key=lambda kv:int(kv[0].split('-')[0])):
        s=_summary(v); out.append({'bucket':k,**s})
    brier=sum((r.model_probability-(1 if _won(r) else 0))**2 for r in rows)/len(rows) if rows else None
    mae=sum(abs((1 if _won(r) else 0)-r.model_probability) for r in rows)/len(rows) if rows else None
    return {'brier_score':brier,'mean_absolute_probability_error':mae,'buckets':out}

def build_diagnostics(records: list[dict], strategy='unlimited_model'):
    rows=[HistoricalMarketRecord(**r) for r in records]
    raw_deltas=[(r.model_probability-r.raw_model_probability) for r in rows if r.raw_model_probability is not None]
    result={
      'strategy':strategy,'overall':_summary(rows),'calibration':_calibration(rows),
      'by_side':_segments(rows,lambda r:r.side),
      'by_ladder':_segments(rows,lambda r:r.threshold),
      'by_price_band':_segments(rows,lambda r:_bucket(r.entry_price_cents,[20,40,60,80],['01-19c','20-39c','40-59c','60-79c','80-99c'])),
      'by_edge_band':_segments(rows,lambda r:_bucket(float(r.adjusted_edge_points or 0),[10,15,20,30],['<10','10-14.9','15-19.9','20-29.9','30+'])),
      'by_confidence_band':_segments(rows,lambda r:_bucket(float(r.confidence or 0),[70,75,80,85,90],['<70','70-74','75-79','80-84','85-89','90+'])),
      'by_selector_rank':_segments([r for r in rows if r.selector_rank is not None],lambda r:str(r.selector_rank)),
      'calibration_shift': {'observations':len(raw_deltas),'average_calibrated_minus_raw_probability':(sum(raw_deltas)/len(raw_deltas) if raw_deltas else None)},
      'availability': {
        'available_now':['side','ladder','entry price','adjusted edge','overall confidence','raw fair probability','calibrated fair probability','selector rank'],
        'not_captured_in_v2_6_8_checkpoint':['projection-to-line gap','Skill component','Lineup component','Workload component','Stability component','Recent component'],
        'note':'The missing fields cannot be reconstructed faithfully from the completed July checkpoint without rerunning the frozen historical model. They are intentionally not guessed.'
      }
    }
    return result
