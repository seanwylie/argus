"""Findings produced from one or more signals."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from argus.core.models.enums import EffortBucket, FindingKind, SeverityLevel


@dataclass
class Finding:
    """
    Structured issue or opportunity derived from signals.

    ``evidence`` holds pointers or excerpts (e.g. metric snapshots) suitable
    for serialization; keep secrets out.
    """

    id: str
    product_id: str
    kind: FindingKind
    severity: SeverityLevel
    effort: EffortBucket
    title: str
    summary: str
    recommendation: str
    source_signals: list[str] = field(default_factory=list)
    evidence: dict[str, Any] = field(default_factory=dict)
    confidence: float | None = None
    created_at: datetime | None = None
