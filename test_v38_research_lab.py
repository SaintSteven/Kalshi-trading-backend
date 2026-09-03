import unittest
from v38_research_lab import V38LineMovementRequest, _metrics, _trade_result, analyze_line_movement, kalshi_order_fee_cents

class V38ResearchLabTests(unittest.TestCase):
    def setUp(self):
        self.request = V38LineMovementRequest(start_date="2026-08-01", end_date="2026-08-02")

    def test_fee_uses_contract_count_and_rounds_up(self):
        self.assertEqual(kalshi_order_fee_cents(2, 40), 4)
        self.assertEqual(kalshi_order_fee_cents(1, 50), 2)

    def test_trade_uses_ask_entry_bid_exit_and_both_fees(self):
        result = _trade_result(40, 45, 1.0, 0.07)
        self.assertEqual(result["contracts"], 2)
        self.assertEqual(result["capital_used"], 0.84)
        self.assertEqual(result["total_fees"], 0.08)
        self.assertEqual(result["profit_loss"], 0.02)

    def test_entry_fee_never_pushes_risk_above_unit(self):
        result = _trade_result(49, 54, 1.0, 0.07)
        self.assertEqual(result["contracts"], 1)
        self.assertLessEqual(result["capital_used"], 1.0)

    def test_one_largest_decline_per_game(self):
        event = "KXMLBGAME-26AUG011305BOSNYY"
        grouped = {event: [{"ticker": "A", "title": "Boston"}, {"ticker": "B", "title": "New York"}]}
        snapshots = {"A": {240: {"yes_ask": 55, "quote_age_minutes": 2}, 90: {"yes_ask": 49, "quote_age_minutes": 1}, 10: {"yes_bid": 53, "quote_age_minutes": 1}},
            "B": {240: {"yes_ask": 47, "quote_age_minutes": 2}, 90: {"yes_ask": 50, "quote_age_minutes": 1}, 10: {"yes_bid": 46, "quote_age_minutes": 1}}}
        trades, diagnostics = analyze_line_movement(grouped, snapshots, self.request)
        self.assertEqual(len(trades), 1); self.assertEqual(trades[0]["ticker"], "A")
        self.assertEqual(trades[0]["trigger_decline_cents"], 6); self.assertEqual(diagnostics["qualified_trades"], 1)

    def test_stale_quote_is_rejected(self):
        event = "KXMLBGAME-26AUG011305BOSNYY"
        grouped = {event: [{"ticker": "A", "title": "Boston"}]}
        snapshots = {"A": {240: {"yes_ask": 55, "quote_age_minutes": 21}, 90: {"yes_ask": 49, "quote_age_minutes": 1}, 10: {"yes_bid": 53, "quote_age_minutes": 1}}}
        trades, diagnostics = analyze_line_movement(grouped, snapshots, self.request)
        self.assertEqual(trades, []); self.assertEqual(diagnostics["stale_quote"], 1)

    def test_metrics_use_actual_capital_not_requested_unit(self):
        rows = [{"date": "2026-08-01", "game_start_time": "a", "ticker": "A", "capital_used": .84, "profit_loss": .02, "profitable": True, "net_move_cents": 4, "total_fees": .08},
            {"date": "2026-08-02", "game_start_time": "b", "ticker": "B", "capital_used": .93, "profit_loss": -.09, "profitable": False, "net_move_cents": 0, "total_fees": .09}]
        result = _metrics(rows)
        self.assertEqual(result["trades"], 2); self.assertEqual(result["capital_used"], 1.77); self.assertEqual(result["profit_loss"], -.07)

if __name__ == "__main__": unittest.main()
