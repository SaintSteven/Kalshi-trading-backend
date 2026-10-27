"""Research v3.2 exact binary outcome gate (read-only; no trade placement).

An exact threshold match is required before a research candidate can advance
to pricing. A neighboring Kalshi ladder rung is never equivalent.
"""
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class Candidate:
    player: str
    stat: str
    direction: str  # OVER or UNDER
    threshold: float  # sportsbook x.5 threshold, or integer threshold
    supporting_independent_signals: int


def binary_outcome(direction: str, threshold: float) -> tuple[str, int]:
    """Return settlement condition ('GE' or 'LE', integer cutoff)."""
    if direction.upper() not in {"OVER", "UNDER"}:
        raise ValueError("direction must be OVER or UNDER")
    if threshold < 0 or (threshold * 2) % 1:
        raise ValueError("threshold must be an integer or half-integer")
    # For discrete nonnegative count stats:
    # OVER 10.5 => >=11; UNDER 10.5 => <=10.
    import math
    return ("GE", math.floor(threshold) + 1) if direction.upper() == "OVER" else ("LE", math.ceil(threshold) - 1)


def kalshi_ladder_outcome(yes_threshold: int, side: str) -> tuple[str, int]:
    """A YES on N+ means >=N; NO on N+ means <=N-1."""
    if side.upper() not in {"YES", "NO"}:
        raise ValueError("side must be YES or NO")
    if yes_threshold < 0:
        raise ValueError("invalid rung")
    return ("GE", yes_threshold) if side.upper() == "YES" else ("LE", yes_threshold - 1)


def validate_ladder_candidate(candidate: Candidate, yes_threshold: int, side: str,
                              actual_player: str, actual_stat: str,
                              rules_verified: bool) -> tuple[bool, str]:
    if candidate.supporting_independent_signals < 2:
        return False, "WATCH: fewer than two independent exact-outcome signals"
    if not rules_verified:
        return False, "WATCH: contract rules not independently verified"
    if candidate.player.strip().casefold() != actual_player.strip().casefold():
        return False, "WATCH: player mismatch"
    if candidate.stat.strip().casefold() != actual_stat.strip().casefold():
        return False, "WATCH: stat mismatch"
    if binary_outcome(candidate.direction, candidate.threshold) != kalshi_ladder_outcome(yes_threshold, side):
        return False, "WATCH: threshold/side settlement mismatch"
    return True, "EXACT_MATCH_RESEARCH_ONLY: pricing and EV checks still required"
