"""Stub angle payloads — shape without depth."""

from __future__ import annotations

import hashlib
from typing import Any

from argus.audit.agent_lens import PROVENANCE_DETERMINISTIC_STUB

# Angle ids that use `stub_angle_payload` when listed here; empty when all nine bundle angles have deterministic runners.
STUB_ANGLE_IDS: tuple[str, ...] = ()


def stub_fingerprint(angle_id: str) -> str:
    return hashlib.sha256(f"stub:{angle_id}".encode()).hexdigest()[:20]


def stub_angle_payload(angle_id: str) -> dict[str, Any]:
    """Minimum contract: angle_status stub, >=1 summary line, deterministic fingerprint."""
    return {
        "schema": f"argus.audit_angle.{angle_id}.v1",
        "angle_status": "stub",
        "summary_lines": [f"{angle_id}: not implemented (stub)"],
        "notes": [],
        "findings": [],
        "provenance": PROVENANCE_DETERMINISTIC_STUB,
    }
