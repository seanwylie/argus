"""Heuristic self-findings from local repo state (no code modification)."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from argus.approval.store import ApprovalStatus, list_records
from argus.capabilities.evaluate import evaluate_capabilities
from argus.decision.history.analyze import analyze_churn
from argus.decision.history.store import load_all_products_with_history
from argus.escalation.packet import list_packets
from argus.experiments.models import ExperimentStatus
from argus.experiments.store import list_experiments
from argus.products.inventory import build_inventory
from argus.self_improvement.models import (
    SelfFindingKind,
    SelfImprovementFinding,
    new_finding_id,
)


def _count_py_files(root: Path, pattern: str) -> int:
    n = 0
    for p in root.rglob(pattern):
        if p.is_file() and "__pycache__" not in p.parts:
            n += 1
    return n


def _execution_run_stats(repo_root: Path) -> dict[str, Any]:
    ex = repo_root / "runs" / "execution"
    if not ex.is_dir():
        return {"runs": 0, "success": 0, "failed": 0}
    success = 0
    failed = 0
    total = 0
    for p in ex.rglob("*.json"):
        if p.name.startswith("."):
            continue
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(raw, dict):
            continue
        st = str(raw.get("status", "")).lower()
        if st not in ("success", "failed"):
            continue
        total += 1
        if st == "success":
            success += 1
        else:
            failed += 1
    return {"runs": total, "success": success, "failed": failed}


def generate_self_findings(repo_root: Path) -> list[SelfImprovementFinding]:
    """Derive structured self-findings from repository artifacts."""
    root = repo_root.resolve()
    out: list[SelfImprovementFinding] = []

    # --- Test coverage heuristic (argus package vs tests) ---
    argus_py = _count_py_files(root / "argus", "*.py")
    test_py = _count_py_files(root / "tests", "test_*.py")
    if argus_py >= 20:
        ratio = test_py / max(1, argus_py)
        if ratio < 0.12:
            out.append(
                SelfImprovementFinding(
                    id=new_finding_id(),
                    kind=SelfFindingKind.WEAK_TEST_COVERAGE,
                    title="Test surface appears thin relative to package size",
                    summary=(
                        f"Counted {test_py} test modules vs ~{argus_py} Python modules under argus/. "
                        "Consider expanding coverage in high-risk areas (execution, autonomy, planning)."
                    ),
                    evidence={"test_modules": test_py, "argus_modules": argus_py, "ratio": round(ratio, 4)},
                    suggested_severity="medium",
                )
            )

    # --- Capability gaps (top few) + one integration representative ---
    ev = evaluate_capabilities(root)
    missing = sorted(ev.missing_capabilities or [], key=lambda g: -int(g.priority))[:8]
    integ_gap = None
    for gap in missing:
        gid = (gap.id or "").lower()
        if integ_gap is None and any(
            x in gid for x in ("ingestion", "adapter", "integration", "snapshot", "signal")
        ):
            integ_gap = gap
    for gap in missing[:6]:
        out.append(
            SelfImprovementFinding(
                id=new_finding_id(),
                kind=SelfFindingKind.MISSING_CAPABILITY,
                title=f"Capability gap: {gap.name}",
                summary=gap.description or gap.reason,
                evidence={"gap_id": gap.id, "priority": gap.priority, "category": gap.category},
                suggested_severity="high" if gap.priority >= 40 else "medium",
            )
        )
    if integ_gap is not None:
        out.append(
            SelfImprovementFinding(
                id=new_finding_id(),
                kind=SelfFindingKind.MISSING_INTEGRATION_PATH,
                title=f"Integration path may be incomplete: {integ_gap.name}",
                summary=integ_gap.reason or integ_gap.description,
                evidence={"gap_id": integ_gap.id},
                suggested_severity="medium",
            )
        )

    # --- Repeated escalation titles (portfolio-wide) ---
    rows = list_packets(root, limit=600)
    titles = [str(r.get("title", "") or "").strip() for r in rows if r.get("title")]
    tc = Counter(titles)
    repeats = [(t, n) for t, n in tc.items() if n >= 3 and t]
    if repeats:
        top = sorted(repeats, key=lambda x: -x[1])[:5]
        out.append(
            SelfImprovementFinding(
                id=new_finding_id(),
                kind=SelfFindingKind.REPEATED_ESCALATION_PATTERN,
                title="Repeated escalation themes detected",
                summary=(
                    "Some escalation titles recur frequently — may indicate unresolved systemic friction "
                    "or missing automation."
                ),
                evidence={"repeated_titles": [{"title": t, "count": n} for t, n in top]},
                suggested_severity="high",
            )
        )

    # --- Decision churn (max across products) ---
    histories = load_all_products_with_history(root)
    worst: tuple[str, float] | None = None
    for pid, entries in histories.items():
        if len(entries) < 3:
            continue
        ch = analyze_churn(pid, entries, repo_root=root).churn_score
        if worst is None or ch > worst[1]:
            worst = (pid, ch)
    if worst and worst[1] >= 0.5:
        out.append(
            SelfImprovementFinding(
                id=new_finding_id(),
                kind=SelfFindingKind.DECISION_CHURN_ARGUS,
                title="High decision churn in portfolio memory",
                summary=(
                    f"Largest churn_score among products with history is {worst[1]:.2f} "
                    f"({worst[0]}). Argus recommendations may be oscillating for some products."
                ),
                evidence={"worst_product_id": worst[0], "churn_score": worst[1]},
                suggested_severity="medium",
            )
        )

    # --- Invalid product manifests (schema / validation gap signal) ---
    inv = build_inventory(root)
    if inv.summary.invalid_count > 0:
        out.append(
            SelfImprovementFinding(
                id=new_finding_id(),
                kind=SelfFindingKind.MISSING_SCHEMA_VALIDATION,
                title="Some product manifests fail validation",
                summary=(
                    f"{inv.summary.invalid_count} invalid product.yaml entries — tighten validation or "
                    "fix manifests so automation can rely on structured data."
                ),
                evidence={"invalid_count": inv.summary.invalid_count},
                suggested_severity="high",
            )
        )

    # --- Execution feedback loop ---
    exs = _execution_run_stats(root)
    if exs["runs"] == 0:
        out.append(
            SelfImprovementFinding(
                id=new_finding_id(),
                kind=SelfFindingKind.POOR_EXECUTION_FEEDBACK,
                title="Little or no execution run history",
                summary=(
                    "No recorded execution runs under runs/execution — harder to close the loop between "
                    "recommended actions and observed outcomes."
                ),
                evidence=exs,
                suggested_severity="low",
            )
        )
    elif exs["failed"] > exs["success"] and exs["runs"] >= 3:
        out.append(
            SelfImprovementFinding(
                id=new_finding_id(),
                kind=SelfFindingKind.POOR_EXECUTION_FEEDBACK,
                title="Execution runs skew toward failure",
                summary=(
                    f"Recorded {exs['failed']} failed vs {exs['success']} successful runs — "
                    "review sandbox policy, approvals, and command safety."
                ),
                evidence=exs,
                suggested_severity="high",
            )
        )

    # --- Operator friction: pending approvals ---
    try:
        recs = list_records(root)
        pending = [r for r in recs if r.status == ApprovalStatus.PENDING]
        if len(pending) >= 5:
            out.append(
                SelfImprovementFinding(
                    id=new_finding_id(),
                    kind=SelfFindingKind.OPERATOR_FRICTION,
                    title="Many pending approval records",
                    summary=(
                        f"{len(pending)} pending approval records — may indicate friction in the "
                        "request/approve path or unclear auto-approval boundaries."
                    ),
                    evidence={"pending_count": len(pending)},
                    suggested_severity="medium",
                )
            )
    except OSError:
        pass

    # --- Experiments without evaluation ---
    try:
        all_ex = list_experiments(root)
        uneval = [
            e for e in all_ex if e.last_evaluation_verdict is None and e.status == ExperimentStatus.ACTIVE
        ]
        if len(all_ex) >= 4 and len(uneval) >= 3:
            out.append(
                SelfImprovementFinding(
                    id=new_finding_id(),
                    kind=SelfFindingKind.POOR_EXECUTION_FEEDBACK,
                    title="Experiments running without fresh evaluation signals",
                    summary=(
                        "Several experiments are still marked running without evaluation verdicts — "
                        "tighten experiment evaluation cadence or automation."
                    ),
                    evidence={"unevaluated_running": len(uneval), "total": len(all_ex)},
                    suggested_severity="low",
                )
            )
    except (OSError, TypeError, ValueError):
        pass

    return out
