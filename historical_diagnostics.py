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

def _projection_summary(rows):
    vals=[r for r in rows if r.projected_strikeouts is not None]
    if not vals:
        return {'observations':0,'mae':None,'bias':None,'rmse':None,'avg_projected':None,'avg_actual':None}
    errors=[float(r.projected_strikeouts)-float(r.actual_strikeouts) for r in vals]
    return {
        'observations':len(vals),
        'mae':sum(abs(x) for x in errors)/len(errors),
        'bias':sum(errors)/len(errors),
        'rmse':sqrt(sum(x*x for x in errors)/len(errors)),
        'avg_projected':sum(float(r.projected_strikeouts) for r in vals)/len(vals),
        'avg_actual':sum(float(r.actual_strikeouts) for r in vals)/len(vals),
    }

def _projection_segment_summary(rows):
    base=_summary(rows)
    ps=_projection_summary(rows)
    return {**base,**{f'projection_{k}':v for k,v in ps.items()}}

def _projection_segments(rows, fn):
    g=defaultdict(list)
    for r in rows:g[fn(r)].append(r)
    return [{'segment':k,**_projection_segment_summary(v)} for k,v in sorted(g.items(),key=lambda kv:str(kv[0]))]

def _component_segments(rows, attr):
    vals=[r for r in rows if getattr(r,attr,None) is not None]
    return _projection_segments(vals, lambda r:_bucket(float(getattr(r,attr)),[60,70,80,90],['<60','60-69','70-79','80-89','90+']))

def build_diagnostics(records: list[dict], strategy='unlimited_model'):
    rows=[HistoricalMarketRecord(**r) for r in records]
    raw_deltas=[(r.model_probability-r.raw_model_probability) for r in rows if r.raw_model_probability is not None]
    projection_rows=[r for r in rows if r.projected_strikeouts is not None]
    gap_rows=[r for r in rows if r.projection_side_gap is not None]
    result={
      'strategy':strategy,'overall':_summary(rows),'calibration':_calibration(rows),
      'by_side':_segments(rows,lambda r:r.side),
      'by_ladder':_segments(rows,lambda r:r.threshold),
      'by_price_band':_segments(rows,lambda r:_bucket(r.entry_price_cents,[20,40,60,80],['01-19c','20-39c','40-59c','60-79c','80-99c'])),
      'by_edge_band':_segments(rows,lambda r:_bucket(float(r.adjusted_edge_points or 0),[10,15,20,30],['<10','10-14.9','15-19.9','20-29.9','30+'])),
      'by_confidence_band':_segments(rows,lambda r:_bucket(float(r.confidence or 0),[70,75,80,85,90],['<70','70-74','75-79','80-84','85-89','90+'])),
      'by_selector_rank':_segments([r for r in rows if r.selector_rank is not None],lambda r:str(r.selector_rank)),
      'calibration_shift': {'observations':len(raw_deltas),'average_calibrated_minus_raw_probability':(sum(raw_deltas)/len(raw_deltas) if raw_deltas else None)},
      'projection_accuracy': _projection_summary(projection_rows),
      'projection_by_side': _projection_segments(projection_rows,lambda r:r.side),
      'projection_by_ladder': _projection_segments(projection_rows,lambda r:r.threshold),
      'by_projection_side_gap': _projection_segments(gap_rows,lambda r:_bucket(float(r.projection_side_gap),[-1,0,1,2],['<-1','-1 to <0','0 to <1','1 to <2','2+'])),
      'component_diagnostics': {
          'skill': _component_segments(rows,'confidence_skill'),
          'lineup': _component_segments(rows,'confidence_lineup'),
          'workload': _component_segments(rows,'confidence_workload'),
          'stability': _component_segments(rows,'confidence_stability'),
          'recent': _component_segments(rows,'confidence_recent'),
      },
      'availability': {
        'available_now':['side','ladder','entry price','adjusted edge','overall confidence','raw fair probability','calibrated fair probability','selector rank'],
        'diagnostic_capture_available':['projected strikeouts','actual strikeouts','projection error','projection-to-threshold gap','Skill component','Lineup component','Workload component','Stability component','Recent component'] if projection_rows else [],
        'not_captured_in_v2_6_8_checkpoint':([] if projection_rows else ['projection-to-line gap','Skill component','Lineup component','Workload component','Stability component','Recent component']),
        'note':('v2.7.1 diagnostic fields are present in this checkpoint.' if projection_rows else 'This checkpoint predates v2.7.1 diagnostic capture. Rerun the frozen historical model to populate projection and component diagnostics.')
      }
    }
    return result
