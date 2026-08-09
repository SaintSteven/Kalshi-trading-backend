"""Conservative probability calibration for MLB strikeout markets.

v2.5 intentionally uses a simple reliability shrinkage rather than fitting a
high-parameter calibration model to a small sample.  Probabilities are pulled
halfway from the raw model estimate toward 50%, preserving ordering and YES/NO
complements while reducing the overconfidence seen in the prospective ledger.
"""

CALIBRATION_FACTOR = 0.50
CALIBRATION_METHOD = "reliability-shrink-50"


def calibrate_probability(raw_probability: float) -> float:
    """Shrink a probability toward 0.50 by the configured reliability factor."""
    p = max(0.0, min(1.0, float(raw_probability)))
    calibrated = 0.5 + CALIBRATION_FACTOR * (p - 0.5)
    return max(0.0, min(1.0, calibrated))
