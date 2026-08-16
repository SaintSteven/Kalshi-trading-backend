from probability_engine_lab import build_probability_lab

def rec(date,p=.45,price=35,k=5,actual=5,side='YES',proj=5.2):
    return dict(player='P',game_date=date,threshold=f'{k}+',side=side,model_probability=p,raw_model_probability=p,entry_price_cents=price,actual_strikeouts=actual,stake=1.0,projected_strikeouts=proj)

def test_two_way_probability_lab_runs():
    rows=[]
    for i in range(20):
        rows.append(rec(f'2026-06-{i%28+1:02d}',p=.45+(i%3)*.02,actual=5 if i%3 else 3,proj=5.4))
        rows.append(rec(f'2026-07-{i%28+1:02d}',p=.44+(i%4)*.02,actual=5 if i%2 else 4,proj=5.3))
    d=build_probability_lab(rows)
    assert d['records']==40
    assert len(d['folds'])==2
    assert {x['method'] for x in d['summary']}=={'baseline','affine','market_shrink','projection_empirical'}
