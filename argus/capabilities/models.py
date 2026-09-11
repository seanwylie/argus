"""Capability registry models (awareness scaffold; no auto-implementation)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

CapabilityCategory = Literal["analysis", "ingestion", "execution", "ui", "planning"]
CapabilityMaturity = Literal["stub", "basic", "advanced"]


@dataclass(frozen=True)
class Capability:
    """Something Argus can do today (this codebase)."""

    id: str
    name: str
    description: str
    category: CapabilityCategory
    maturity: CapabilityMaturity
    coverage: str
    known_gaps: tuple[str, ...] = ()
    last_updated: str = ""


@dataclass(frozen=True)
class MissingCapability:
    """Inferred or declared gap (not yet covered or only partially)."""

    id: str
    name: str
    description: str
    category: CapabilityCategory
    reason: str
    priority: int = 50


@dataclass
class CapabilityGapFinding:
    """
    Synthetic finding-like record for capability awareness (not persisted as core Finding).

    Serialized under ``runs/capabilities/`` by ``evaluate``.
    """

    id: str
    title: str
    summary: str
    gap_id: str
    product_id: str | None
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass
class CapabilityEvaluation:
    """Output of scanning findings and merging with inferred gaps."""

    schema: str = "argus.capability_evaluation.v1"
    evaluated_at_utc: str = ""
    repo_root: str = ""
    capabilities: list[Capability] = field(default_factory=list)
    missing_capabilities: list[MissingCapability] = field(default_factory=list)
    gap_findings: list[CapabilityGapFinding] = field(default_factory=list)
    suggested_next: str | None = None
    finding_aggregate: dict[str, Any] = field(default_factory=dict)
