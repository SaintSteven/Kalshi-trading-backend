from types import SimpleNamespace
from v33_forward_validation import score_recommendations, summarize_state


def _history():
    rows=[]
    for i in range(30):
        rows.append({
            'player':f'P{i}','game_date':f'2026-07-{(i%30)+1:02d}','threshold':'6+','side':'YES' if i%2==0 else 'NO',
            'model_probability':0.45,'entry_price_cents':30+(i%5),'actual_strikeouts':4+(i%5),'stake':1.0,
            'projected_strikeouts':5.0+(i%4)*0.4,'confidence_skill':80,'confidence_lineup':72,
            'confidence_workload':78,'confidence_stability':75,'confidence_recent':70,
        })
    return rows


def test_v33_scores_and_primary_summary():
    rec=SimpleNamespace(ticker='T1',player='Test Pitcher',threshold='6+',side='YES',market_price_cents=20,
        calibrated_fair_probability=.45,fair_probability=.5,projected_strikeouts=6.5,matchup='A @ B',game_start_display='7:00 PM ET',
        confidence={'overall':80,'pitcher_skill':82,'lineup':72,'workload':80,'workload_stability':76,'recent_change':72})
    out=score_recommendations(_history(),[rec],'2026-08-19','source')
    assert out['version']=='3.3.1'
    assert len(out['scored'])==1
    row=out['scored'][0]
    assert 'residual_edge_points' in row
    state={'captures':[dict(row,status='SETTLED',won=1,net_profit=1.0)],'target_primary_settled':100}
    s=summarize_state(state)
    assert s['all_5pt']['settled'] in (0,1)
