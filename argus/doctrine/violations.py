"""Emit doctrine findings (violations + policy reminders)."""

from __future__ import annotations

from datetime import datetime, timezone

from argus.core.models.enums import FindingKind, SignalType
from argus.core.models.finding import Finding
from argus.core.models.product import ProductNode
from argus.core.models.signal import SignalRecord
from argus.core.models.validation import validate_finding
from argus.doctrine.models import ProductDoctrine
from argus.findings.heuristics import base_severity, effort_for_kind
from argus.findings.ids import new_finding_id
from argus.lifecycle.model import LifecycleAssessment


def _effective_monthly_usd(product: ProductNode, signals: list[SignalRecord]) -> float | None:
    if product.cost.monthly_usd is not None:
        return float(product.cost.monthly_usd)
    best: float | None = None
    for r in signals:
        if r.signal_type != SignalType.COST:
            continue
        payload = r.payload or {}
        mu = payload.get("monthly_usd")
        if mu is None:
            mu = payload.get("monthly_usd_estimate")
        if mu is None:
            continue
        try:
            v = float(mu)
        except (TypeError, ValueError):
            continue
        best = v if best is None else max(best, v)
    return best


def _cost_violation_finding(
    product: ProductNode,
    monthly: float,
    cap: float,
    signals: list[SignalRecord],
) -> Finding:
    src_ids = [r.id for r in signals if r.signal_type == SignalType.COST][:12]
    f = Finding(
        id=new_finding_id(),
        product_id=product.id,
        kind=FindingKind.DOCTRINE_VIOLATION,
        severity=base_severity(FindingKind.DOCTRINE_VIOLATION),
        effort=effort_for_kind(FindingKind.DOCTRINE_VIOLATION),
        title="Monthly spend exceeds doctrine cost cap",
        summary=(
            f"Observed monthly spend ≈ {monthly:.2f} USD exceeds doctrine limit "
            f"{cap:.2f} USD (product.cost or cost signals)."
        ),
        recommendation="Reduce spend or raise the doctrine cap after explicit review.",
        source_signals=src_ids,
        evidence={
            "doctrine_rule": "max_monthly_cost_usd",
            "monthly_usd": monthly,
            "doctrine_cap_usd": cap,
        },
        confidence=0.85,
        created_at=datetime.now(timezone.utc),
    )
    validate_finding(f)
    return f


def _kill_review_finding(product: ProductNode, assessment: LifecycleAssessment) -> Finding:
    f = Finding(
        id=new_finding_id(),
        product_id=product.id,
        kind=FindingKind.DOCTRINE_VIOLATION,
        severity=base_severity(FindingKind.DOCTRINE_VIOLATION),
        effort=effort_for_kind(FindingKind.DOCTRINE_VIOLATION),
        title="Kill posture requires human review (doctrine)",
        summary=(
            "Lifecycle assessment is in kill_candidate posture, but doctrine requires "
            "explicit human review before irreversible actions."
        ),
        recommendation="Schedule a portfolio or owner review before wind-down or kill steps.",
        source_signals=[],
        evidence={
            "doctrine_rule": "require_human_review_when_kill_candidate",
            "kill_candidate": assessment.kill_candidate,
            "lifecycle_kill_score": round(assessment.kill, 4),
        },
        confidence=0.75,
        created_at=datetime.now(timezone.utc),
    )
    validate_finding(f)
    return f


def doctrine_violation_findings(
    product: ProductNode,
    signals: list[SignalRecord],
    doctrine: ProductDoctrine,
    *,
    assessment_for_kill_rule: LifecycleAssessment | None,
) -> list[Finding]:
    """Return new findings for doctrine violations (deterministic)."""
    out: list[Finding] = []
    cap = doctrine.constraints.max_monthly_cost_usd
    if cap is not None and cap > 0:
        monthly = _effective_monthly_usd(product, signals)
        if monthly is not None and monthly > cap:
            out.append(_cost_violation_finding(product, monthly, cap, signals))

    if doctrine.constraints.require_human_review_when_kill_candidate:
        if assessment_for_kill_rule is not None and assessment_for_kill_rule.kill_candidate:
            out.append(_kill_review_finding(product, assessment_for_kill_rule))

    return out
