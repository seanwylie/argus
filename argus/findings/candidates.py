"""Intermediate representation before consolidation and ID assignment."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from argus.core.models.enums import FindingKind, SeverityLevel


@dataclass
class FindingCandidate:
    """
    Emitted by a single rule; consolidated into one :class:`~argus.core.models.finding.Finding`
    per ``(kind, rule_id, issue_key)`` for a product.
    """

    rule_id: str
    issue_key: str
    kind: FindingKind
    title: str
    summary: str
    recommendation: str
    source_signal_ids: list[str] = field(default_factory=list)
    evidence: dict[str, Any] = field(default_factory=dict)
    severity_hint: SeverityLevel | None = None
    confidence: float | None = None
