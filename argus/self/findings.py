"""Structured findings for Argus self-audit (meta-critique of local pipeline health)."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class SelfFindingCategory(StrEnum):
    MISSING_CAPABILITY = "missing_capability"
    SLOW_DECISION_CYCLE = "slow_decision_cycle"
    REPEATED_ESCALATION = "repeated_escalation"
    POOR_EXPERIMENT_OUTCOME = "poor_experiment_outcome"


class SelfFindingSeverity(StrEnum):
    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass
class SelfFinding:
    """One actionable critique of how Argus is performing in this repo."""

    id: str
    category: SelfFindingCategory
    severity: SelfFindingSeverity
    message: str
    detail: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)


def finding_to_jsonable(f: SelfFinding) -> dict[str, Any]:
    return {
        "id": f.id,
        "category": f.category.value,
        "severity": f.severity.value,
        "message": f.message,
        "detail": f.detail,
        "evidence": f.evidence,
    }
