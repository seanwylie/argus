"""Approval records for gated Argus execution."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


@dataclass
class ApprovalRecord:
    """One approval request tied to an action id and product."""

    approval_id: str
    action_id: str
    product_id: str
    status: ApprovalStatus
    reason: str = ""
    created_at: str = ""
    decided_at: str | None = None
    schema: str = "argus.approval.v1"
    metadata: dict[str, Any] = field(default_factory=dict)
