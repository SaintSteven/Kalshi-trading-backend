import unittest
from exact_threshold_gate import Candidate, validate_ladder_candidate


class ExactThresholdGateTests(unittest.TestCase):
    def setUp(self):
        self.c = Candidate("Donovan Clingan", "rebounds", "UNDER", 10.5, 2)

    def check(self, rung, side, signals=2, rules=True):
        c = Candidate(self.c.player, self.c.stat, self.c.direction, self.c.threshold, signals)
        return validate_ladder_candidate(c, rung, side, self.c.player, self.c.stat, rules)

    def test_no_on_12_plus_is_not_under_10_5(self):
        self.assertFalse(self.check(12, "NO")[0])

    def test_no_on_10_plus_is_not_under_10_5(self):
        self.assertFalse(self.check(10, "NO")[0])

    def test_no_on_11_plus_is_under_10_5(self):
        self.assertTrue(self.check(11, "NO")[0])

    def test_requires_two_independent_signals(self):
        self.assertFalse(self.check(11, "NO", signals=1)[0])

    def test_requires_verified_rules(self):
        self.assertFalse(self.check(11, "NO", rules=False)[0])

    def test_over_10_5_is_yes_on_11_plus(self):
        c = Candidate("Donovan Clingan", "rebounds", "OVER", 10.5, 2)
        self.assertTrue(validate_ladder_candidate(c, 11, "YES", c.player, c.stat, True)[0])


if __name__ == "__main__":
    unittest.main()
