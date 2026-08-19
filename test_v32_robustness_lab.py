from v32_robustness_lab import build_v32_robustness_lab


def _r(month, day, player, threshold, side, p, price, actual, proj=5.5):
    return dict(player=player, game_date=f'2026-{month}-{day:02d}', threshold=threshold, side=side,
                model_probability=p, entry_price_cents=price, actual_strikeouts=actual,
                stake=1.0, projected_strikeouts=proj, confidence_skill=75, confidence_lineup=75,
                confidence_workload=75, confidence_stability=75, confidence_recent=75)


def test_v32_builds_with_all_months():
    rows=[]
    for month in ['04','05','06','07']:
        for i in range(1,8):
            rows.append(_r(month,i,f'P{i}','5+','YES',.50,35,6 if i%2 else 3,5.5))
            rows.append(_r(month,i,f'P{i}','6+','YES',.40,25,6 if i%2 else 3,5.5))
    d=build_v32_robustness_lab(rows,5)
    assert d['version']=='3.2.0'
    assert len(d['folds'])==3
    assert len(d['edge_thresholds'])==5
    assert 'cluster_bootstrap' in d
    assert 'one_best_per_pitcher_game' in d
