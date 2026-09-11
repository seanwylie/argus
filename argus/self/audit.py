"""Aggregate local artifacts into a self-critique report (no network)."""

from __future__ import annotations

import uuid
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from argus.capabilities.evaluate import evaluate_capabilities
from argus.decision.history.store import load_all_products_with_history
from argus.escalation.packet import list_packets
from argus.experiments.models import EvaluationVerdict, ExperimentStatus
from argus.experiments.store import list_experiments
from argus.self.findings import (
    SelfFinding,
    SelfFindingCategory,
    SelfFindingSeverity,
    finding_to_jsonable,
)


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


# Curated operator-facing lines (examples in product spec)
_GAP_MESSAGE: dict[str, str] = {
    "gap.ingestion.revenue_snapshots_unused": "Argus lacks revenue signal integration",
    "gap.ingestion.no_business_snapshots": "Argus has no ingested business snapshots to ground decisions",
    "gap.execution.experiment_tracking": "Experiment exposure is not tracked in the signal layer",
    "gap.analysis.causal_attribution": "Causal attribution is shallow — findings are rule- and delta-based only",
    "gap.ui.interactive_portfolio": "Portfolio UI is static; no interactive operator workflows",
}


def _gap_message(gap_id: str, fallback_name: str) -> str:
    if gap_id in _GAP_MESSAGE:
        return _GAP_MESSAGE[gap_id]
    return f"Capability gap: {fallback_name}"


def _parse_utc(s: str) -> datetime | None:
    if not s or not str(s).strip():
        return None
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return None


# Median gap between decision generations above this is "slow" (7 days)
_SLOW_MEDIAN_HOURS = 168.0
# Single interval between two runs above this is slow (14 days)
_SLOW_SINGLE_INTERVAL_HOURS = 336.0
# Same product appears this many times in escalation index → pattern
_REPEATED_ESCALATION_THRESHOLD = 3


def _median(values: list[float]) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    n = len(s)
    mid = n // 2
    if n % 2:
        return s[mid]
    return (s[mid - 1] + s[mid]) / 2.0


_MAX_CAPABILITY_FINDINGS = 8


def _audit_missing_capabilities(repo: Path) -> list[SelfFinding]:
    out: list[SelfFinding] = []
    ev = evaluate_capabilities(repo)
    # Surface highest-priority gaps as critique (dedupe by id)
    seen: set[str] = set()
    for m in sorted(ev.missing_capabilities, key=lambda x: (x.priority, x.id)):
        if m.id in seen:
            continue
        seen.add(m.id)
        sev = SelfFindingSeverity.HIGH if m.priority <= 20 else SelfFindingSeverity.MEDIUM
        if m.priority >= 50:
            sev = SelfFindingSeverity.LOW
        out.append(
            SelfFinding(
                id=_new_id("cap"),
                category=SelfFindingCategory.MISSING_CAPABILITY,
                severity=sev,
                message=_gap_message(m.id, m.name),
                detail=m.reason,
                evidence={"gap_id": m.id, "priority": m.priority, "category": m.category},
            )
        )
        if len(out) >= _MAX_CAPABILITY_FINDINGS:
            break
    return out


_MAX_SLOW_CADENCE_FINDINGS = 5


def _audit_decision_cadence(repo: Path) -> list[SelfFinding]:
    out: list[SelfFinding] = []
    all_h = load_all_products_with_history(repo)
    if not all_h:
        out.append(
            SelfFinding(
                id=_new_id("cadence"),
                category=SelfFindingCategory.SLOW_DECISION_CYCLE,
                severity=SelfFindingSeverity.INFO,
                message="No decision generations on disk — cannot measure decision cycle time",
                detail="Run `argus decisions generate` (or portfolio refresh) to build history.",
                evidence={},
            )
        )
        return out

    slow_scored: list[tuple[float, SelfFinding]] = []
    for pid, entries in sorted(all_h.items()):
        if len(entries) < 2:
            continue
        times: list[datetime] = []
        for e in entries:
            t = _parse_utc(e.generated_at_utc)
            if t is not None:
                times.append(t)
        if len(times) < 2:
            continue
        gaps_h: list[float] = []
        for i in range(1, len(times)):
            delta = times[i] - times[i - 1]
            gaps_h.append(delta.total_seconds() / 3600.0)

        med = _median(gaps_h)
        worst = max(gaps_h) if gaps_h else 0.0
        slow = med >= _SLOW_MEDIAN_HOURS or (
            len(gaps_h) == 1 and worst >= _SLOW_SINGLE_INTERVAL_HOURS
        )
        if slow:
            slow_scored.append(
                (
                    med,
                    SelfFinding(
                        id=_new_id("cadence"),
                        category=SelfFindingCategory.SLOW_DECISION_CYCLE,
                        severity=SelfFindingSeverity.MEDIUM,
                        message=f"Slow decision cycle for {pid!r}",
                        detail=(
                            f"Median gap between decision runs is {med:.1f}h "
                            f"(threshold {_SLOW_MEDIAN_HOURS:.0f}h); worst gap {worst:.1f}h."
                        ),
                        evidence={
                            "product_id": pid,
                            "runs": len(times),
                            "median_gap_hours": round(med, 2),
                            "max_gap_hours": round(worst, 2),
                        },
                    ),
                )
            )
    slow_scored.sort(key=lambda x: x[0], reverse=True)
    for _med, finding in slow_scored[:_MAX_SLOW_CADENCE_FINDINGS]:
        out.append(finding)
    return out


def _audit_escalations(repo: Path) -> list[SelfFinding]:
    rows = list_packets(repo, limit=500)
    if not rows:
        return []
    by_pid: Counter[str] = Counter()
    for r in rows:
        pid = r.get("product_id")
        if pid:
            by_pid[str(pid)] += 1
    out: list[SelfFinding] = []
    for pid, n in by_pid.items():
        if n >= _REPEATED_ESCALATION_THRESHOLD:
            out.append(
                SelfFinding(
                    id=_new_id("esc"),
                    category=SelfFindingCategory.REPEATED_ESCALATION,
                    severity=SelfFindingSeverity.HIGH if n >= 5 else SelfFindingSeverity.MEDIUM,
                    message=f"Repeated escalation for product {pid!r}",
                    detail=f"{n} packet(s) indexed under runs/escalations/latest/ (automation keeps halting).",
                    evidence={"product_id": pid, "packet_count": n},
                )
            )
    return out


def _audit_experiments(repo: Path) -> list[SelfFinding]:
    out: list[SelfFinding] = []
    exps = list_experiments(repo, product_id=None)
    if not exps:
        return []

    shallow = [e for e in exps if not e.success_metrics and e.status != ExperimentStatus.PROPOSED]
    if shallow:
        out.append(
            SelfFinding(
                id=_new_id("exp"),
                category=SelfFindingCategory.POOR_EXPERIMENT_OUTCOME,
                severity=SelfFindingSeverity.MEDIUM,
                message="Experiment evaluation too shallow",
                detail="Some active or completed experiments declare no success metrics.",
                evidence={
                    "experiment_ids": [e.id for e in shallow][:20],
                    "count": len(shallow),
                },
            )
        )

    failed_like = [
        e
        for e in exps
        if e.status == ExperimentStatus.FAILED
        or (
            e.last_evaluation_verdict is not None
            and e.last_evaluation_verdict == EvaluationVerdict.FAILED.value
        )
    ]
    inconclusive = [
        e
        for e in exps
        if e.last_evaluation_verdict == EvaluationVerdict.INCONCLUSIVE.value
    ]

    if len(failed_like) >= 2:
        out.append(
            SelfFinding(
                id=_new_id("exp"),
                category=SelfFindingCategory.POOR_EXPERIMENT_OUTCOME,
                severity=SelfFindingSeverity.MEDIUM,
                message="Several experiments ended in failure — outcomes are not validating hypotheses",
                detail=f"{len(failed_like)} experiment(s) failed or marked failed by evaluation.",
                evidence={
                    "failed_count": len(failed_like),
                    "experiment_ids": [e.id for e in failed_like][:20],
                },
            )
        )
    elif len(failed_like) == 1:
        out.append(
            SelfFinding(
                id=_new_id("exp"),
                category=SelfFindingCategory.POOR_EXPERIMENT_OUTCOME,
                severity=SelfFindingSeverity.LOW,
                message="At least one experiment failed — review hypothesis and signals",
                detail="Check snapshots and trends coverage for that product.",
                evidence={"experiment_ids": [failed_like[0].id]},
            )
        )

    if len(inconclusive) >= 3:
        out.append(
            SelfFinding(
                id=_new_id("exp"),
                category=SelfFindingCategory.POOR_EXPERIMENT_OUTCOME,
                severity=SelfFindingSeverity.MEDIUM,
                message="Experiment outcomes are often inconclusive — history or metrics may be too thin",
                detail=f"{len(inconclusive)} experiment(s) last evaluated as inconclusive.",
                evidence={"inconclusive_count": len(inconclusive)},
            )
        )

    return out


@dataclass(frozen=True)
class SelfAuditReport:
    """Full self-audit payload."""

    generated_at_utc: str
    repo_root: str
    findings: list[SelfFinding]

    def to_jsonable(self) -> dict[str, Any]:
        return {
            "schema": "argus.self_audit.v1",
            "generated_at_utc": self.generated_at_utc,
            "repo_root": self.repo_root,
            "findings": [finding_to_jsonable(f) for f in self.findings],
            "summary": {
                "count": len(self.findings),
                "by_category": {
                    c.value: sum(1 for f in self.findings if f.category == c)
                    for c in SelfFindingCategory
                },
            },
        }


def run_self_audit(repo_root: Path) -> SelfAuditReport:
    """Scan local runs/ and capability state; return critique findings."""
    root = repo_root.resolve()
    now = datetime.now(timezone.utc).isoformat()
    findings: list[SelfFinding] = []
    findings.extend(_audit_missing_capabilities(root))
    findings.extend(_audit_decision_cadence(root))
    findings.extend(_audit_escalations(root))
    findings.extend(_audit_experiments(root))
    # Stable order: category then severity rank then message
    sev_rank = {
        SelfFindingSeverity.HIGH: 0,
        SelfFindingSeverity.MEDIUM: 1,
        SelfFindingSeverity.LOW: 2,
        SelfFindingSeverity.INFO: 3,
    }
    findings.sort(
        key=lambda f: (f.category.value, sev_rank.get(f.severity, 9), f.message),
    )
    return SelfAuditReport(generated_at_utc=now, repo_root=str(root), findings=findings)
