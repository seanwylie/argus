"""Audit MVP domain models — evidence-backed capability status per product."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any


class AuditStatus(StrEnum):
    """Capability implementation status; ``unknown`` is never interchangeable with ``missing``."""

    IMPLEMENTED = "implemented"
    PARTIAL = "partial"
    MISSING = "missing"
    UNKNOWN = "unknown"


class AuditEvidenceKind(StrEnum):
    FILE_PATH = "file_path"
    PATTERN_MATCH = "pattern_match"
    CONFIG_REF = "config_ref"
    SCRIPT_REF = "script_ref"
    DOC_REF = "doc_ref"


@dataclass
class AuditEvidence:
    kind: AuditEvidenceKind
    ref: str
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind.value, "ref": self.ref, "detail": self.detail}

    @staticmethod
    def from_dict(raw: dict[str, Any]) -> AuditEvidence:
        return AuditEvidence(
            kind=AuditEvidenceKind(str(raw.get("kind", "file_path"))),
            ref=str(raw.get("ref", "")),
            detail=str(raw.get("detail", "")),
        )


@dataclass
class AuditCapabilityEntry:
    capability_id: str
    product_id: str
    description: str
    status: AuditStatus
    confidence: str  # high | medium | low
    evidence: list[AuditEvidence] = field(default_factory=list)
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "product_id": self.product_id,
            "description": self.description,
            "status": self.status.value,
            "confidence": self.confidence,
            "evidence": [e.to_dict() for e in self.evidence],
            "notes": self.notes,
        }

    @staticmethod
    def from_dict(raw: dict[str, Any]) -> AuditCapabilityEntry:
        ev = raw.get("evidence") or []
        evidence = [AuditEvidence.from_dict(x) for x in ev if isinstance(x, dict)]
        return AuditCapabilityEntry(
            capability_id=str(raw.get("capability_id", "")),
            product_id=str(raw.get("product_id", "")),
            description=str(raw.get("description", "")),
            status=AuditStatus(str(raw.get("status", "unknown"))),
            confidence=str(raw.get("confidence", "medium")),
            evidence=evidence,
            notes=str(raw.get("notes", "")),
        )


@dataclass
class AuditSummary:
    """Persisted artifact for one product audit run."""

    product_id: str
    generated_at_utc: str
    scan_depth: str  # "quick" for MVP
    scanned_paths: list[str]
    excluded_paths: list[str]
    capabilities: list[AuditCapabilityEntry]
    counts_by_status: dict[str, int]
    notes: list[str] = field(default_factory=list)
    inputs_fingerprint: str = ""
    schema: str = "argus.audit_summary.v1"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "product_id": self.product_id,
            "generated_at_utc": self.generated_at_utc,
            "scan_depth": self.scan_depth,
            "scanned_paths": self.scanned_paths,
            "excluded_paths": self.excluded_paths,
            "capabilities": [c.to_dict() for c in self.capabilities],
            "counts_by_status": dict(self.counts_by_status),
            "notes": list(self.notes),
            "inputs_fingerprint": self.inputs_fingerprint,
        }

    @staticmethod
    def from_dict(raw: dict[str, Any]) -> AuditSummary | None:
        if not isinstance(raw, dict):
            return None
        if str(raw.get("schema", "")) not in ("argus.audit_summary.v1",):
            return None
        caps_raw = raw.get("capabilities") or []
        caps = [AuditCapabilityEntry.from_dict(x) for x in caps_raw if isinstance(x, dict)]
        return AuditSummary(
            product_id=str(raw.get("product_id", "")),
            generated_at_utc=str(raw.get("generated_at_utc", "")),
            scan_depth=str(raw.get("scan_depth", "quick")),
            scanned_paths=[str(x) for x in (raw.get("scanned_paths") or [])],
            excluded_paths=[str(x) for x in (raw.get("excluded_paths") or [])],
            capabilities=caps,
            counts_by_status={str(k): int(v) for k, v in (raw.get("counts_by_status") or {}).items()},
            notes=[str(x) for x in (raw.get("notes") or [])],
            inputs_fingerprint=str(raw.get("inputs_fingerprint", "")),
            schema="argus.audit_summary.v1",
        )


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()
