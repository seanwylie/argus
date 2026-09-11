"""Require human approval before any shell execution (unless auto-approval rules apply)."""

from __future__ import annotations

from pathlib import Path

from argus.actions.models import ActionContract
from argus.approval.rules import evaluate_auto_approval
from argus.approval.store import has_approved_for_action


class ExecutionNotApprovedError(RuntimeError):
    """Raised when an action would run without approval (stored or auto)."""

    def __init__(self, action_id: str, product_id: str, message: str = "") -> None:
        self.action_id = action_id
        self.product_id = product_id
        default = (
            f"No manual approval and action does not satisfy auto-approval rules for "
            f"action_id={action_id!r} product_id={product_id!r}. "
            "Create a pending approval (`argus approval request`), then `argus approval approve <id>`, "
            "or use `argus approval evaluate <file>` to inspect auto-approval."
        )
        super().__init__(message or default)


def is_execution_approved(repo_root: Path, contract: ActionContract) -> bool:
    """
    True if there is a stored **approved** record or the contract passes
    :func:`~argus.approval.rules.evaluate_auto_approval`.
    """
    if has_approved_for_action(repo_root, contract.action_id, contract.product_id):
        return True
    return evaluate_auto_approval(contract, repo_root=repo_root).auto_approve


def require_execution_approval(repo_root: Path, contract: ActionContract) -> None:
    """
    Ensure stored approval or auto-approval rules allow execution.

    Raises :class:`ExecutionNotApprovedError` if neither applies.
    """
    if is_execution_approved(repo_root, contract):
        return
    raise ExecutionNotApprovedError(contract.action_id, contract.product_id)
