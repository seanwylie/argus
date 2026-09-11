"""Scan findings and merge with inferred capability gaps (awareness only)."""

from __future__ import annotations

import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from argus.capabilities.models import (
    CapabilityEvaluation,
    CapabilityGapFinding,
    MissingCapability,
)
from argus.capabilities.registry import (
    current_capabilities,
    infer_missing_capabilities,
    suggest_next_build,
)
from argus.core.serialize import dumps_json, to_jsonable
from argus.findings.persistence import load_all_latest_summaries, load_latest_findings
from argus.products.inventory import build_inventory


def _new_gap_finding_id() -> str:
    return f"cgap-{uuid.uuid4().hex[:12]}"


def _aggregate_findings(repo_root: Path) -> dict[str, Any]:
    summary = load_all_latest_summaries(repo_root)
    kinds: Counter[str] = Counter()
    products_with_findings = 0
    for _pid, row in summary.items():
        products_with_findings += 1
        for k, n in (row.get("by_kind") or {}).items():
            kinds[k] += int(n)
    return {
        "product_count": len(summary),
        "products_with_latest_findings": products_with_findings,
        "kinds_total": dict(sorted(kinds.items())),
    }


def _reliability_problem_count(repo_root: Path) -> int:
    inv = build_inventory(repo_root)
    n = 0
    for pid in inv.valid:
        b = load_latest_findings(repo_root, pid)
        if not b:
            continue
        n += sum(1 for f in b.findings if f.kind.value == "reliability_problem")
    return n


def _cost_risk_count(repo_root: Path) -> int:
    inv = build_inventory(repo_root)
    n = 0
    for pid in inv.valid:
        b = load_latest_findings(repo_root, pid)
        if not b:
            continue
        n += sum(1 for f in b.findings if f.kind.value == "cost_risk")
    return n


def _dedupe_missing(rows: list[MissingCapability]) -> list[MissingCapability]:
    seen: set[str] = set()
    out: list[MissingCapability] = []
    for m in sorted(rows, key=lambda x: (x.priority, x.id)):
        if m.id not in seen:
            seen.add(m.id)
            out.append(m)
    return out


def build_gap_findings(
    repo_root: Path,
    base_missing: list[MissingCapability],
    agg: dict[str, Any],
) -> tuple[list[CapabilityGapFinding], list[MissingCapability]]:
    """Merge inferred gaps with finding-driven gaps; emit one synthetic finding per gap."""
    extra: list[MissingCapability] = []

    if _reliability_problem_count(repo_root) >= 3:
        extra.append(
            MissingCapability(
                id="gap.analysis.observability_stress",
                name="Elevated reliability findings",
                description="Many reliability_problem findings across products; may need richer signals.",
                category="analysis",
                reason="Heuristic: ≥3 reliability_problem findings across latest bundles.",
                priority=20,
            )
        )

    from argus.capabilities.registry import snapshot_usage_filenames

    if _cost_risk_count(repo_root) >= 4 and len(snapshot_usage_filenames(repo_root)) == 0:
        extra.append(
            MissingCapability(
                id="gap.ingestion.cost_context",
                name="Cost risk without snapshot context",
                description="Several cost_risk findings but no business snapshot files in repo.",
                category="ingestion",
                reason="Heuristic: ≥4 cost_risk findings and zero snapshot files.",
                priority=18,
            )
        )

    merged = _dedupe_missing(list(base_missing) + extra)
    findings: list[CapabilityGapFinding] = []
    for m in merged:
        findings.append(
            CapabilityGapFinding(
                id=_new_gap_finding_id(),
                title=f"Capability gap: {m.name}",
                summary=f"{m.description} — {m.reason}",
                gap_id=m.id,
                product_id=None,
                evidence={
                    "reason": m.reason,
                    "category": m.category,
                    "priority": m.priority,
                    "finding_aggregate": agg,
                },
            )
        )
    return findings, merged


def evaluate_capabilities(repo_root: Path) -> CapabilityEvaluation:
    root = repo_root.resolve()
    now = datetime.now(timezone.utc).isoformat()
    caps = current_capabilities()
    base_missing = infer_missing_capabilities(root)
    agg = _aggregate_findings(root)
    gap_findings, merged_missing = build_gap_findings(root, base_missing, agg)
    suggested = suggest_next_build(merged_missing)

    return CapabilityEvaluation(
        evaluated_at_utc=now,
        repo_root=str(root),
        capabilities=caps,
        missing_capabilities=merged_missing,
        gap_findings=gap_findings,
        suggested_next=suggested,
        finding_aggregate=agg,
    )


def evaluation_to_json(ev: CapabilityEvaluation) -> str:
    return dumps_json(to_jsonable(ev))


def write_evaluation_artifact(repo_root: Path, ev: CapabilityEvaluation) -> Path:
    d = repo_root.resolve() / "runs" / "capabilities"
    d.mkdir(parents=True, exist_ok=True)
    p = d / "latest.json"
    p.write_text(evaluation_to_json(ev), encoding="utf-8")
    return p
