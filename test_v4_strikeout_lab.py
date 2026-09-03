from v4_strikeout_lab import (
    V4StrikeoutRequest, _anchored_probability, _execution, analyze_v4_universe,
    negative_binomial_at_least,
)


def test_negative_binomial_ladder_is_monotone():
    probs = [negative_binomial_at_least(5.5, 8.0, k) for k in range(3, 11)]
    assert all(a >= b for a, b in zip(probs, probs[1:]))


def test_market_anchor_endpoints():
    assert abs(_anchored_probability(.40, .70, 0) - .40) < 1e-9
    assert abs(_anchored_probability(.40, .70, 1) - .70) < 1e-9


def test_execution_includes_fee_and_budget():
    result = _execution(40, .55, 1.0, .07)
    assert result["capital_used"] <= 1
    assert result["entry_fee"] > 0
    assert result["effective_price_cents"] > 40


def test_full_ladder_tests_both_sides_and_one_per_pitcher():
    starts = []
    quotes = []
    for day in range(1, 11):
        ds = f"2026-04-{day:02d}"
        starts.append({"game_date": ds, "player": "Test Pitcher", "projected_strikeouts": 6.0, "actual_strikeouts": 6 if day % 2 else 7})
        quotes.extend([
            {"date": ds, "player": "Test Pitcher", "threshold": "5+", "ticker": f"A{day}", "yes_bid_cents": 35, "yes_ask_cents": 36, "no_ask_cents": 66, "quote_age_minutes": 1},
            {"date": ds, "player": "Test Pitcher", "threshold": "7+", "ticker": f"B{day}", "yes_bid_cents": 20, "yes_ask_cents": 21, "no_ask_cents": 81, "quote_age_minutes": 1},
        ])
    request = V4StrikeoutRequest(start_date="2026-04-01", end_date="2026-04-10", max_days=10, minimum_net_edge_points=0)
    result = analyze_v4_universe(starts, quotes, request)
    keys = {(row["date"], row["player"]) for row in result["trades"]}
    assert len(keys) == len(result["trades"])
    assert result["coverage"]["usable_contracts"] == 20
    assert {row["side"] for row in result["trades"]}.issubset({"YES", "NO"})
    assert result["promotion"]["eligible"] is False
