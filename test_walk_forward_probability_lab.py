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
