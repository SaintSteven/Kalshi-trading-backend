"""v2.7 experimental reliability correction learned from the frozen July discovery set.

IMPORTANT: this is a validation candidate, not the live production calibration.
The July v2.6.6 diagnostic sample showed average calibrated predicted probability
44.2% versus 28.9% actual. A weighted least-squares fit across the preregistered
20-30 / 30-40 / 40-50 / 50-60 calibration buckets produced:

    corrected_p = 0.01087202 + 0.62876989 * calibrated_p

The correction is intentionally global rather than ladder/side specific to avoid
hard-coding the July 5+ or YES failures. It is also conservative: it can never
increase the v2.6.x calibrated selected-side probability.

June 2026 is the holdout validation period. Do not tune these coefficients from
June results.
"""

V27_INTERCEPT = 0.01087202
V27_SLOPE = 0.62876989
V27_METHOD = "v27-july-discovery-global-affine-a0.010872-b0.628770"


def apply_v27_reliability_correction(calibrated_probability: float) -> float:
    p = max(0.0, min(1.0, float(calibrated_probability)))
    corrected = V27_INTERCEPT + V27_SLOPE * p
    # Reliability correction may only reduce conviction; never manufacture it.
    return max(0.0, min(p, corrected, 1.0))
