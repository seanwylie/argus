"""Heuristic auto-approval rules for safe action execution (no manual approval record)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from argus.actions.models import ActionContract


@dataclass
class AutoApprovalEvaluation:
    """Result of :func:`evaluate_auto_approval`."""

    auto_approve: bool
    """True when the action may run without a stored approval record."""

    reasons: list[str] = field(default_factory=list)
    """Why auto-approval was granted or denied (human-readable)."""


def evaluate_auto_approval(
    contract: ActionContract,
    *,
    repo_root: Path,
    decision_metadata: dict | None = None,
) -> AutoApprovalEvaluation:
    """
    Decide if an action qualifies for **auto-approval** (no manual approval record).

    Delegates to :func:`~argus.autonomy.safe_execution.evaluate_safe_autonomy` (analyze / investigate /
    generate, read-only or ``runs/``-only artifacts, experiment linkage rules).

    Stored manual approval always satisfies the gate via
    :func:`~argus.approval.store.has_approved_for_action`.
    """
    from argus.autonomy.safe_execution import evaluate_safe_autonomy

    ev = evaluate_safe_autonomy(
        contract,
        repo_root=repo_root,
        decision_metadata=decision_metadata,
    )
    return AutoApprovalEvaluation(auto_approve=ev.autonomous, reasons=ev.reasons)
