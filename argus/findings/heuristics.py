"""Deterministic severity and effort from finding kind and signal hints."""

from __future__ import annotations

from argus.core.models.enums import EffortBucket, FindingKind, SeverityLevel


def base_severity(kind: FindingKind) -> SeverityLevel:
    """Default severity before signal hints."""
    return {
        FindingKind.COST_RISK: SeverityLevel.HIGH,
        FindingKind.DEPRECATION_CANDIDATE: SeverityLevel.MEDIUM,
        FindingKind.INACTIVITY: SeverityLevel.MEDIUM,
        FindingKind.GROWTH_OPPORTUNITY: SeverityLevel.LOW,
        FindingKind.LAUNCH_CANDIDATE: SeverityLevel.LOW,
        FindingKind.STRUCTURAL_READINESS: SeverityLevel.LOW,
        FindingKind.VALIDATION_READINESS: SeverityLevel.LOW,
        FindingKind.VALIDATION_EVIDENCE_GAP: SeverityLevel.MEDIUM,
        FindingKind.QUALITY_ISSUE: SeverityLevel.MEDIUM,
        FindingKind.RELIABILITY_PROBLEM: SeverityLevel.LOW,
        FindingKind.RETENTION_PROBLEM: SeverityLevel.MEDIUM,
        FindingKind.DOCTRINE_VIOLATION: SeverityLevel.MEDIUM,
        FindingKind.CURRENT_OPPORTUNITY: SeverityLevel.LOW,
        FindingKind.CURRENT_RISK: SeverityLevel.HIGH,
        FindingKind.STALE_CONTEXT: SeverityLevel.MEDIUM,
        FindingKind.TRENDING_TOPIC: SeverityLevel.LOW,
        FindingKind.URGENCY_WINDOW: SeverityLevel.HIGH,
        FindingKind.NO_RECENT_EVIDENCE: SeverityLevel.MEDIUM,
    }.get(kind, SeverityLevel.MEDIUM)


_ORDER = (
    SeverityLevel.INFO,
    SeverityLevel.LOW,
    SeverityLevel.MEDIUM,
    SeverityLevel.HIGH,
    SeverityLevel.CRITICAL,
)


def _max_severity(a: SeverityLevel, b: SeverityLevel) -> SeverityLevel:
    ia = _ORDER.index(a)
    ib = _ORDER.index(b)
    return _ORDER[max(ia, ib)]


def resolve_severity(
    kind: FindingKind,
    hints: list[SeverityLevel | None],
) -> SeverityLevel:
    """Combine base severity with optional signal severity hints (take max)."""
    s = base_severity(kind)
    for h in hints:
        if h is not None:
            s = _max_severity(s, h)
    return s


def effort_for_kind(kind: FindingKind) -> EffortBucket:
    """Rough effort bucket by finding category (deterministic)."""
    return {
        FindingKind.COST_RISK: EffortBucket.SMALL,
        FindingKind.DEPRECATION_CANDIDATE: EffortBucket.MEDIUM,
        FindingKind.INACTIVITY: EffortBucket.SMALL,
        FindingKind.GROWTH_OPPORTUNITY: EffortBucket.SMALL,
        FindingKind.LAUNCH_CANDIDATE: EffortBucket.MEDIUM,
        FindingKind.STRUCTURAL_READINESS: EffortBucket.SMALL,
        FindingKind.VALIDATION_READINESS: EffortBucket.SMALL,
        FindingKind.VALIDATION_EVIDENCE_GAP: EffortBucket.SMALL,
        FindingKind.QUALITY_ISSUE: EffortBucket.TRIVIAL,
        FindingKind.RELIABILITY_PROBLEM: EffortBucket.SMALL,
        FindingKind.RETENTION_PROBLEM: EffortBucket.SMALL,
        FindingKind.DOCTRINE_VIOLATION: EffortBucket.SMALL,
        FindingKind.CURRENT_OPPORTUNITY: EffortBucket.SMALL,
        FindingKind.CURRENT_RISK: EffortBucket.SMALL,
        FindingKind.STALE_CONTEXT: EffortBucket.SMALL,
        FindingKind.TRENDING_TOPIC: EffortBucket.SMALL,
        FindingKind.URGENCY_WINDOW: EffortBucket.TRIVIAL,
        FindingKind.NO_RECENT_EVIDENCE: EffortBucket.SMALL,
    }.get(kind, EffortBucket.SMALL)
