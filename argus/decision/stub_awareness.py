"""
Architecture stub / capability-gap awareness for decision confidence.

Only **relevant** stubs and gaps adjust confidence (tag intersection). This avoids
global collapse when unrelated parts of the repo are placeholders.

See ``docs/stub-inventory.md`` — registry IDs here should stay aligned with that doc.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from argus.core.models.decision import DecisionCandidate
from argus.core.models.enums import ActionType, FindingKind
from argus.core.models.finding import Finding
from argus.decision.intents import DecisionIntent
from argus.decision.priority import attach_priority
from argus.lifecycle.model import LifecycleAssessment
from argus.strategy.modes import StrategyProfile

# --- Architectural stubs (always-on for this codebase unless overridden) ------------

# Autonomy shutdown: cloud/SaaS teardown is stubbed (argus.autonomy.shutdown.resource_cleanup_stub).
STUB_AUTONOMY_RESOURCE_CLEANUP = "stub.autonomy.resource_cleanup"

@dataclass(frozen=True)
class ArchitecturalStub:
    """A known stub with tags describing which decisions it should affect."""

    id: str
    tags: frozenset[str]
    confidence_factor: float  # multiply into candidate confidence when matched (deterministic)
    rationale_line: str


ARCHITECTURAL_STUBS: tuple[ArchitecturalStub, ...] = (
    ArchitecturalStub(
        id=STUB_AUTONOMY_RESOURCE_CLEANUP,
        tags=frozenset({"cleanup", "shutdown", "terminal", "archive"}),
        confidence_factor=0.88,
        rationale_line=(
            "autonomy shutdown still uses resource_cleanup_stub (no automated cloud/SaaS teardown)"
        ),
    ),
)


# Capability gaps (from registry / evaluate) → relevance tags + penalty when decision touches that area.
CAPABILITY_GAP_IMPACT: dict[str, tuple[frozenset[str], float, str]] = {
    "gap.execution.experiment_tracking": (
        frozenset({"experiment", "launch"}),
        0.90,
        "experiment tracking adapters are not first-class (gap.execution.experiment_tracking)",
    ),
    "gap.analysis.causal_attribution": (
        frozenset({"causal", "attribution", "deep_analysis"}),
        0.94,
        "causal attribution beyond heuristics is not implemented",
    ),
}


@dataclass
class StubGapContext:
    """
    Optional inputs for stub / gap awareness.

    - ``active_stub_ids``: architectural stubs considered active (e.g. inferred from repo).
    - ``capability_gap_ids``: missing capability ids (e.g. from ``infer_missing_capabilities`` or latest.json).
    - ``incomplete_areas``: extra stub ids to treat as active (operator override, same ID namespace).
    """

    active_stub_ids: frozenset[str] = field(default_factory=frozenset)
    capability_gap_ids: frozenset[str] = field(default_factory=frozenset)
    incomplete_areas: frozenset[str] = field(default_factory=frozenset)


def _scale_confidence(base: float | None, factor: float) -> float:
    v = 0.55 if base is None else float(base)
    return max(0.05, min(0.99, v * factor))


def _intent_from_metadata(md: dict[str, Any]) -> DecisionIntent | None:
    intent_str = md.get("intent")
    if not intent_str:
        return None
    try:
        return DecisionIntent(str(intent_str))
    except ValueError:
        return None


def _relevance_tags_for_candidate(
    c: DecisionCandidate,
    finding: Finding | None,
) -> frozenset[str]:
    """Deterministic tags describing what the decision relies on architecturally."""
    tags: set[str] = set()
    intent = _intent_from_metadata(c.metadata or {})
    if intent == DecisionIntent.LAUNCH_EXPERIMENT:
        tags.update({"experiment", "launch"})
    if intent == DecisionIntent.GATHER_MORE_DATA:
        tags.update({"ingestion", "signals", "adapters"})
    if intent in (DecisionIntent.KILL_PRODUCT, DecisionIntent.DEPRECATE_PRODUCT):
        tags.update({"cleanup", "shutdown", "terminal", "archive"})
    if intent == DecisionIntent.ESCALATE_TO_HUMAN:
        tags.add("escalation")
    if intent == DecisionIntent.REDUCE_COST:
        tags.update({"cost", "spend"})
    if intent == DecisionIntent.IMPROVE_PRODUCT:
        tags.update({"quality", "engineering"})

    at = c.action_type
    if at in (ActionType.STOP, ActionType.DEPRECATE, ActionType.ARCHIVE):
        tags.update({"cleanup", "terminal"})
    if at == ActionType.CUSTOM:
        tags.add("custom_automation")

    if finding is not None:
        k = finding.kind
        if k in (FindingKind.COST_RISK,):
            tags.update({"cost", "spend"})
        if k in (FindingKind.GROWTH_OPPORTUNITY, FindingKind.LAUNCH_CANDIDATE):
            tags.update({"experiment", "launch"})
        if k == FindingKind.STRUCTURAL_READINESS:
            tags.update({"observability", "validation_gap"})
        if k == FindingKind.VALIDATION_READINESS:
            tags.update({"observability", "validation_ready"})
        if k == FindingKind.VALIDATION_EVIDENCE_GAP:
            tags.update({"observability", "validation_gap", "lifecycle"})
        if k in (FindingKind.RELIABILITY_PROBLEM, FindingKind.QUALITY_ISSUE):
            tags.update({"engineering", "observability"})
        if k in (FindingKind.INACTIVITY, FindingKind.DEPRECATION_CANDIDATE):
            tags.update({"terminal", "lifecycle"})

    return frozenset(tags)


def _active_stub_set(ctx: StubGapContext) -> frozenset[str]:
    return frozenset(ctx.active_stub_ids | ctx.incomplete_areas)


def infer_stub_gap_context(repo_root: Path | None) -> StubGapContext:
    """
    Build default context from repo layout + capability inference (deterministic).

    Sources:
    - Always include ``stub.autonomy.resource_cleanup`` (documented placeholder).
    - Capability gaps from :func:`argus.capabilities.registry.infer_missing_capabilities`.
    - Optional merge of ``runs/capabilities/latest.json`` missing id list when present and valid.
    """
    if repo_root is None:
        return StubGapContext()

    root = repo_root.resolve()
    active: set[str] = {STUB_AUTONOMY_RESOURCE_CLEANUP}

    gap_ids: set[str] = set()
    try:
        from argus.capabilities.registry import infer_missing_capabilities

        for m in infer_missing_capabilities(root):
            gap_ids.add(m.id)
    except (OSError, ValueError, TypeError):
        pass

    cap_json = root / "runs" / "capabilities" / "latest.json"
    if cap_json.is_file():
        try:
            raw = json.loads(cap_json.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                for row in raw.get("missing_capabilities") or []:
                    if isinstance(row, dict) and row.get("id"):
                        gap_ids.add(str(row["id"]))
        except (OSError, json.JSONDecodeError, TypeError):
            pass

    return StubGapContext(
        active_stub_ids=frozenset(active),
        capability_gap_ids=frozenset(sorted(gap_ids)),
    )


def _matched_architectural(
    active: frozenset[str],
    relevance: frozenset[str],
) -> list[ArchitecturalStub]:
    out: list[ArchitecturalStub] = []
    for stub in ARCHITECTURAL_STUBS:
        if stub.id not in active:
            continue
        if stub.tags & relevance:
            out.append(stub)
    return out


def _matched_capability_gaps(
    gap_ids: frozenset[str],
    relevance: frozenset[str],
) -> list[tuple[str, float, str]]:
    out: list[tuple[str, float, str]] = []
    for gid in sorted(gap_ids):
        row = CAPABILITY_GAP_IMPACT.get(gid)
        if row is None:
            continue
        tags, factor, desc = row
        if tags & relevance:
            out.append((gid, factor, desc))
    return out


def apply_stub_awareness_to_candidates(
    repo_root: Path | None,
    product_id: str,
    findings: list[Finding],
    candidates: list[DecisionCandidate],
    assessment: LifecycleAssessment,
    *,
    monthly_spend: float | None,
    spend_cap: float | None,
    strategy_profile: StrategyProfile | None,
    stub_context: StubGapContext | None = None,
) -> list[DecisionCandidate]:
    """
    Adjust confidence and metadata when decisions intersect active stubs or capability gaps.

    Recomputes priority scores after adjustments (same pattern as freshness).
    """
    if stub_context is None:
        stub_context = infer_stub_gap_context(repo_root)

    by_fid = {f.id: f for f in findings}
    active = _active_stub_set(stub_context)
    gap_ids = stub_context.capability_gap_ids

    out: list[DecisionCandidate] = []
    for c in candidates:
        md = dict(c.metadata or {})
        fid = md.get("finding_id")
        finding = by_fid.get(str(fid)) if fid else None
        rel = _relevance_tags_for_candidate(c, finding)

        arch = _matched_architectural(active, rel)
        gaps = _matched_capability_gaps(gap_ids, rel)

        factor = 1.0
        names: list[str] = []
        for stub in arch:
            factor *= stub.confidence_factor
            names.append(stub.id)
        for gid, gf, _desc in gaps:
            factor *= gf
            names.append(gid)

        factor = max(0.05, min(0.99, factor))

        if arch or gaps:
            base_conf = c.confidence
            c.confidence = _scale_confidence(base_conf, factor)
            pieces = [s.rationale_line for s in arch] + [g[2] for g in gaps]
            note = (
                " Confidence reduced because relevant subsystem(s) are stubbed or incomplete: "
                + "; ".join(pieces)
                + "."
            )
            r = (c.rationale or "").rstrip()
            if note.strip() not in r:
                c.rationale = r + note

            md["stub_awareness"] = {
                "product_id": product_id,
                "relevance_tags": sorted(rel),
                "matched_architectural_stub_ids": [s.id for s in arch],
                "matched_capability_gap_ids": [g[0] for g in gaps],
                "active_stub_ids_considered": sorted(active),
                "capability_gap_ids_considered": sorted(gap_ids),
                "applied_confidence_factor": round(factor, 4),
            }
        else:
            md["stub_awareness"] = {
                "product_id": product_id,
                "relevance_tags": sorted(rel),
                "matched_architectural_stub_ids": [],
                "matched_capability_gap_ids": [],
                "active_stub_ids_considered": sorted(active),
                "capability_gap_ids_considered": sorted(gap_ids),
                "applied_confidence_factor": 1.0,
            }

        c.metadata = md
        attach_priority(
            c,
            finding=finding,
            assessment=assessment,
            monthly_spend=monthly_spend,
            spend_cap=spend_cap,
            strategy_profile=strategy_profile,
        )
        out.append(c)

    out.sort(key=lambda x: (x.priority_score or 0), reverse=True)
    return out


def architecture_stub_risk_for_assessment(
    repo_root: Path,
    product_id: str,
    findings: list[Finding],
    *,
    stub_context: StubGapContext | None = None,
) -> float:
    """
    Map tag-matched stub/gap multipliers for the top priority candidate to ``architecture_stub_risk`` (0..1)
    for :func:`argus.decision_assessment.confidence.score_confidence`.

    Uses the same relevance rules as :func:`apply_stub_awareness_to_candidates` without mutating candidates.
    Aligns with ``stub_blend = 1.0 - 0.4 * architecture_stub_risk`` in ``score_confidence``.
    """
    from argus.core.serialize import decision_candidate_from_dict
    from argus.decision.persistence import load_latest_product_decisions

    ctx = stub_context if stub_context is not None else infer_stub_gap_context(repo_root)
    raw = load_latest_product_decisions(repo_root, product_id)
    if not raw or not isinstance(raw, dict):
        return 0.0
    cands_raw = raw.get("candidates") or []
    if not cands_raw:
        return 0.0
    candidates: list[DecisionCandidate] = []
    for d in cands_raw:
        if not isinstance(d, dict):
            continue
        try:
            candidates.append(decision_candidate_from_dict(d))
        except (KeyError, TypeError, ValueError):
            continue
    if not candidates:
        return 0.0
    candidates.sort(key=lambda c: c.priority_score or 0.0, reverse=True)
    top = candidates[0]
    by_fid = {f.id: f for f in findings}
    active = _active_stub_set(ctx)
    gap_ids = ctx.capability_gap_ids
    md = dict(top.metadata or {})
    fid = md.get("finding_id")
    finding = by_fid.get(str(fid)) if fid else None
    rel = _relevance_tags_for_candidate(top, finding)
    arch = _matched_architectural(active, rel)
    gaps = _matched_capability_gaps(gap_ids, rel)
    factor = 1.0
    for stub in arch:
        factor *= stub.confidence_factor
    for _gid, gf, _desc in gaps:
        factor *= gf
    factor = max(0.05, min(0.99, factor))
    if factor >= 0.999:
        return 0.0
    return max(0.0, min(1.0, (1.0 - factor) / 0.4))
