"""Orchestrate deterministic sub-scores into a :class:`DecisionContextAssessment`."""

from __future__ import annotations

import json
from pathlib import Path

from argus.advisors.temporal import TemporalGrounding, build_temporal_grounding
from argus.core.models.enums import FindingKind, SeverityLevel
from argus.core.models.finding import Finding
from argus.decision.history.analyze import analyze_churn
from argus.decision.history.store import load_product_decision_history
from argus.decision.persistence import load_latest_product_decisions
from argus.decision.stub_awareness import architecture_stub_risk_for_assessment
from argus.decision_assessment.confidence import score_confidence
from argus.decision_assessment.escalation_pressure import (
    score_escalation_pressure as blend_escalation_pressure,
)
from argus.decision_assessment.friction import score_friction
from argus.decision_assessment.leap import evaluate_exploratory_leap
from argus.decision_assessment.models import (
    ConfidenceBucket,
    DecisionContextAssessment,
    EscalationRecommendation,
    FactorContribution,
    now_iso,
)
from argus.decision_assessment.momentum import score_momentum
from argus.decision_assessment.recurrence import score_recurrence
from argus.decision_assessment.risk import score_risk
from argus.decision_assessment.uncertainty import score_uncertainty
from argus.findings.experiment_surfaced import merged_findings_for_decisions
from argus.findings.persistence import load_latest_findings
from argus.lifecycle.scoring import assess_lifecycle
from argus.products.inventory import build_inventory
from argus.signals.persistence import load_latest_bundle
from argus.strategy.apply import get_strategy_profile, load_strategy_mode

_WEIGHTS = {
    "signals": 0.22,
    "findings": 0.22,
    "decisions": 0.18,
    "trends": 0.18,
    "experiments": 0.20,
}


def _bucket(conf: float) -> ConfidenceBucket:
    if conf >= 0.62:
        return ConfidenceBucket.HIGH
    if conf >= 0.38:
        return ConfidenceBucket.MEDIUM
    return ConfidenceBucket.LOW


def evidence_density_from_grounding(tg: TemporalGrounding) -> float:
    """0–1 density from per-source status and overall freshness risk."""
    if not tg.sources:
        return 0.22
    acc = 0.0
    for s in tg.sources:
        w = _WEIGHTS.get(s.key, 0.14)
        if s.status == "current":
            acc += w
        elif s.status == "stale":
            acc += 0.55 * w
    blended = acc * (0.55 + 0.45 * (1.0 - max(0.0, min(1.0, tg.overall_freshness_risk))))
    return max(0.0, min(1.0, blended))


def _conflicting_findings_severity(findings: list[Finding]) -> float:
    kinds_cost = {FindingKind.COST_RISK, FindingKind.CURRENT_RISK}
    kinds_growth = {FindingKind.GROWTH_OPPORTUNITY, FindingKind.LAUNCH_CANDIDATE}
    sev_ok = {SeverityLevel.HIGH, SeverityLevel.CRITICAL}
    has_cost = any(f.kind in kinds_cost and f.severity in sev_ok for f in findings)
    has_growth = any(f.kind in kinds_growth and f.severity in sev_ok for f in findings)
    if has_cost and has_growth:
        return 0.85
    return min(
        1.0,
        sum(1 for f in findings if f.severity in sev_ok) * 0.12,
    )


def _high_severity_count(findings: list[Finding]) -> int:
    return sum(1 for f in findings if f.severity in (SeverityLevel.HIGH, SeverityLevel.CRITICAL))


def _load_advisor_snapshot(repo_root: Path, product_id: str) -> tuple[float | None, int, str]:
    """Consensus confidence, disagreement count, short summary line (advisory text)."""
    p = repo_root / "runs" / "advisors" / f"{product_id}.latest.json"
    if not p.is_file():
        return None, 0, ""
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None, 0, ""
    if not isinstance(raw, dict):
        return None, 0, ""
    cons = raw.get("consensus")
    if not isinstance(cons, dict):
        return None, 0, ""
    cs = cons.get("confidence_score")
    try:
        conf = float(cs) if cs is not None else None
    except (TypeError, ValueError):
        conf = None
    dis = cons.get("disagreement_signals")
    n = len(dis) if isinstance(dis, list) else 0
    summ = str(cons.get("disagreement_summary") or cons.get("final_recommendation") or "")[:800]
    return conf, n, summ


def _thin_signals(repo_root: Path, product_id: str) -> bool:
    b = load_latest_bundle(repo_root, product_id)
    if b is None or not b.records:
        return True
    return len(b.records) < 4


def _missing_sources_list(tg: TemporalGrounding) -> list[str]:
    return list(tg.missing_sources)


def _rationale_lines(
    *,
    confidence: float,
    uncertainty: float,
    risk: float,
    recurrence: float,
    momentum: float,
    friction: float,
    pressure: float,
    esc_rec: EscalationRecommendation,
    exploratory: bool,
    exploratory_reason: str,
) -> str:
    lines = [
        "Argus does not model feelings; this summarizes evidence quality, uncertainty, and action risk.",
        f"confidence={confidence:.2f} uncertainty={uncertainty:.2f} risk={risk:.2f} "
        f"recurrence={recurrence:.2f} momentum={momentum:.2f} friction={friction:.2f}.",
        f"escalation_pressure={pressure:.2f} recommendation={esc_rec.value}.",
    ]
    if exploratory:
        lines.append(f"exploratory_action: {exploratory_reason}")
    else:
        lines.append("exploratory_action: not recommended under current gates.")
    return "\n".join(lines)


def evaluate_decision_context(
    repo_root: Path,
    product_id: str,
    *,
    decision_id: str | None = None,
) -> DecisionContextAssessment:
    """Full assessment for one product (deterministic; uses on-disk artifacts only)."""
    root = repo_root.resolve()
    inv = build_inventory(root)
    if product_id not in inv.valid:
        raise ValueError(f"Unknown or invalid product: {product_id!r}")
    node = inv.valid[product_id].node

    fb = load_latest_findings(root, product_id)
    findings: list[Finding] = merged_findings_for_decisions(root, product_id)

    prof = get_strategy_profile(root)
    la = assess_lifecycle(
        node,
        findings,
        kill_score_min=prof.kill_score_min,
        move_forward_max=prof.move_forward_max,
    )

    tg = build_temporal_grounding(root, product_id)
    evidence_density = evidence_density_from_grounding(tg)
    temporal_risk = float(tg.overall_freshness_risk)

    hist = load_product_decision_history(root, product_id)
    churn_score: float | None = None
    if len(hist) >= 2:
        churn_score = analyze_churn(product_id, hist, repo_root=root).churn_score

    raw_dec = load_latest_product_decisions(root, product_id)
    has_decisions = raw_dec is not None
    has_findings = fb is not None
    sig_b = load_latest_bundle(root, product_id)
    has_signals = sig_b is not None and bool(sig_b.records)

    adv_conf, adv_dis, adv_summary_line = _load_advisor_snapshot(root, product_id)

    from argus.llm.advisor_runner import load_council_metrics

    llm_council_path = root / "runs" / "advisors" / f"llm_council_{product_id}.latest.json"
    has_llm_council = llm_council_path.is_file()
    council_align, council_conflict, council_summary = load_council_metrics(root, product_id)

    if has_llm_council and council_align is not None:
        advisor_alignment_score: float | None = council_align
        advisor_conflict_flag = bool(council_conflict)
        advisor_summary = council_summary or adv_summary_line
    else:
        advisor_alignment_score = adv_conf
        advisor_conflict_flag = adv_dis >= 2
        advisor_summary = adv_summary_line

    stub_risk = architecture_stub_risk_for_assessment(root, product_id, findings)

    conf_score, conf_factors = score_confidence(
        evidence_density=evidence_density,
        temporal_freshness_risk=temporal_risk,
        advisor_consensus_confidence=adv_conf,
        advisor_disagreement_count=adv_dis,
        decision_churn_score=churn_score,
        has_decisions_artifact=has_decisions,
        has_findings_artifact=has_findings,
        has_signals_artifact=has_signals,
        architecture_stub_risk=stub_risk,
    )

    unc_score, unc_factors = score_uncertainty(
        temporal_freshness_risk=temporal_risk,
        missing_artifact_sources=_missing_sources_list(tg),
        advisor_disagreement_count=adv_dis,
        conflicting_findings_severity=_conflicting_findings_severity(findings),
        thin_signal_count=_thin_signals(root, product_id),
    )

    from argus.refinement.signals import refinement_uncertainty_nudge

    ref_nudge = refinement_uncertainty_nudge(root, product_id)
    if ref_nudge > 0:
        unc_score = min(1.0, unc_score + ref_nudge)
        unc_factors.append(
            FactorContribution(
                "refinement_sessions_active",
                "Open artifact refinement work for this product increases interpretive uncertainty.",
                ref_nudge,
                "increases_uncertainty",
            )
        )

    # LLM advisor council file: small nudges only (does not replace temporal/risk/stub logic).
    if has_llm_council and council_align is not None:
        conf_score = min(1.0, conf_score + 0.03 * max(0.0, council_align - 0.5))
        conf_factors.append(
            FactorContribution(
                "llm_council_alignment_nudge",
                "Optional slight confidence lift when persisted LLM council shows alignment.",
                0.03 * max(0.0, council_align - 0.5),
                "increases_confidence",
            )
        )
    if has_llm_council and council_conflict:
        unc_score = min(1.0, unc_score + 0.04)
        unc_factors.append(
            FactorContribution(
                "llm_council_conflict_nudge",
                "Optional uncertainty lift when persisted LLM council flags conflict.",
                0.04,
                "increases_uncertainty",
            )
        )

    monthly = node.cost.monthly_usd
    cap = node.constraints.max_monthly_cost_usd

    risk_score, risk_factors = score_risk(
        kill_candidate=la.kill_candidate,
        monthly_cost_usd=monthly,
        max_monthly_cost_usd=cap,
        lifecycle_stage=la.stage,
        high_severity_finding_count=_high_severity_count(findings),
    )

    rec_score, rec_factors = score_recurrence(root, product_id)
    mom_score, mom_factors = score_momentum(root, product_id)
    fr_score, fr_factors = score_friction(root, product_id)

    esc_p, esc_rec, _esc_factors = blend_escalation_pressure(
        uncertainty_score=unc_score,
        recurrence_risk_score=rec_score,
        friction_score=fr_score,
        confidence_score=conf_score,
        risk_score=risk_score,
    )

    mode = load_strategy_mode(root)
    stage = node.lifecycle.stage

    expl, expl_reason, expl_guard, expl_factors = evaluate_exploratory_leap(
        strategy_mode=mode,
        confidence_score=conf_score,
        uncertainty_score=unc_score,
        risk_score=risk_score,
        evidence_density_score=evidence_density,
        monthly_cost_usd=monthly,
        max_monthly_cost_usd=cap,
        lifecycle_stage=stage,
    )

    rationale = _rationale_lines(
        confidence=conf_score,
        uncertainty=unc_score,
        risk=risk_score,
        recurrence=rec_score,
        momentum=mom_score,
        friction=fr_score,
        pressure=esc_p,
        esc_rec=esc_rec,
        exploratory=expl,
        exploratory_reason=expl_reason,
    )

    return DecisionContextAssessment(
        product_id=product_id,
        decision_id=decision_id,
        assessed_at_utc=now_iso(),
        confidence_score=conf_score,
        confidence_bucket=_bucket(conf_score),
        uncertainty_score=unc_score,
        uncertainty_factors=unc_factors,
        risk_score=risk_score,
        risk_factors=risk_factors,
        recurrence_risk_score=rec_score,
        momentum_score=mom_score,
        friction_score=fr_score,
        escalation_pressure=esc_p,
        escalation_recommendation=esc_rec,
        evidence_density_score=evidence_density,
        exploratory_action_recommended=expl,
        exploratory_action_reason=expl_reason,
        exploratory_guardrails=expl_guard,
        rationale=rationale,
        confidence_factors=conf_factors + expl_factors,
        momentum_factors=mom_factors,
        friction_factors=fr_factors,
        recurrence_factors=rec_factors,
        advisor_alignment_score=advisor_alignment_score,
        advisor_conflict_flag=advisor_conflict_flag,
        advisor_summary=advisor_summary,
    )
