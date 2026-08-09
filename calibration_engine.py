"""Conservative probability calibration for MLB strikeout markets.

v2.5.1 fixes a flaw in the original v2.5 reliability shrinkage. Pulling every
probability toward 50% made rare tail outcomes *more* likely (for example, 3%
raw could become 26.5%), which could manufacture apparent value on long-shot
ladders.

The v2.5.1 rule is intentionally asymmetric and conservative:
- If the selected side's raw fair probability is above 50%, shrink the excess
  probability 50% toward 50%.
- If the selected side's raw fair probability is 50% or below, do not increase
  it at all.

This means calibration can reduce conviction, but it cannot turn a rare raw
outcome into a much more likely event.
"""

CALIBRATION_FACTOR = 0.50
CALIBRATION_METHOD = "conservative-one-sided-shrink-50"


def calibrate_selected_side_probability(raw_probability: float) -> float:
    """Conservatively calibrate the already-selected side's raw probability.

    The result is never greater than the raw probability. Probabilities above
    50% are shrunk halfway toward 50%; probabilities at/below 50% are left
    unchanged.
    """
    p = max(0.0, min(1.0, float(raw_probability)))
    if p <= 0.5:
        return p
    calibrated = 0.5 + CALIBRATION_FACTOR * (p - 0.5)
    return max(0.0, min(p, calibrated))


# Backwards-compatible name for any older imports. In v2.5.1 this function is
# intended to be called on the selected side probability, not blindly on YES.
def calibrate_probability(raw_probability: float) -> float:
    return calibrate_selected_side_probability(raw_probability)
