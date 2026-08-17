from walk_forward_probability_lab import build_walk_forward_lab

def rec(date, p=.45, ask=35, actual=6, threshold='6+', proj=5.7):
    return {
        'game_date':date,'player':'P','threshold':threshold,'side':'YES','model_probability':p,
        'entry_price_cents':ask,'actual_strikeouts':actual,'stake':1.0,'projected_strikeouts':proj,
    }

def test_walk_forward_lab_runs_all_three_folds():
    records=[]
    for month in ['04','05','06','07']:
        for i in range(1,5):
            records.append(rec(f'2026-{month}-{i:02d}', p=.35+.03*i, ask=25+i, actual=6 if i%2 else 4, proj=5.2+.2*i))
    d=build_walk_forward_lab(records,5)
    assert d['version']=='2.8.1'
    assert len(d['folds'])==3
    assert d['month_counts']['2026-04']==4
    assert len(d['summary'])==4


def test_v3_challenger_uses_full_universe_and_walks_forward():
    from v3_challenger_lab import build_v3_challenger_lab
    rows=[]
    for month in ['04','05','06','07']:
        for i in range(20):
            actual=4+(i%4)
            proj=actual+((-1)**i)*0.7
            threshold='5+'
            yes=actual>=5
            side='YES' if i%2==0 else 'NO'
            rows.append({
                'player':f'P{month}{i}','game_date':f'2026-{month}-{(i%20)+1:02d}','threshold':threshold,
                'side':side,'model_probability':0.48,'raw_model_probability':0.50,'entry_price_cents':38,
                'actual_strikeouts':actual,'stake':1.0,'projected_strikeouts':proj,
            })
    result=build_v3_challenger_lab(rows,5)
    assert result['version']=='3.0.0'
    assert result['records']==80
    assert len(result['folds'])==3
    assert any(x['method']=='v3_market_prior_posterior' for x in result['summary'])
    for fold in result['folds']:
        assert 0 <= fold['fit']['market_prior_weight'] <= 1
        assert fold['fit']['projection_sigma'] > 0
