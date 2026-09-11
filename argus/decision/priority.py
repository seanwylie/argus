"""Transparent priority scoring for DecisionCandidate (0–100)."""

from __future__ import annotations

from argus.core.models.decision import DecisionCandidate
from argus.core.models.enums import EffortBucket, FindingKind, SeverityLevel
from argus.core.models.finding import Finding
from argus.decision.intents import DecisionIntent
from argus.lifecycle.model import LifecycleAssessment
from argus.strategy.modes import LEGACY_PROFILE, STRATEGY_EMPHASIS, StrategyProfile
from argus.strategy.uncertainty import leap_of_faith_adjusted_confidence


def _severity_to_impact(sev: SeverityLevel) -> float:
    return {
        SeverityLevel.CRITICAL: 1.0,
        SeverityLevel.HIGH: 0.82,
        SeverityLevel.MEDIUM: 0.58,
        SeverityLevel.LOW: 0.38,
        SeverityLevel.INFO: 0.22,
    }.get(sev, 0.45)


def _severity_to_urgency(sev: SeverityLevel, kind: FindingKind) -> float:
    u = _severity_to_impact(sev)
    if kind in (
        FindingKind.COST_RISK,
        FindingKind.DEPRECATION_CANDIDATE,
        FindingKind.DOCTRINE_VIOLATION,
        FindingKind.CURRENT_RISK,
        FindingKind.URGENCY_WINDOW,
        FindingKind.NO_RECENT_EVIDENCE,
    ):
        u = min(1.0, u + 0.12)
    return u


def _effort_penalty(eff: EffortBucket) -> float:
    return {
        EffortBucket.TRIVIAL: 0.08,
        EffortBucket.SMALL: 0.22,
        EffortBucket.MEDIUM: 0.42,
        EffortBucket.LARGE: 0.62,
        EffortBucket.XLARGE: 0.82,
    }.get(eff, 0.35)


def _cost_penalty_from_product(monthly: float | None, cap: float | None) -> float:
    if monthly is None or cap is None or cap <= 0:
        return 0.15
    ratio = min(1.5, monthly / cap)
    return min(1.0, max(0.0, ratio * 0.55))


def _scale_cost_penalty(base: float, profile: StrategyProfile) -> float:
    return min(1.0, max(0.0, base * profile.cost_penalty_input_scale))


def _confidence_for_strategy(
    conf: float,
    intent: DecisionIntent,
    profile: StrategyProfile,
) -> tuple[float, dict[str, float]]:
    """Delegate to :func:`leap_of_faith_adjusted_confidence` for ``LAUNCH_EXPERIMENT`` only."""
    if intent != DecisionIntent.LAUNCH_EXPERIMENT:
        return conf, {"raw": conf}
    return leap_of_faith_adjusted_confidence(conf, profile)


def lifecycle_fit(intent: DecisionIntent, a: LifecycleAssessment) -> float:
    """How well this intent matches current lifecycle posture."""
    m = {
        DecisionIntent.IMPROVE_PRODUCT: a.improve,
        DecisionIntent.GATHER_MORE_DATA: max(a.hold, a.improve) * 0.9,
        DecisionIntent.LAUNCH_EXPERIMENT: a.move_forward,
        DecisionIntent.REDUCE_COST: max(a.improve, a.hold),
        DecisionIntent.HOLD_STEADY: a.hold,
        DecisionIntent.DEPRECATE_PRODUCT: a.deprecate,
        DecisionIntent.KILL_PRODUCT: a.kill,
        DecisionIntent.ESCALATE_TO_HUMAN: max(a.kill, a.deprecate, 1.0 - a.move_forward),
    }
    return float(m.get(intent, 0.5))


def compute_priority_score(
    candidate: DecisionCandidate,
    *,
    finding: Finding | None,
    assessment: LifecycleAssessment,
    monthly_spend: float | None,
    spend_cap: float | None,
    strategy_profile: StrategyProfile | None = None,
) -> float:
    """Return a 0–100 score; higher = more important to consider next."""
    p = strategy_profile or LEGACY_PROFILE
    intent_str = (candidate.metadata or {}).get("intent")
    try:
        intent = DecisionIntent(str(intent_str)) if intent_str else DecisionIntent.HOLD_STEADY
    except ValueError:
        intent = DecisionIntent.HOLD_STEADY

    if finding is not None:
        impact = _severity_to_impact(finding.severity)
        urgency = _severity_to_urgency(finding.severity, finding.kind)
        conf = finding.confidence if finding.confidence is not None else 0.55
        eff_p = _effort_penalty(finding.effort)
    else:
        impact, urgency = 0.45, 0.4
        conf = candidate.confidence if candidate.confidence is not None else 0.5
        eff_p = 0.25

    conf = max(0.0, min(1.0, conf))
    conf, unc_trace = _confidence_for_strategy(conf, intent, p)
    cost_p = _scale_cost_penalty(_cost_penalty_from_product(monthly_spend, spend_cap), p)
    lc = lifecycle_fit(intent, assessment)

    raw = (
        p.w_impact * impact
        + p.w_confidence * conf
        + p.w_urgency * urgency
        + p.w_lifecycle_fit * lc
        - p.w_cost_penalty * cost_p
        - p.w_effort_penalty * eff_p
    )
    score = 100.0 * max(0.0, min(1.0, raw))
    if intent == DecisionIntent.LAUNCH_EXPERIMENT:
        score = min(100.0, score * p.experiment_score_multiplier)
    return round(score, 2)


def attach_priority(
    candidate: DecisionCandidate,
    *,
    finding: Finding | None,
    assessment: LifecycleAssessment,
    monthly_spend: float | None,
    spend_cap: float | None,
    strategy_profile: StrategyProfile | None = None,
) -> DecisionCandidate:
    """Mutate metadata with score breakdown and set priority_score."""
    p = strategy_profile or LEGACY_PROFILE
    score = compute_priority_score(
        candidate,
        finding=finding,
        assessment=assessment,
        monthly_spend=monthly_spend,
        spend_cap=spend_cap,
        strategy_profile=p,
    )
    candidate.priority_score = score
    candidate.metadata = dict(candidate.metadata or {})
    if p.mode is None:
        candidate.metadata["score_model"] = "argus.priority.v1"
    else:
        candidate.metadata["score_model"] = "argus.priority.v2"
        candidate.metadata["strategy_mode"] = p.mode.value
        candidate.metadata["strategy_emphasis"] = STRATEGY_EMPHASIS[p.mode]
    candidate.metadata["weights"] = {
        "impact": p.w_impact,
        "confidence": p.w_confidence,
        "urgency": p.w_urgency,
        "lifecycle_fit": p.w_lifecycle_fit,
        "cost_penalty": p.w_cost_penalty,
        "effort_penalty": p.w_effort_penalty,
    }
    candidate.metadata["cost_penalty_input_scale"] = p.cost_penalty_input_scale
    candidate.metadata["experiment_score_multiplier"] = p.experiment_score_multiplier
    intent_str = (candidate.metadata or {}).get("intent")
    try:
        intent_att = DecisionIntent(str(intent_str)) if intent_str else DecisionIntent.HOLD_STEADY
    except ValueError:
        intent_att = DecisionIntent.HOLD_STEADY
    if intent_att == DecisionIntent.LAUNCH_EXPERIMENT and (
        p.leap_of_faith_lift > 0 or p.leap_of_faith_damp > 0
    ):
        raw_conf = (
            finding.confidence
            if finding is not None and finding.confidence is not None
            else (candidate.confidence if candidate.confidence is not None else 0.55)
        )
        raw_conf = max(0.0, min(1.0, float(raw_conf)))
        _, unc_trace = leap_of_faith_adjusted_confidence(raw_conf, p)
        if len(unc_trace) > 1:
            candidate.metadata["strategy_uncertainty_adjustment"] = unc_trace
    return candidate
