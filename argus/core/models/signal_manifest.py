"""Product-owned signal manifest (declarations only; collection stays in Argus)."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from argus.core.models.enums import SignalType

PRODUCT_SIGNAL_MANIFEST_SCHEMA = "argus.product_signal_manifest.v1"


class SignalManifestCategory(StrEnum):
    """What the signal is about (product taxonomy)."""

    OPERATIONAL = "operational"
    BUSINESS = "business"
    TEMPORAL = "temporal"
    QUALITY = "quality"
    COST = "cost"
    COMPLIANCE = "compliance"
    EXPERIENCE = "experience"
    CUSTOM = "custom"


class ManifestValueType(StrEnum):
    """Declared shape of observed values."""

    GAUGE = "gauge"
    COUNTER = "counter"
    BOOLEAN = "boolean"
    STRING = "string"
    JSON = "json"
    DURATION = "duration"
    BLOB = "blob"


class ManifestRequiredFor(StrEnum):
    """Where this signal is required for Argus behavior (policy hint)."""

    PIPELINE = "pipeline"
    AUDIT = "audit"
    FINDINGS = "findings"
    PORTFOLIO = "portfolio"
    GOVERNANCE = "governance"
    NONE = "none"


class ManifestTrustLevel(StrEnum):
    """How much weight to give observations of this signal."""

    AUTHORITATIVE = "authoritative"
    DERIVED = "derived"
    HEURISTIC = "heuristic"
    UNKNOWN = "unknown"


class SourceWindowKind(StrEnum):
    """Optional semantics for time windows (metadata for collectors/temporal)."""

    SLIDING = "sliding"
    FIXED = "fixed"
    POINT_IN_TIME = "point_in_time"
    CUMULATIVE = "cumulative"


@dataclass(frozen=True)
class ProductSignalManifestEntry:
    """
    One declared product signal.

    ``source_ref`` and ``path`` are both optional in YAML, but at least one must be
    non-empty after stripping (logical ref vs repo-relative path).
    """

    id: str
    category: SignalManifestCategory
    source_type: SignalType
    freshness_sla: str
    value_type: ManifestValueType
    required_for: ManifestRequiredFor
    trust_level: ManifestTrustLevel
    source_ref: str | None = None
    path: str | None = None
    description: str | None = None
    unit: str | None = None
    source_window_kind: SourceWindowKind | None = None
    owner: str | None = None
    enabled: bool = True


@dataclass
class ProductSignalManifest:
    """Validated bundle attached to :class:`ProductNode`."""

    schema: str = PRODUCT_SIGNAL_MANIFEST_SCHEMA
    signals: list[ProductSignalManifestEntry] = field(default_factory=list)
