"""Enumerations and small constants for the Argus domain model."""

from __future__ import annotations

from enum import StrEnum


class LifecycleStage(StrEnum):
    """High-level lifecycle stages for a product node."""

    IDEA = "idea"
    BUILD = "build"
    VALIDATE = "validate"
    GROW = "grow"
    MAINTAIN = "maintain"
    DECLINE = "decline"
    KILL = "kill"


class SignalType(StrEnum):
    """Kinds of signal sources Argus may normalize."""

    FILESYSTEM = "filesystem"
    ANALYTICS = "analytics"
    COST = "cost"
    LOGS = "logs"
    METRICS = "metrics"
    HEALTH = "health"
    EXECUTION = "execution"
    CUSTOM = "custom"
    #: Time-aware snapshots (market, news, recency); payload carries source vs fetch times.
    TEMPORAL = "temporal"


class FindingKind(StrEnum):
    """Classification for audit findings."""

    QUALITY_ISSUE = "quality_issue"
    GROWTH_OPPORTUNITY = "growth_opportunity"
    COST_RISK = "cost_risk"
    RETENTION_PROBLEM = "retention_problem"
    INACTIVITY = "inactivity"
    RELIABILITY_PROBLEM = "reliability_problem"
    LAUNCH_CANDIDATE = "launch_candidate"
    #: Early lifecycle: repo/layout/metrics exist and are inspectable, but not launch-ready.
    STRUCTURAL_READINESS = "structural_readiness"
    #: Minimum non-bootstrap evidence depth for validation-oriented reasoning (see readiness rules).
    VALIDATION_READINESS = "validation_readiness"
    #: lifecycle.stage says validate but the validation evidence contract is not met.
    VALIDATION_EVIDENCE_GAP = "validation_evidence_gap"
    DEPRECATION_CANDIDATE = "deprecation_candidate"
    DOCTRINE_VIOLATION = "doctrine_violation"
    # Temporal / change-over-time (see ``argus.findings.rules.temporal``)
    CURRENT_OPPORTUNITY = "current_opportunity"
    CURRENT_RISK = "current_risk"
    STALE_CONTEXT = "stale_context"
    TRENDING_TOPIC = "trending_topic"
    URGENCY_WINDOW = "urgency_window"
    NO_RECENT_EVIDENCE = "no_recent_evidence"


class ActionType(StrEnum):
    """Verb-style actions Argus may propose or execute."""

    START = "start"
    STOP = "stop"
    ANALYZE = "analyze"
    INVESTIGATE = "investigate"
    GENERATE = "generate"
    SCALE = "scale"
    PAUSE = "pause"
    DEPRECATE = "deprecate"
    ARCHIVE = "archive"
    CUSTOM = "custom"


class SeverityLevel(StrEnum):
    """Relative severity for findings and signal hints."""

    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class EffortBucket(StrEnum):
    """Rough effort sizing for findings and decisions."""

    TRIVIAL = "trivial"
    SMALL = "small"
    MEDIUM = "medium"
    LARGE = "large"
    XLARGE = "xlarge"


class RunStage(StrEnum):
    """Where a run is in its own lifecycle (orchestration hook, not product lifecycle)."""

    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLED = "cancelled"


class RunResult(StrEnum):
    """Outcome classification for a completed run."""

    SUCCESS = "success"
    PARTIAL = "partial"
    FAILURE = "failure"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"
