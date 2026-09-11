r"""Strategic operating modes for Argus (weights, risk posture, experiment bias).

Weight mapping (decision priority score)
----------------------------------------
Each mode reallocates the same six weight slots — they always sum to **1.0**:

- **growth** — *opportunity*: raise ``impact`` and ``urgency`` (chase upside signals);
  lower cost/effort penalties so promising work is not smothered by spend friction.

- **profit** — *ROI*: raise ``confidence`` (trust evidence) and ``cost_penalty`` (enforce
  spend discipline); moderate ``lifecycle_fit`` so bets align with posture without pure exploration.

- **survival** — *cost reduction*: maximize ``cost_penalty`` and ``cost_penalty_input_scale``;
  raise ``urgency`` on risk; shrink ``lifecycle_fit`` so forward bets are de-emphasized.

- **exploration** — *experimentation*: maximize ``lifecycle_fit`` and ``experiment_score_multiplier``;
  keep cost/effort penalties moderate so learning-oriented work ranks well.

Lifecycle kill thresholds remain mode-specific (see ``kill_score_min`` / ``move_forward_max``).

Uncertainty / confidence posture (deterministic)
--------------------------------------------------
Each mode sets **freshness\_\*** confidence factors (stale vs missing operational signals)
and **leap\_of\_faith\_\*** knobs for ``LAUNCH_EXPERIMENT`` ranking — see
:class:`StrategyProfile` fields and ``argus.decision.freshness`` / ``argus.decision.priority``.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class StrategyMode(StrEnum):
    GROWTH = "growth"
    PROFIT = "profit"
    EXPLORATION = "exploration"
    SURVIVAL = "survival"


@dataclass(frozen=True)
class StrategyProfile:
    """
    Tunables used by lifecycle, priority scoring, and candidate ranking.

    Priority weights are applied as in ``argus.decision.priority`` (positive terms minus
    weighted cost/effort penalties). They should sum to 1.0.
    """

    mode: StrategyMode | None
    w_impact: float
    w_confidence: float
    w_urgency: float
    w_lifecycle_fit: float
    w_cost_penalty: float
    w_effort_penalty: float
    #: Multiplier on raw cost penalty input before weighting (higher = spend hurts more).
    cost_penalty_input_scale: float
    #: Extra multiplier on final 0–100 score for ``launch_experiment`` intents.
    experiment_score_multiplier: float
    #: ``kill_candidate`` when ``kill >= kill_score_min`` and ``move_forward <= move_forward_max``.
    kill_score_min: float
    move_forward_max: float
    #: Multiply candidate confidence when required operational signals are stale (0–1); lower = harsher.
    freshness_stale_confidence_factor: float
    #: Same when required signal types are missing from the bundle.
    freshness_missing_confidence_factor: float
    #: For ``LAUNCH_EXPERIMENT`` only: ``conf += lift * (1 - conf)`` before weighting (bounded exploration).
    leap_of_faith_lift: float
    #: For ``LAUNCH_EXPERIMENT`` only: ``conf *= (1 - damp)`` (stricter evidence bar).
    leap_of_faith_damp: float


# Canonical pre-strategy defaults (matches historical argus.priority / lifecycle thresholds).
LEGACY_PROFILE = StrategyProfile(
    mode=None,
    w_impact=0.22,
    w_confidence=0.18,
    w_urgency=0.20,
    w_lifecycle_fit=0.15,
    w_cost_penalty=0.12,
    w_effort_penalty=0.13,
    cost_penalty_input_scale=1.0,
    experiment_score_multiplier=1.0,
    kill_score_min=0.65,
    move_forward_max=0.38,
    freshness_stale_confidence_factor=0.82,
    freshness_missing_confidence_factor=0.72,
    leap_of_faith_lift=0.0,
    leap_of_faith_damp=0.0,
)

PROFILE_BY_MODE: dict[StrategyMode, StrategyProfile] = {
    # Opportunity: weight upside (impact/urgency) and lifecycle fit; light cost friction.
    StrategyMode.GROWTH: StrategyProfile(
        mode=StrategyMode.GROWTH,
        w_impact=0.26,
        w_confidence=0.15,
        w_urgency=0.24,
        w_lifecycle_fit=0.20,
        w_cost_penalty=0.06,
        w_effort_penalty=0.09,
        cost_penalty_input_scale=0.85,
        experiment_score_multiplier=1.15,
        kill_score_min=0.72,
        move_forward_max=0.32,
        freshness_stale_confidence_factor=0.86,
        freshness_missing_confidence_factor=0.76,
        leap_of_faith_lift=0.05,
        leap_of_faith_damp=0.0,
    ),
    # ROI: trust signals + enforce spend (confidence + cost); balanced urgency.
    StrategyMode.PROFIT: StrategyProfile(
        mode=StrategyMode.PROFIT,
        w_impact=0.18,
        w_confidence=0.26,
        w_urgency=0.14,
        w_lifecycle_fit=0.12,
        w_cost_penalty=0.22,
        w_effort_penalty=0.08,
        cost_penalty_input_scale=1.15,
        experiment_score_multiplier=0.90,
        kill_score_min=0.62,
        move_forward_max=0.40,
        freshness_stale_confidence_factor=0.76,
        freshness_missing_confidence_factor=0.66,
        leap_of_faith_lift=0.0,
        leap_of_faith_damp=0.10,
    ),
    # Experimentation: lifecycle alignment + learning; strong boost on experiment intents.
    StrategyMode.EXPLORATION: StrategyProfile(
        mode=StrategyMode.EXPLORATION,
        w_impact=0.17,
        w_confidence=0.16,
        w_urgency=0.16,
        w_lifecycle_fit=0.26,
        w_cost_penalty=0.09,
        w_effort_penalty=0.16,
        cost_penalty_input_scale=0.92,
        experiment_score_multiplier=1.28,
        kill_score_min=0.68,
        move_forward_max=0.36,
        freshness_stale_confidence_factor=0.90,
        freshness_missing_confidence_factor=0.82,
        leap_of_faith_lift=0.10,
        leap_of_faith_damp=0.0,
    ),
    # Cost reduction: penalize spend heavily; favor urgency on risks; narrow forward bets.
    StrategyMode.SURVIVAL: StrategyProfile(
        mode=StrategyMode.SURVIVAL,
        w_impact=0.18,
        w_confidence=0.14,
        w_urgency=0.22,
        w_lifecycle_fit=0.08,
        w_cost_penalty=0.26,
        w_effort_penalty=0.12,
        cost_penalty_input_scale=1.22,
        experiment_score_multiplier=0.85,
        kill_score_min=0.55,
        move_forward_max=0.45,
        freshness_stale_confidence_factor=0.72,
        freshness_missing_confidence_factor=0.62,
        leap_of_faith_lift=0.0,
        leap_of_faith_damp=0.16,
    ),
}

# Stable labels for CLI / decision metadata (maps mode → design intent).
STRATEGY_EMPHASIS: dict[StrategyMode, str] = {
    StrategyMode.GROWTH: "opportunity",
    StrategyMode.PROFIT: "roi",
    StrategyMode.EXPLORATION: "experimentation",
    StrategyMode.SURVIVAL: "cost_reduction",
}
