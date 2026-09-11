"""Deterministic confidence adjustments from :class:`StrategyProfile` (shared by decision + experiments)."""

from __future__ import annotations

from argus.strategy.modes import StrategyProfile


def leap_of_faith_adjusted_confidence(
    conf: float,
    profile: StrategyProfile,
) -> tuple[float, dict[str, float]]:
    """
    Apply strategy *damp* then *lift* for launch-experiment-style ranking.

    Used when scoring ``LAUNCH_EXPERIMENT`` decisions and launch-like experiment proposals.
    """
    trace: dict[str, float] = {"raw": conf}
    c = max(0.0, min(1.0, float(conf)))
    damp = profile.leap_of_faith_damp
    if damp > 0:
        c = max(0.05, c * (1.0 - damp))
        trace["after_damp"] = c
    lift = profile.leap_of_faith_lift
    if lift > 0:
        c = min(0.99, c + lift * (1.0 - c))
        trace["after_lift"] = c
    return c, trace
