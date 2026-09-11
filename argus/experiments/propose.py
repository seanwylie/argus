"""
Deterministic experiment proposals from findings, trends, decisions, and lifecycle.

Suggestions only — no execution or external APIs.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from argus.core.models.enums import FindingKind, LifecycleStage
from argus.core.serialize import dumps_json, loads_json, to_jsonable
from argus.decision.persistence import load_latest_product_decisions
from argus.experiments.models import ExperimentProposal, ExperimentType, ProposalRun
from argus.findings.persistence import load_latest_findings
from argus.findings.rules.temporal import TEMPORAL_FINDING_KINDS
from argus.products.inventory import ProductInventory, build_inventory
from argus.trends.analyze import analyze_product
from argus.trends.models import TrendFlag


def _stable_id(product_id: str, key: str) -> str:
    h = hashlib.sha256(f"{product_id}:{key}:argus.propose.v1".encode()).hexdigest()[:12]
    return f"pr_{product_id}_{h}"


def _clip_conf(x: float) -> float:
    return max(0.15, min(0.92, x))


def _finding_kinds(findings: list[Any]) -> set[str]:
    return {f.kind.value for f in findings}


def _decision_intents(raw: dict[str, Any] | None) -> list[str]:
    if not raw:
        return []
    out: list[str] = []
    for c in (raw.get("candidates") or [])[:8]:
        if not isinstance(c, dict):
            continue
        md = c.get("metadata") or {}
        if isinstance(md, dict) and md.get("intent"):
            out.append(str(md["intent"]))
    return out


def _collect_proposals_for_product(
    repo_root: Path,
    product_id: str,
    *,
    lifecycle_stage: LifecycleStage,
    kill_candidate: bool,
) -> list[ExperimentProposal]:
    props: list[ExperimentProposal] = []
    fb = load_latest_findings(repo_root, product_id)
    findings = list(fb.findings) if fb else []
    kinds = _finding_kinds(findings)

    tr = analyze_product(repo_root, product_id)
    flags = set(tr.trend_flags)

    dec_raw = load_latest_product_decisions(repo_root, product_id)
    intents = _decision_intents(dec_raw)
    stage = lifecycle_stage

    # --- Finding-driven (deterministic templates) ---
    if FindingKind.COST_RISK.value in kinds:
        key = "cost_risk"
        props.append(
            ExperimentProposal(
                proposal_id=_stable_id(product_id, key),
                product_id=product_id,
                hypothesis="Reduce recurring infrastructure cost without losing critical coverage.",
                type=ExperimentType.COST_REDUCTION,
                description=(
                    "Cost-risk finding present: review highest-cost services, rightsizing, "
                    "and replaceable components (e.g. managed caches, oversized instances)."
                ),
                expected_outcome="Monthly spend decreases with stable or improved reliability signals.",
                success_metrics=["monthly_cost_usd", "cost_vs_cap_ratio", "incident_count_proxy"],
                estimated_effort="medium",
                confidence=_clip_conf(0.67),
                rationale="finding:cost_risk",
            )
        )

    temporal_vals = {k.value for k in TEMPORAL_FINDING_KINDS}
    if kinds & temporal_vals:
        key = "temporal_signal_followup"
        props.append(
            ExperimentProposal(
                proposal_id=_stable_id(product_id, key),
                product_id=product_id,
                hypothesis="Align product bets with time-bounded signals: validate relevance, then ship a small timed experiment.",
                type=ExperimentType.GROWTH,
                description=(
                    "Temporal finding(s) present: treat the window explicitly—confirm ICP fit, "
                    "instrument the metric tied to the signal, and run a bounded capture or mitigation."
                ),
                expected_outcome="Decision uses current evidence; primary metric moves or risk is contained within the window.",
                success_metrics=["signal_aligned_metric", "time_to_decision_hours", "finding_resolution_rate"],
                estimated_effort="small",
                confidence=_clip_conf(0.56),
                rationale="finding:temporal_kinds",
            )
        )

    if FindingKind.GROWTH_OPPORTUNITY.value in kinds:
        key = "growth_opp"
        props.append(
            ExperimentProposal(
                proposal_id=_stable_id(product_id, key),
                product_id=product_id,
                hypothesis="Amplify the observed growth signal with a bounded traffic or UX experiment.",
                type=ExperimentType.GROWTH,
                description=(
                    "Growth opportunity finding: run a small, measurable change (funnel step, "
                    "activation prompt, or distribution tweak) with a fixed evaluation window."
                ),
                expected_outcome="Primary north-star metric moves beyond prior baseline variance.",
                success_metrics=["conversion_or_activation_rate", "weekly_active_proxy"],
                estimated_effort="small",
                confidence=_clip_conf(0.58),
                rationale="finding:growth_opportunity",
            )
        )

    if FindingKind.INACTIVITY.value in kinds:
        key = "inactivity_pause"
        props.append(
            ExperimentProposal(
                proposal_id=_stable_id(product_id, key),
                product_id=product_id,
                hypothesis="Pause scheduled spend-heavy jobs for 7 days to observe organic baseline activity.",
                type=ExperimentType.ENGAGEMENT,
                description=(
                    "Inactivity finding: temporarily reduce automated pushes/crawls to measure "
                    "natural usage and noise floor."
                ),
                expected_outcome="Clearer read on organic engagement vs pipeline-driven activity.",
                success_metrics=["events_per_day_organic", "job_runs_per_day"],
                estimated_effort="small",
                confidence=_clip_conf(0.52),
                rationale="finding:inactivity",
            )
        )

    if FindingKind.RETENTION_PROBLEM.value in kinds:
        key = "retention"
        props.append(
            ExperimentProposal(
                proposal_id=_stable_id(product_id, key),
                product_id=product_id,
                hypothesis="Improve retention with one focused friction fix or onboarding step.",
                type=ExperimentType.ENGAGEMENT,
                description=(
                    "Retention risk surfaced: pick a single funnel segment, instrument exits, "
                    "and ship a minimal remediation."
                ),
                expected_outcome="D1/D7 retention stable or up vs prior window.",
                success_metrics=["retention_d1", "retention_d7", "churn_proxy"],
                estimated_effort="medium",
                confidence=_clip_conf(0.55),
                rationale="finding:retention_problem",
            )
        )

    if FindingKind.RELIABILITY_PROBLEM.value in kinds:
        key = "reliability"
        props.append(
            ExperimentProposal(
                proposal_id=_stable_id(product_id, key),
                product_id=product_id,
                hypothesis="Increase observability and reduce flaky paths before scaling load.",
                type=ExperimentType.INFRASTRUCTURE,
                description=(
                    "Reliability finding: add health checks, narrow timeouts, and snapshot coverage "
                    "for the noisiest dependency."
                ),
                expected_outcome="Fewer reliability findings and fewer silent failures in snapshots.",
                success_metrics=["error_rate_proxy", "snapshot_success_rate"],
                estimated_effort="medium",
                confidence=_clip_conf(0.54),
                rationale="finding:reliability_problem",
            )
        )

    if FindingKind.DEPRECATION_CANDIDATE.value in kinds:
        key = "deprecate_observe"
        props.append(
            ExperimentProposal(
                proposal_id=_stable_id(product_id, key),
                product_id=product_id,
                hypothesis="Measure organic usage for 14 days before committing to a sunset timeline.",
                type=ExperimentType.ENGAGEMENT,
                description=(
                    "Deprecation posture: freeze feature work; collect minimal usage evidence to "
                    "justify timeline and comms."
                ),
                expected_outcome="Documented usage trend supports continue vs wind-down decision.",
                success_metrics=["active_users_proxy", "support_tickets_proxy"],
                estimated_effort="small",
                confidence=_clip_conf(0.5),
                rationale="finding:deprecation_candidate",
            )
        )

    if FindingKind.LAUNCH_CANDIDATE.value in kinds and stage in (
        LifecycleStage.IDEA,
        LifecycleStage.BUILD,
        LifecycleStage.VALIDATE,
    ):
        key = "launch_gate"
        props.append(
            ExperimentProposal(
                proposal_id=_stable_id(product_id, key),
                product_id=product_id,
                hypothesis="Advance one validation gate with explicit success criteria and rollback.",
                type=ExperimentType.GROWTH,
                description=(
                    "Launch-candidate signals: narrow scope to the next gate (beta cohort, feature flag, "
                    "or checklist completion)."
                ),
                expected_outcome="Gate criteria met with no new critical findings.",
                success_metrics=["gate_checklist_completion", "critical_findings_count"],
                estimated_effort="small",
                confidence=_clip_conf(0.56),
                rationale="finding:launch_candidate+early_stage",
            )
        )

    if FindingKind.STRUCTURAL_READINESS.value in kinds and stage in (
        LifecycleStage.IDEA,
        LifecycleStage.BUILD,
    ):
        key = "validation_depth"
        props.append(
            ExperimentProposal(
                proposal_id=_stable_id(product_id, key),
                product_id=product_id,
                hypothesis="Add one operational KPI plus an exercise signal (or manual validation artifact) to satisfy the validation evidence contract.",
                type=ExperimentType.ENGAGEMENT,
                description=(
                    "Structural readiness: repo/metrics are inspectable but evidence is still thin for "
                    "validation-oriented reasoning."
                ),
                expected_outcome="Validation evidence contract satisfied or manual validation artifact recorded.",
                success_metrics=["operational_metrics_row_count", "secondary_validation_axes"],
                estimated_effort="small",
                confidence=_clip_conf(0.52),
                rationale="finding:structural_readiness",
            )
        )

    if FindingKind.VALIDATION_READINESS.value in kinds:
        key = "align_validate_manifest"
        props.append(
            ExperimentProposal(
                proposal_id=_stable_id(product_id, key),
                product_id=product_id,
                hypothesis="When evidence supports it, align lifecycle.stage in product.yaml with validation posture and document the next gate.",
                type=ExperimentType.ENGAGEMENT,
                description=(
                    "Validation evidence contract is satisfied — confirm manifest stage, operators, and "
                    "the next measurable validation gate."
                ),
                expected_outcome="Manifest and operator queue reflect validate-stage intent with evidence citations.",
                success_metrics=["lifecycle_stage_aligned", "validation_axes_documented"],
                estimated_effort="small",
                confidence=_clip_conf(0.54),
                rationale="finding:validation_readiness",
            )
        )

    if FindingKind.VALIDATION_EVIDENCE_GAP.value in kinds:
        key = "close_validation_evidence_gap"
        props.append(
            ExperimentProposal(
                proposal_id=_stable_id(product_id, key),
                product_id=product_id,
                hypothesis="Either add evidence to justify validate in YAML or downgrade stage until the contract is met.",
                type=ExperimentType.INFRASTRUCTURE,
                description=(
                    "Declared validate without validation evidence contract: add KPI + exercise axis, "
                    "or argus_validation_evidence completion, or revert lifecycle.stage to build."
                ),
                expected_outcome="Evidence contract satisfied or stage declaration corrected.",
                success_metrics=["validation_evidence_contract", "secondary_validation_axes"],
                estimated_effort="small",
                confidence=_clip_conf(0.55),
                rationale="finding:validation_evidence_gap",
            )
        )

    # --- Trend-driven ---
    if TrendFlag.STAGNATING.value in flags and stage == LifecycleStage.GROW:
        key = "output_freq"
        props.append(
            ExperimentProposal(
                proposal_id=_stable_id(product_id, key),
                product_id=product_id,
                hypothesis="Increase primary output frequency toward 3/day where the product is content-led.",
                type=ExperimentType.CONTENT,
                description=(
                    "Stagnation in grow stage: raise shipping cadence on the main surface area "
                    "(clips, posts, or releases) with a fixed calendar."
                ),
                expected_outcome="Sustained higher output for 2 weeks with engagement not regressing.",
                success_metrics=["outputs_per_day", "engagement_per_output"],
                estimated_effort="medium",
                confidence=_clip_conf(0.53),
                rationale="trend:stagnating+stage:grow",
            )
        )

    if TrendFlag.RISK_INCREASING.value in flags:
        key = "risk_mitigate"
        props.append(
            ExperimentProposal(
                proposal_id=_stable_id(product_id, key),
                product_id=product_id,
                hypothesis="Contain downside: cap spend and add guardrails on the riskiest workflow.",
                type=ExperimentType.INFRASTRUCTURE,
                description=(
                    "Risk-increasing trend: add rate limits, kill-switches, or staged rollout for "
                    "the highest-variance path."
                ),
                expected_outcome="Risk flags stabilize; escalations do not increase week over week.",
                success_metrics=["escalation_count", "incident_proxy"],
                estimated_effort="medium",
                confidence=_clip_conf(0.57),
                rationale="trend:risk_increasing",
            )
        )

    # --- Decision intent-driven ---
    if "reduce_cost" in intents:
        key = "intent_cost"
        props.append(
            ExperimentProposal(
                proposal_id=_stable_id(product_id, key),
                product_id=product_id,
                hypothesis="Replace or downsize the top cost driver identified in portfolio review.",
                type=ExperimentType.COST_REDUCTION,
                description=(
                    "Top decision intent is cost reduction: target one replaceable dependency "
                    "(e.g. managed service tier, always-on job, or redundant storage)."
                ),
                expected_outcome="Monthly cost down with operational risk documented.",
                success_metrics=["monthly_cost_usd", "p95_latency_proxy"],
                estimated_effort="medium",
                confidence=_clip_conf(0.6),
                rationale="decision_intent:reduce_cost",
            )
        )

    if "launch_experiment" in intents or "improve_product" in intents:
        key = "intent_exp"
        props.append(
            ExperimentProposal(
                proposal_id=_stable_id(product_id, key),
                product_id=product_id,
                hypothesis="Ship a small experiment tied to the top recommended action.",
                type=ExperimentType.GROWTH,
                description=(
                    "Decision candidates favor experimentation: define one metric, one change, "
                    "and a 1–2 week window."
                ),
                expected_outcome="Metric moves in expected direction vs control week.",
                success_metrics=["primary_kpi", "finding_severity_trend"],
                estimated_effort="small",
                confidence=_clip_conf(0.58),
                rationale="decision_intent:launch_experiment_or_improve",
            )
        )

    # --- Lifecycle / posture ---
    existing_rationale = {p.rationale for p in props}
    if kill_candidate and "finding:inactivity" not in existing_rationale:
        key = "pause_spend"
        props.append(
            ExperimentProposal(
                proposal_id=_stable_id(product_id, key),
                product_id=product_id,
                hypothesis="Pause non-essential spend for 7 days while observing organic signals only.",
                type=ExperimentType.COST_REDUCTION,
                description=(
                    "Kill-candidate posture: freeze discretionary budgets and noisy automation; "
                    "keep observability only."
                ),
                expected_outcome="Spend drops; organic signal clarity improves for a go/no-go.",
                success_metrics=["monthly_cost_usd", "organic_events_per_day"],
                estimated_effort="small",
                confidence=_clip_conf(0.48),
                rationale="lifecycle:kill_candidate",
            )
        )

    if stage in (LifecycleStage.GROW, LifecycleStage.MAINTAIN) and not any(
        p.type == ExperimentType.CONTENT for p in props
    ):
        key = "marketing_push"
        props.append(
            ExperimentProposal(
                proposal_id=_stable_id(product_id, key),
                product_id=product_id,
                hypothesis="Add a basic marketing push (one channel, one message) for two weeks.",
                type=ExperimentType.CONTENT,
                description=(
                    "Mature stage: run a minimal acquisition or re-activation push with a single CTA "
                    "and weekly review."
                ),
                expected_outcome="Measurable lift in visits or signups vs prior baseline week.",
                success_metrics=["landing_visits", "signup_or_activation_count"],
                estimated_effort="small",
                confidence=_clip_conf(0.5),
                rationale="stage:grow_or_maintain+default_marketing",
            )
        )

    # Dedupe by proposal_id (stable keys already dedupe rules)
    seen: set[str] = set()
    unique: list[ExperimentProposal] = []
    for p in sorted(props, key=lambda x: (x.type.value, x.proposal_id)):
        if p.proposal_id not in seen:
            seen.add(p.proposal_id)
            unique.append(p)
    return unique


def propose_experiments(
    repo_root: Path,
    *,
    product_id: str | None = None,
    inventory: ProductInventory | None = None,
) -> ProposalRun:
    """Generate proposals for one product or all valid inventory products."""
    root = repo_root.resolve()
    now = datetime.now(timezone.utc).isoformat()
    inv = inventory or build_inventory(root)
    pids = [product_id] if product_id else sorted(inv.valid.keys())
    all_p: list[ExperimentProposal] = []
    for pid in pids:
        if pid not in inv.valid:
            continue
        rec = inv.valid[pid]
        stage = rec.node.lifecycle.stage
        dec_raw = load_latest_product_decisions(root, pid)
        lc = (dec_raw or {}).get("lifecycle") or {}
        kill_candidate = bool(lc.get("kill_candidate"))
        all_p.extend(
            _collect_proposals_for_product(
                root,
                pid,
                lifecycle_stage=stage,
                kill_candidate=kill_candidate,
            )
        )
    return ProposalRun(generated_at_utc=now, repo_root=str(root), proposals=all_p)


def proposals_latest_path(repo_root: Path, product_id: str) -> Path:
    """Deterministic path for ``argus.experiment_proposals_run.v1`` for one product."""
    return repo_root.resolve() / "runs" / "experiments" / "proposals" / "latest" / f"{product_id}.json"


def proposal_from_dict(d: dict[str, Any]) -> ExperimentProposal:
    """Deserialize ``ExperimentProposal`` from JSON (e.g. persisted proposals latest)."""
    return ExperimentProposal(
        proposal_id=str(d["proposal_id"]),
        product_id=str(d["product_id"]),
        hypothesis=str(d.get("hypothesis", "")),
        type=ExperimentType(str(d["type"])),
        description=str(d.get("description", "")),
        expected_outcome=str(d.get("expected_outcome", "")),
        success_metrics=[str(x) for x in (d.get("success_metrics") or []) if x is not None],
        estimated_effort=str(d.get("estimated_effort", "small")),
        confidence=float(d.get("confidence", 0.55)),
        rationale=str(d.get("rationale", "")),
        schema=str(d.get("schema", "argus.experiment_proposal.v1")),
    )


def load_proposals_run_latest(repo_root: Path, product_id: str) -> ProposalRun:
    """Load persisted ``ProposalRun`` from ``proposals_latest_path`` (canonical input for prioritization)."""
    path = proposals_latest_path(repo_root, product_id)
    if not path.is_file():
        raise FileNotFoundError(str(path))
    raw = loads_json(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("proposals JSON must be an object")
    pr_raw = raw.get("proposals")
    if not isinstance(pr_raw, list):
        pr_raw = []
    proposals: list[ExperimentProposal] = []
    for item in pr_raw:
        if isinstance(item, dict):
            proposals.append(proposal_from_dict(item))
    return ProposalRun(
        generated_at_utc=str(raw.get("generated_at_utc") or ""),
        repo_root=str(raw.get("repo_root") or ""),
        proposals=proposals,
        schema=str(raw.get("schema", "argus.experiment_proposals_run.v1")),
    )


def save_proposals_run_latest(repo_root: Path, product_id: str, run: ProposalRun) -> Path:
    """Write ``ProposalRun`` JSON for ``product_id`` (suggestions only; not execution authority)."""
    path = proposals_latest_path(repo_root, product_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dumps_json(to_jsonable(run)), encoding="utf-8")
    return path


def format_proposals_text(run: ProposalRun) -> str:
    """Human-readable summary for CLI (non-JSON)."""
    lines = [
        "Experiment proposals (suggestions only; not created automatically)",
        f"Generated: {run.generated_at_utc}",
        f"Count: {len(run.proposals)}",
        "",
    ]
    for i, p in enumerate(run.proposals, start=1):
        lines.append(f"{i}. [{p.type.value}] {p.hypothesis}")
        lines.append(f"   id: {p.proposal_id}")
        lines.append(f"   product: {p.product_id}")
        lines.append(f"   effort: {p.estimated_effort}  confidence: {p.confidence:.2f}")
        lines.append(f"   rationale: {p.rationale}")
        lines.append(f"   metrics: {', '.join(p.success_metrics)}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
