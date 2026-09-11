"""Decision candidates and selected action proposals."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from argus.core.models.enums import ActionType


@dataclass
class DecisionCandidate:
    """A scored option Argus might execute."""

    id: str
    product_id: str
    action_type: ActionType
    summary: str
    expected_impact: str = ""
    estimated_cost: float | None = None
    confidence: float | None = None
    rationale: str = ""
    priority_score: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ActionProposal:
    """Chosen next action for a product, ready for execution or logging."""

    id: str
    product_id: str
    action_type: ActionType
    command: str
    reason: str
    expected_outcome: str = ""
    rollback_notes: str = ""
    selected_at: datetime | None = None
