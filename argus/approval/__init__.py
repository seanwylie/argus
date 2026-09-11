"""Human approval gate before Argus executes actions."""

from argus.approval.models import ApprovalRecord, ApprovalStatus
from argus.approval.rules import AutoApprovalEvaluation, evaluate_auto_approval
from argus.approval.store import (
    approve,
    create_pending,
    has_approved_for_action,
    list_records,
    load_record,
    reject,
)

__all__ = [
    "ApprovalRecord",
    "ApprovalStatus",
    "AutoApprovalEvaluation",
    "approve",
    "create_pending",
    "evaluate_auto_approval",
    "has_approved_for_action",
    "list_records",
    "load_record",
    "reject",
]
