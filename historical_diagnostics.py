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


def _directional_projection_error(r: HistoricalMarketRecord):
    """Positive means the projection error moved in the same direction as the wager."""
    if r.projected_strikeouts is None:
        return None
    err=float(r.projected_strikeouts)-float(r.actual_strikeouts)
    return err if r.side=='YES' else -err


def _month_label(r: HistoricalMarketRecord):
    s=str(r.game_date)
    return s[:7] if len(s)>=7 else s


def _projection_error_bucket(v: float):
    return _bucket(v,[-2,-1,0,1,2],['<-2','-2 to <-1','-1 to <0','0 to <1','1 to <2','2+'])


def _gap_bucket(v: float):
    return _bucket(v,[-1,0,1,2],['<-1','-1 to <0','0 to <1','1 to <2','2+'])


def _price_bucket_fine(v: float):
    return _bucket(v,[20,30,40,50,60],['01-19c','20-29c','30-39c','40-49c','50-59c','60c+'])


def _edge_bucket_fine(v: float):
    return _bucket(v,[7.5,10,12.5,15,20],['<7.5','7.5-9.9','10-12.4','12.5-14.9','15-19.9','20+'])


def _interaction(rows, fn_a, fn_b, min_bets=3):
    g=defaultdict(list)
    for r in rows:
        a=fn_a(r); b=fn_b(r)
        if a is None or b is None: continue
        g[(a,b)].append(r)
    out=[]
    for (a,b),vals in g.items():
        if len(vals)<min_bets: continue
        ps=_projection_summary(vals)
        s=_summary(vals)
        directional=[_directional_projection_error(r) for r in vals]
        directional=[x for x in directional if x is not None]
        out.append({
            'segment_a':a,'segment_b':b,**s,
            'projection_mae':ps.get('mae'),'projection_bias':ps.get('bias'),
            'avg_directional_projection_error':(sum(directional)/len(directional) if directional else None),
        })
    return sorted(out,key=lambda x:(str(x['segment_a']),str(x['segment_b'])))


def _loss_drivers(segment_map, limit=12):
    """Rank descriptive (overlapping) segments by dollar loss. Not additive attribution."""
    rows=[]
    for dimension,segments in segment_map.items():
        for s in segments:
            if (s.get('bets') or 0)<5: continue
            rows.append({
                'dimension':dimension,'segment':s.get('segment'), 'bets':s.get('bets'),
                'net_profit':s.get('net_profit'), 'roi':s.get('roi'),
                'avg_predicted':s.get('avg_predicted'), 'actual_rate':s.get('actual_rate'),
                'calibration_error':s.get('calibration_error'),
            })
    return sorted(rows,key=lambda x:(x.get('net_profit') if x.get('net_profit') is not None else 0))[:limit]


def build_model_error_lab(records: list[dict]):
    """v2.9 research-only diagnosis of where apparent edge is being manufactured.

    Uses only already-captured frozen qualifier records. It does not fit or promote a new model.
    """
    rows=[HistoricalMarketRecord(**r) for r in records]
    if not rows:
        raise ValueError('Model Error Lab requires frozen historical qualifier records.')
    projection_rows=[r for r in rows if r.projected_strikeouts is not None]
    if not projection_rows:
        raise ValueError('Model Error Lab requires v2.7.1+ diagnostic-capture fields.')

    directional=[_directional_projection_error(r) for r in projection_rows]
    directional=[x for x in directional if x is not None]
    favoring=sum(1 for x in directional if x>0)
    against=sum(1 for x in directional if x<0)

    by_month=_projection_segments(rows,_month_label)
    by_side=_projection_segments(rows,lambda r:r.side)
    by_ladder=_projection_segments(rows,lambda r:r.threshold)
    by_price=_projection_segments(rows,lambda r:_price_bucket_fine(r.entry_price_cents))
    by_model_prob=_projection_segments(rows,lambda r:_bucket(r.model_probability,[.30,.40,.50,.60],['<30%','30-39%','40-49%','50-59%','60%+']))
    by_claimed_edge=_projection_segments(rows,lambda r:_edge_bucket_fine((r.model_probability*100)-r.entry_price_cents))
    by_adjusted_edge=_projection_segments(rows,lambda r:_edge_bucket_fine(float(r.adjusted_edge_points or 0)))
    gap_rows=[r for r in rows if r.projection_side_gap is not None]
    by_gap=_projection_segments(gap_rows,lambda r:_gap_bucket(float(r.projection_side_gap)))
    by_directional_error=[]
    g=defaultdict(list)
    for r in projection_rows:
        v=_directional_projection_error(r)
        if v is not None:g[_projection_error_bucket(v)].append(r)
    for k,v in sorted(g.items(),key=lambda kv:str(kv[0])):
        d=_projection_segment_summary(v)
        d['segment']=k
        d['avg_directional_projection_error']=sum(_directional_projection_error(r) for r in v)/len(v)
        by_directional_error.append(d)

    by_confidence=_projection_segments(rows,lambda r:_bucket(float(r.confidence or 0),[75,80,85,90],['<75','75-79','80-84','85-89','90+']))

    interactions={
        'ladder_x_price':_interaction(rows,lambda r:r.threshold,lambda r:_price_bucket_fine(r.entry_price_cents)),
        'gap_x_price':_interaction(gap_rows,lambda r:_gap_bucket(float(r.projection_side_gap)),lambda r:_price_bucket_fine(r.entry_price_cents)),
        'month_x_side':_interaction(rows,_month_label,lambda r:r.side),
        'adjusted_edge_x_price':_interaction(rows,lambda r:_edge_bucket_fine(float(r.adjusted_edge_points or 0)),lambda r:_price_bucket_fine(r.entry_price_cents)),
        'confidence_x_directional_error':_interaction(projection_rows,lambda r:_bucket(float(r.confidence or 0),[75,80,85,90],['<75','75-79','80-84','85-89','90+']),lambda r:_projection_error_bucket(_directional_projection_error(r))),
    }

    segment_map={
        'month':by_month,'side':by_side,'ladder':by_ladder,'entry_price':by_price,
        'model_probability':by_model_prob,'claimed_edge':by_claimed_edge,'adjusted_edge':by_adjusted_edge,
        'projection_gap':by_gap,'directional_projection_error':by_directional_error,'confidence':by_confidence,
    }

    overall=_projection_segment_summary(rows)
    market_avg=sum(r.entry_price_cents/100 for r in rows)/len(rows)
    model_avg=sum(r.model_probability for r in rows)/len(rows)
    actual=sum(_won(r) for r in rows)/len(rows)
    brier=sum((r.model_probability-(1 if _won(r) else 0))**2 for r in rows)/len(rows)

    return {
        'version':'2.9.0','mode':'model-error-diagnostic-lab','records':len(rows),
        'overall':overall,
        'stage_assessment':{
            'avg_model_probability':model_avg,
            'avg_entry_market_probability':market_avg,
            'actual_win_rate':actual,
            'model_overprediction_points':(model_avg-actual)*100,
            'market_minus_actual_points':(market_avg-actual)*100,
            'brier_score':brier,
            'projection_mae':overall.get('projection_mae'),
            'projection_bias':overall.get('projection_bias'),
            'avg_directional_projection_error':(sum(directional)/len(directional) if directional else None),
            'projection_error_favored_wager_rate':(favoring/len(directional) if directional else None),
            'projection_error_opposed_wager_rate':(against/len(directional) if directional else None),
        },
        'segments':segment_map,
        'interactions':interactions,
        'largest_descriptive_loss_segments':_loss_drivers(segment_map),
        'notes':[
            'All tables use the already-saved frozen qualifier records; no historical rerun or candidate fitting is performed.',
            'Directional projection error is positive when the projection miss moved in the same direction as the wager (YES: projected minus actual; NO: actual minus projected).',
            'Loss-driver rows overlap across dimensions and are descriptive, not additive causal attribution.',
            'Interaction tables require at least 3 bets per cell to reduce one-off noise.',
            'This lab is diagnostic only and does not promote a betting model.',
        ],
    }
