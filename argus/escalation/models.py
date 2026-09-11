"""Canonical escalation packet model (halt-and-handoff; no execution)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any


class SourceType(StrEnum):
    """What primarily caused the escalation."""

    FINDING = "finding"
    DECISION = "decision"
    LIFECYCLE = "lifecycle"
    POLICY = "policy"
    COMPOSITE = "composite"


class RiskLevel(StrEnum):
    """Overall packet severity for triage."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class EscalationOption:
    """
    One actionable choice for an operator or downstream system.

    ``command`` is a suggested Argus/CLI string (documentation only — not executed here).
    """

    action: str
    description: str
    command: str
    risk: str
    requires_human_confirmation: bool


@dataclass
class EscalationPacket:
    """
    Self-contained halt-and-handoff record: why Argus stopped, what fired, what you can do next.

    No hidden context: ``context_files`` lists on-disk artifacts to open; ``notes`` explains scope.
    """

    packet_id: str
    created_at: datetime
    product_id: str
    source_type: SourceType
    source_id: str
    stopped_stage: str
    title: str
    summary: str
    why_stopped: str
    risk_level: RiskLevel
    triggering_rules: list[str]
    options: list[EscalationOption]
    context_files: list[str] = field(default_factory=list)
    notes: str = ""

    # Optional structured extras for tools (schemas, version hints)
    metadata: dict[str, Any] = field(default_factory=dict)

    # --- Decision-confidence enrichment (optional; omitted in legacy packets) ---
    confidence_score: float | None = None
    """Top-decision confidence 0–1 when available."""
    uncertainty_score: float | None = None
    """Complementary uncertainty 0–1 (higher = less known)."""
    risk_score: float | None = None
    """Normalized posture risk 0–1 (lifecycle/kill + trigger severity)."""
    recurrence_risk_score: float | None = None
    """0–1: recent repeated escalations for this product (gun-shy signal)."""
    escalation_pressure: float | None = None
    """0–1 blended pressure: uncertainty, risk, recurrence."""
    top_uncertainty_factors: list[str] = field(default_factory=list)
    """Human-readable drivers of doubt (missing data, low confidence, contradictions)."""
    top_blocking_factors: list[str] = field(default_factory=list)
    """Policy or safety blocks (cost, unsafe contract, confidence floor, etc.)."""
    exploratory_action_considered: str | None = None
    """If a growth/experiment leap was visible in ranked decisions."""
    exploratory_action_rejected_reason: str | None = None
    """Why automatic proceed was not chosen (policy, confidence, conflict)."""


# Schema version written alongside JSON payloads
PACKET_SCHEMA = "argus.escalation_packet.v1"
