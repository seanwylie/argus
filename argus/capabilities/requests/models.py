"""Capability request model — Argus asking humans for external abilities."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class CapabilityRequestStatus(StrEnum):
    """Lifecycle for a capability request."""

    OPEN = "open"
    ACKNOWLEDGED = "acknowledged"
    FULFILLED = "fulfilled"
    REJECTED = "rejected"
    CANCELLED = "cancelled"


class CapabilityRequestSource(StrEnum):
    """Where the request originated."""

    EXECUTION = "execution"
    EXPERIMENT = "experiment"
    ADVISOR = "advisor"
    AUTONOMY = "autonomy"
    MANUAL = "manual"


_TERMINAL = frozenset(
    {
        CapabilityRequestStatus.FULFILLED,
        CapabilityRequestStatus.REJECTED,
        CapabilityRequestStatus.CANCELLED,
    },
)


def is_terminal_status(status: CapabilityRequestStatus) -> bool:
    return status in _TERMINAL


@dataclass
class CapabilityRequest:
    """
    A structured ask for a human to provide or enable a capability Argus lacks.

    Persisted as JSON under ``runs/capabilities/requests/``.
    """

    request_id: str
    title: str
    description: str
    source: CapabilityRequestSource
    status: CapabilityRequestStatus = CapabilityRequestStatus.OPEN
    capability_hint: str = ""
    """Optional id aligned with registry / gap vocabulary (e.g. ``advisors.llm``)."""
    product_id: str | None = None
    source_ref: dict[str, Any] = field(default_factory=dict)
    """Structured linkage: ``action_id``, ``experiment_id``, ``action_path``, etc."""
    created_at: str = ""
    updated_at: str = ""
    resolution_note: str = ""
    schema: str = "argus.capability_request.v1"
    metadata: dict[str, Any] = field(default_factory=dict)
