"""v3.2 robustness/validation lab for the frozen v3.1 residual-edge candidate.

No model coefficients are changed here. The lab replays the exact v3.1 walk-forward
predictions and stress-tests independence, thresholds, concentration, and uncertainty.
"""
from collections import defaultdict
from random import Random

from edge_models import HistoricalMarketRecord
from probability_engine_lab import _brier, _logloss, _market_p, _won
from v3_challenger_lab import _fit_projection
from v31_residual_edge_lab import _fit_logistic, _predict


def _month(rows, prefix):
    return [r for r in rows if str(r.game_date).startswith(prefix)]


def _pnl(r):
    price = r.entry_price_cents / 100.0
    stake = max(0.0, float(r.stake or 0.0))
    if stake <= 0 or price <= 0:
        return 0.0, 0.0, 0
    won = _won(r)
    contracts = stake / price
    pnl = contracts * (1.0 - price) if won else -stake
    return pnl, stake, won


def _trade_rows(scored, edge_points):
    out = []
    for item in scored:
        r = item['record']
        p = item['probability']
        edge = 100.0 * p - r.entry_price_cents
        if edge < edge_points:
            continue
        pnl, risk, won = _pnl(r)
        if risk <= 0:
            continue
        out.append({**item, 'edge_points': edge, 'pnl': pnl, 'risk': risk, 'won': won})
    return out


def _summary(trades):
    risk = sum(x['risk'] for x in trades)
    pnl = sum(x['pnl'] for x in trades)
    wins = sum(x['won'] for x in trades)
    return {
        'bets': len(trades),
        'wins': wins,
        'win_rate': wins / len(trades) if trades else None,
        'risked': risk,
        'net_profit': pnl,
        'roi': pnl / risk if risk else None,
        'unique_pitcher_games': len({(x['record'].game_date, x['record'].player) for x in trades}),
    }


def _bucket_summary(trades, key_fn):
    groups = defaultdict(list)
    for t in trades:
        groups[str(key_fn(t))].append(t)
    out = []
    for k, vals in groups.items():
        s = _summary(vals)
        out.append({'segment': k, **s})
    return out


def _one_best_per_pitcher_game(trades):
    best = {}
    for t in trades:
        key = (t['record'].game_date, t['record'].player)
        prev = best.get(key)
        if prev is None or t['edge_points'] > prev['edge_points']:
            best[key] = t
    return list(best.values())


def _cluster_bootstrap_roi(trades, iterations=1000, seed=3102026):
    groups = defaultdict(list)
    for t in trades:
        groups[(t['record'].game_date, t['record'].player)].append(t)
    keys = list(groups)
    if not keys:
        return {'iterations': iterations, 'clusters': 0, 'roi_p025': None, 'roi_median': None, 'roi_p975': None, 'prob_roi_positive': None}
    rng = Random(seed)
    rois = []
    for _ in range(iterations):
        risk = pnl = 0.0
        for _j in range(len(keys)):
            k = keys[rng.randrange(len(keys))]
            for t in groups[k]:
                risk += t['risk']; pnl += t['pnl']
        if risk > 0:
            rois.append(pnl / risk)
    rois.sort()
    def q(frac):
        if not rois: return None
        idx = min(len(rois)-1, max(0, int(round(frac*(len(rois)-1)))))
        return rois[idx]
    return {
        'iterations': iterations,
        'clusters': len(keys),
        'roi_p025': q(.025),
        'roi_median': q(.5),
        'roi_p975': q(.975),
        'prob_roi_positive': sum(1 for x in rois if x > 0) / len(rois) if rois else None,
    }


def _score_fold(name, train, test):
    a, b, sigma = _fit_projection(train)
    w = _fit_logistic(train, a, b, sigma)
    scored = []
    probs = []
    market = []
    for r in test:
        p, _pb, pm = _predict(r, w, a, b, sigma)
        probs.append(p); market.append(pm)
        scored.append({'record': r, 'probability': p, 'market_probability': pm, 'fold': name})
    return {
        'fold': name,
        'train_records': len(train),
        'test_records': len(test),
        'fit': {'projection_sigma': sigma, 'coefficients': w},
        'brier': _brier(test, probs),
        'log_loss': _logloss(test, probs),
        'market_brier': _brier(test, market),
        'market_log_loss': _logloss(test, market),
        'scored': scored,
    }


def build_v32_robustness_lab(records, minimum_edge_points=5.0):
    rows = [HistoricalMarketRecord(**r) for r in records]
    ms = {m: _month(rows, m) for m in ['2026-04','2026-05','2026-06','2026-07']}
    if any(not v for v in ms.values()):
        raise ValueError('v3.2 requires April-July full-universe records.')

    folds = [
        _score_fold('Fit April → Test May', ms['2026-04'], ms['2026-05']),
        _score_fold('Fit Apr+May → Test June', ms['2026-04']+ms['2026-05'], ms['2026-06']),
        _score_fold('Fit Apr+May+June → Test July', ms['2026-04']+ms['2026-05']+ms['2026-06'], ms['2026-07']),
    ]
    all_scored = [x for f in folds for x in f['scored']]
    base_trades = _trade_rows(all_scored, minimum_edge_points)

    fold_results = []
    for f in folds:
        trades = _trade_rows(f['scored'], minimum_edge_points)
        one_best = _one_best_per_pitcher_game(trades)
        fold_results.append({
            'fold': f['fold'], 'train_records': f['train_records'], 'test_records': f['test_records'],
            'brier': f['brier'], 'market_brier': f['market_brier'],
            'all_5pt': _summary(trades), 'one_best_per_pitcher_game': _summary(one_best),
        })

    thresholds = []
    for e in [5.0, 7.5, 10.0, 12.5, 15.0]:
        ts = _trade_rows(all_scored, e)
        fold_rois = []
        for f in folds:
            s = _summary(_trade_rows(f['scored'], e))
            fold_rois.append({'fold': f['fold'], **s})
        thresholds.append({'edge_points': e, **_summary(ts), 'positive_folds': sum(1 for x in fold_rois if (x['roi'] or 0) > 0), 'folds': fold_rois})

    one_best = _one_best_per_pitcher_game(base_trades)

    # Descriptive only. These are not promotion filters.
    by_month = _bucket_summary(base_trades, lambda t: str(t['record'].game_date)[:7])
    by_side = _bucket_summary(base_trades, lambda t: t['record'].side)
    by_ladder = _bucket_summary(base_trades, lambda t: t['record'].threshold)
    by_price = _bucket_summary(base_trades, lambda t: f"{(t['record'].entry_price_cents//10)*10:02d}-{(t['record'].entry_price_cents//10)*10+9:02d}c")
    by_residual_edge = _bucket_summary(base_trades, lambda t: ('5-7.4' if t['edge_points'] < 7.5 else '7.5-9.9' if t['edge_points'] < 10 else '10-12.4' if t['edge_points'] < 12.5 else '12.5-14.9' if t['edge_points'] < 15 else '15+'))

    # Concentration: how many simultaneous qualifying ladders per pitcher-game.
    counts = defaultdict(int)
    for t in base_trades:
        counts[(t['record'].game_date, t['record'].player)] += 1
    by_ladder_count = _bucket_summary(base_trades, lambda t: f"{counts[(t['record'].game_date,t['record'].player)]} qualifying ladder(s)")

    market_brier = sum(f['market_brier'] for f in folds) / len(folds)
    v31_brier = sum(f['brier'] for f in folds) / len(folds)
    base_summary = _summary(base_trades)
    one_best_summary = _summary(one_best)
    bootstrap = _cluster_bootstrap_roi(base_trades)
    one_best_bootstrap = _cluster_bootstrap_roi(one_best)

    return {
        'version': '3.2.0',
        'mode': 'v31-robustness-validation',
        'records': len(rows),
        'minimum_edge_points': minimum_edge_points,
        'candidate_frozen': 'v3.1 residual-edge coefficients re-fit train-only exactly as in v3.1; no new predictive features or filters',
        'mean_brier': v31_brier,
        'market_mean_brier': market_brier,
        'aggregate_5pt': base_summary,
        'one_best_per_pitcher_game': one_best_summary,
        'folds': fold_results,
        'edge_thresholds': thresholds,
        'cluster_bootstrap': bootstrap,
        'one_best_cluster_bootstrap': one_best_bootstrap,
        'diagnostics': {
            'by_month': by_month,
            'by_side': by_side,
            'by_ladder': by_ladder,
            'by_entry_price': by_price,
            'by_residual_edge': by_residual_edge,
            'by_simultaneous_ladders': by_ladder_count,
        },
        'guardrails': [
            'v3.1 is frozen for this validation; no predictive feature, coefficient rule, side rule, ladder rule, or price filter is added.',
            'Breakdowns are descriptive diagnostics only and are not eligible to become betting filters from this dataset.',
            'Bootstrap resampling is clustered by pitcher-game so correlated ladders are not treated as independent observations.',
            'One-best-per-pitcher-game uses the largest pregame residual edge only and is reported as a concentration stress test, not a tuned strategy.',
            'Edge thresholds 5/7.5/10/12.5/15 are a pre-specified sensitivity analysis; no threshold is promoted solely because it looks best here.',
            'Production/live MLB remains unchanged. Any promotion still requires fresh forward paper validation on unseen dates.',
        ],
    }
