"""Generate decision candidates from findings + lifecycle assessment."""

from __future__ import annotations

from pathlib import Path

from argus.core.models.decision import DecisionCandidate
from argus.core.models.enums import ActionType, FindingKind
from argus.core.models.finding import Finding
from argus.core.models.product import ProductNode
from argus.core.models.validation import validate_decision_candidate
from argus.decision.freshness import apply_freshness_to_candidates
from argus.decision.ids import new_decision_id
from argus.decision.intents import DecisionIntent
from argus.decision.priority import attach_priority
from argus.decision.stub_awareness import (
    StubGapContext,
    apply_stub_awareness_to_candidates,
    infer_stub_gap_context,
)
from argus.lifecycle.model import LifecycleAssessment
from argus.strategy.modes import StrategyProfile


def _base(
    product_id: str,
    action_type: ActionType,
    summary: str,
    intent: DecisionIntent,
    rationale: str,
    *,
    finding: Finding | None = None,
    estimated_cost: float | None = None,
    confidence: float | None = None,
) -> DecisionCandidate:
    md: dict = {"intent": intent.value}
    if finding is not None:
        md["finding_id"] = finding.id
        md["finding_kind"] = finding.kind.value
    c = DecisionCandidate(
        id=new_decision_id(),
        product_id=product_id,
        action_type=action_type,
        summary=summary,
        expected_impact="",
        estimated_cost=estimated_cost,
        confidence=confidence,
        rationale=rationale,
        priority_score=None,
        metadata=md,
    )
    validate_decision_candidate(c)
    return c


def finding_to_candidates(
    product: ProductNode,
    finding: Finding,
) -> list[DecisionCandidate]:
    """Map one finding to one or more candidates (usually one)."""
    pid = product.id
    k = finding.kind
    out: list[DecisionCandidate] = []

    if k == FindingKind.COST_RISK:
        out.append(
            _base(
                pid,
                ActionType.INVESTIGATE,
                "Reduce cost exposure relative to cap",
                DecisionIntent.REDUCE_COST,
                "Cost snapshot exceeds declared monthly cap; investigate drivers.",
                finding=finding,
                confidence=finding.confidence,
            )
        )
    elif k == FindingKind.GROWTH_OPPORTUNITY:
        out.append(
            _base(
                pid,
                ActionType.CUSTOM,
                "Run a focused experiment on the growth spike",
                DecisionIntent.LAUNCH_EXPERIMENT,
                "Engagement up materially vs prior window; validate causality with an experiment.",
                finding=finding,
                confidence=finding.confidence,
            )
        )
    elif k == FindingKind.DEPRECATION_CANDIDATE:
        out.append(
            _base(
                pid,
                ActionType.DEPRECATE,
                "Plan deprecation path (read-only, timeline, comms)",
                DecisionIntent.DEPRECATE_PRODUCT,
                "Inactivity and low traction align with sunsetting posture.",
                finding=finding,
                confidence=finding.confidence,
            )
        )
    elif k == FindingKind.INACTIVITY:
        out.append(
            _base(
                pid,
                ActionType.INVESTIGATE,
                "Restore activity: pipelines, schedules, or outputs",
                DecisionIntent.IMPROVE_PRODUCT,
                "Product shows insufficient recent activity.",
                finding=finding,
                confidence=finding.confidence,
            )
        )
    elif k == FindingKind.QUALITY_ISSUE:
        out.append(
            _base(
                pid,
                ActionType.INVESTIGATE,
                "Fix missing paths or empty expected outputs",
                DecisionIntent.IMPROVE_PRODUCT,
                "Filesystem/layout does not match declared expectations.",
                finding=finding,
                confidence=finding.confidence,
            )
        )
    elif k == FindingKind.LAUNCH_CANDIDATE:
        out.append(
            _base(
                pid,
                ActionType.CUSTOM,
                "Advance validation gate or widen controlled exposure",
                DecisionIntent.LAUNCH_EXPERIMENT,
                "Readiness markers suggest next gate or broader experiment.",
                finding=finding,
                confidence=finding.confidence,
            )
        )
    elif k == FindingKind.STRUCTURAL_READINESS:
        out.append(
            _base(
                pid,
                ActionType.ANALYZE,
                "Deepen validation evidence (usage, SLOs, non-bootstrap KPIs)",
                DecisionIntent.GATHER_MORE_DATA,
                "Layout and local metrics exist, but operational validation evidence is still thin.",
                finding=finding,
                confidence=finding.confidence,
            )
        )
    elif k == FindingKind.VALIDATION_READINESS:
        out.append(
            _base(
                pid,
                ActionType.ANALYZE,
                "Align lifecycle.stage and validation gates with evidence-backed posture",
                DecisionIntent.IMPROVE_PRODUCT,
                "Validation evidence contract is satisfied; adopt validate-stage practices or update manifest when appropriate.",
                finding=finding,
                confidence=finding.confidence,
            )
        )
    elif k == FindingKind.VALIDATION_EVIDENCE_GAP:
        out.append(
            _base(
                pid,
                ActionType.INVESTIGATE,
                "Back declare validate with operational + exercise evidence or manual validation artifact",
                DecisionIntent.GATHER_MORE_DATA,
                "Stage declares validate but local signals do not meet the validation evidence contract.",
                finding=finding,
                confidence=finding.confidence,
            )
        )
    elif k == FindingKind.RELIABILITY_PROBLEM:
        out.append(
            _base(
                pid,
                ActionType.ANALYZE,
                "Increase observability and local snapshot coverage",
                DecisionIntent.GATHER_MORE_DATA,
                "Insufficient signal volume to judge viability confidently.",
                finding=finding,
                confidence=finding.confidence,
            )
        )
    elif k == FindingKind.RETENTION_PROBLEM:
        out.append(
            _base(
                pid,
                ActionType.INVESTIGATE,
                "Investigate retention drivers and UX friction",
                DecisionIntent.IMPROVE_PRODUCT,
                "Retention risk surfaced from signals/findings.",
                finding=finding,
                confidence=finding.confidence,
            )
        )
    elif k == FindingKind.DOCTRINE_VIOLATION:
        out.append(
            _base(
                pid,
                ActionType.ANALYZE,
                "Reconcile automated posture with declared product doctrine",
                DecisionIntent.ESCALATE_TO_HUMAN,
                "Declared doctrine constraints or review rules are not satisfied; needs explicit human alignment.",
                finding=finding,
                confidence=finding.confidence,
            )
        )
    elif k == FindingKind.CURRENT_OPPORTUNITY:
        out.append(
            _base(
                pid,
                ActionType.CUSTOM,
                "Exploit the current temporal opportunity window",
                DecisionIntent.LAUNCH_EXPERIMENT,
                "Spike or acceleration vs prior window; run a bounded bet while conditions hold.",
                finding=finding,
                confidence=finding.confidence,
            )
        )
    elif k == FindingKind.TRENDING_TOPIC:
        out.append(
            _base(
                pid,
                ActionType.CUSTOM,
                "Align roadmap or GTM with elevated topic momentum",
                DecisionIntent.LAUNCH_EXPERIMENT,
                "Narrative momentum detected; validate fit with a small, measurable initiative.",
                finding=finding,
                confidence=finding.confidence,
            )
        )
    elif k == FindingKind.URGENCY_WINDOW:
        out.append(
            _base(
                pid,
                ActionType.ANALYZE,
                "Decide before the monitored urgency window closes",
                DecisionIntent.ESCALATE_TO_HUMAN,
                "Time-bounded window requires explicit owner decision (in/out) before expiry.",
                finding=finding,
                confidence=finding.confidence,
            )
        )
    elif k == FindingKind.CURRENT_RISK:
        out.append(
            _base(
                pid,
                ActionType.INVESTIGATE,
                "Mitigate acute temporal risk vs recent baseline",
                DecisionIntent.IMPROVE_PRODUCT,
                "Negative velocity or acute risk flag in the current window; tighten controls.",
                finding=finding,
                confidence=finding.confidence,
            )
        )
    elif k == FindingKind.STALE_CONTEXT:
        out.append(
            _base(
                pid,
                ActionType.ANALYZE,
                "Refresh external context before major bets",
                DecisionIntent.GATHER_MORE_DATA,
                "Market or operating context may be outdated; refresh intel and assumptions.",
                finding=finding,
                confidence=finding.confidence,
            )
        )
    elif k == FindingKind.NO_RECENT_EVIDENCE:
        out.append(
            _base(
                pid,
                ActionType.ANALYZE,
                "Restore fresh observability before trusting posture",
                DecisionIntent.GATHER_MORE_DATA,
                "Signals are missing or too old for reliable temporal comparison.",
                finding=finding,
                confidence=finding.confidence,
            )
        )

    return out


def augment_with_posture_candidates(
    product: ProductNode,
    findings: list[Finding],
    assessment: LifecycleAssessment,
) -> list[DecisionCandidate]:
    """Add hold / escalate / kill suggestions not tied 1:1 to a single finding."""
    pid = product.id
    extra: list[DecisionCandidate] = []

    if assessment.kill_candidate:
        extra.append(
            _base(
                pid,
                ActionType.CUSTOM,
                "Escalate portfolio review (kill posture signals)",
                DecisionIntent.ESCALATE_TO_HUMAN,
                "Kill score high with low forward momentum; needs human judgment.",
                finding=None,
                confidence=0.7,
            )
        )
        extra.append(
            _base(
                pid,
                ActionType.ARCHIVE,
                "Prepare terminal wind-down plan",
                DecisionIntent.KILL_PRODUCT,
                "System flag: kill_candidate — align with owner before irreversible steps.",
                finding=None,
                confidence=0.55,
            )
        )

    if assessment.hold >= 0.42 and len(findings) <= 2:
        extra.append(
            _base(
                pid,
                ActionType.PAUSE,
                "Hold steady: wait for more signal before larger bets",
                DecisionIntent.HOLD_STEADY,
                "Lifecycle posture favors holding with limited contradictory evidence.",
                finding=None,
                confidence=0.5,
            )
        )

    return extra


def build_candidates(
    product: ProductNode,
    findings: list[Finding],
    assessment: LifecycleAssessment,
    *,
    strategy_profile: StrategyProfile | None = None,
    repo_root: Path | None = None,
    stub_gap_context: StubGapContext | None = None,
) -> list[DecisionCandidate]:
    """All candidates with priority scores attached."""
    monthly = product.cost.monthly_usd
    cap = product.constraints.max_monthly_cost_usd

    all_c: list[tuple[DecisionCandidate, Finding | None]] = []
    for f in findings:
        for c in finding_to_candidates(product, f):
            all_c.append((c, f))
    for c in augment_with_posture_candidates(product, findings, assessment):
        all_c.append((c, None))

    scored: list[DecisionCandidate] = []
    for c, f in all_c:
        attach_priority(
            c,
            finding=f,
            assessment=assessment,
            monthly_spend=monthly,
            spend_cap=cap,
            strategy_profile=strategy_profile,
        )
        validate_decision_candidate(c)
        scored.append(c)

    scored.sort(key=lambda x: (x.priority_score or 0), reverse=True)
    scored = apply_freshness_to_candidates(
        repo_root,
        product.id,
        findings,
        scored,
        assessment,
        monthly_spend=monthly,
        spend_cap=cap,
        strategy_profile=strategy_profile,
    )
    ctx = stub_gap_context if stub_gap_context is not None else infer_stub_gap_context(repo_root)
    scored = apply_stub_awareness_to_candidates(
        repo_root,
        product.id,
        findings,
        scored,
        assessment,
        monthly_spend=monthly,
        spend_cap=cap,
        strategy_profile=strategy_profile,
        stub_context=ctx,
    )
    return scored
