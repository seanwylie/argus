"""Structural validation for domain models (no business rules)."""

from __future__ import annotations

from datetime import datetime

from argus.core.models.canonical_signal import CanonicalSignal
from argus.core.models.decision import ActionProposal, DecisionCandidate
from argus.core.models.enums import (
    ActionType,
    EffortBucket,
    FindingKind,
    LifecycleStage,
    RunResult,
    RunStage,
    SeverityLevel,
    SignalType,
)
from argus.core.models.finding import Finding
from argus.core.models.product import ProductLifecycle, ProductNode
from argus.core.models.run import RunRecord
from argus.core.models.signal import SignalRecord


class ArgusValidationError(ValueError):
    """Raised when a domain object fails structural validation."""


def _non_empty_str(name: str, value: str | None) -> None:
    if value is None or not str(value).strip():
        raise ArgusValidationError(f"{name} must be a non-empty string")


def _confidence(name: str, value: float | None) -> None:
    if value is None:
        return
    if not 0.0 <= value <= 1.0:
        raise ArgusValidationError(f"{name} must be between 0.0 and 1.0 inclusive")


def _priority_score(value: float | None) -> None:
    if value is None:
        return
    # Open-ended score; only reject NaN/inf if float misbehaves
    if value != value:  # NaN
        raise ArgusValidationError("priority_score must not be NaN")


def validate_product_lifecycle(obj: ProductLifecycle) -> None:
    if not isinstance(obj.stage, LifecycleStage):
        raise ArgusValidationError("lifecycle.stage must be a LifecycleStage")
    if obj.next_gate is not None and not str(obj.next_gate).strip():
        raise ArgusValidationError("lifecycle.next_gate must be non-empty when set")


def validate_product_node(obj: ProductNode) -> None:
    _non_empty_str("id", obj.id)
    _non_empty_str("name", obj.name)
    _non_empty_str("product_root", obj.product_root)
    _non_empty_str("config_path", obj.config_path)
    if obj.owner.team is None or not str(obj.owner.team).strip():
        raise ArgusValidationError("owner.team must be set")
    validate_product_lifecycle(obj.lifecycle)
    for sd in obj.signals:
        if not str(sd.type).strip():
            raise ArgusValidationError("each signal definition needs a non-empty type")


def validate_signal_record(obj: SignalRecord) -> None:
    _non_empty_str("id", obj.id)
    _non_empty_str("product_id", obj.product_id)
    _non_empty_str("source", obj.source)
    if not isinstance(obj.signal_type, SignalType):
        raise ArgusValidationError("signal_type must be a SignalType")
    if obj.severity_hint is not None and not isinstance(obj.severity_hint, SeverityLevel):
        raise ArgusValidationError("severity_hint must be a SeverityLevel or None")
    if not isinstance(obj.observed_at, datetime):
        raise ArgusValidationError("observed_at must be a datetime")
    _confidence("confidence", obj.confidence)
    if obj.canonical is not None:
        _validate_canonical_signal(obj.canonical)


def _validate_canonical_signal(c: CanonicalSignal) -> None:
    if not isinstance(c, CanonicalSignal):
        raise ArgusValidationError("canonical must be a CanonicalSignal or None")
    _non_empty_str("canonical.signal_id", c.signal_id)
    _non_empty_str("canonical.product_id", c.product_id)
    _non_empty_str("canonical.category", c.category)
    _non_empty_str("canonical.value_type", c.value_type)
    _non_empty_str("canonical.source_type", c.source_type)
    _non_empty_str("canonical.source_ref", c.source_ref)
    _non_empty_str("canonical.observed_at", c.observed_at)
    _non_empty_str("canonical.collected_at", c.collected_at)
    _non_empty_str("canonical.freshness_status", c.freshness_status)
    _non_empty_str("canonical.trust_level", c.trust_level)
    _non_empty_str("canonical.collection_status", c.collection_status)


def validate_finding(obj: Finding) -> None:
    _non_empty_str("id", obj.id)
    _non_empty_str("product_id", obj.product_id)
    _non_empty_str("title", obj.title)
    _non_empty_str("summary", obj.summary)
    _non_empty_str("recommendation", obj.recommendation)
    if not isinstance(obj.kind, FindingKind):
        raise ArgusValidationError("kind must be a FindingKind")
    if not isinstance(obj.severity, SeverityLevel):
        raise ArgusValidationError("severity must be a SeverityLevel")
    if not isinstance(obj.effort, EffortBucket):
        raise ArgusValidationError("effort must be an EffortBucket")
    _confidence("confidence", obj.confidence)
    if obj.created_at is not None and not isinstance(obj.created_at, datetime):
        raise ArgusValidationError("created_at must be a datetime or None")


def validate_decision_candidate(obj: DecisionCandidate) -> None:
    _non_empty_str("id", obj.id)
    _non_empty_str("product_id", obj.product_id)
    _non_empty_str("summary", obj.summary)
    if not isinstance(obj.action_type, ActionType):
        raise ArgusValidationError("action_type must be an ActionType")
    _confidence("confidence", obj.confidence)
    _priority_score(obj.priority_score)


def validate_action_proposal(obj: ActionProposal) -> None:
    _non_empty_str("id", obj.id)
    _non_empty_str("product_id", obj.product_id)
    _non_empty_str("command", obj.command)
    _non_empty_str("reason", obj.reason)
    if not isinstance(obj.action_type, ActionType):
        raise ArgusValidationError("action_type must be an ActionType")
    if obj.selected_at is not None and not isinstance(obj.selected_at, datetime):
        raise ArgusValidationError("selected_at must be a datetime or None")


def validate_run_record(obj: RunRecord) -> None:
    _non_empty_str("run_id", obj.run_id)
    if not isinstance(obj.started_at, datetime):
        raise ArgusValidationError("started_at must be a datetime")
    if obj.finished_at is not None and not isinstance(obj.finished_at, datetime):
        raise ArgusValidationError("finished_at must be a datetime or None")
    if not isinstance(obj.stage, RunStage):
        raise ArgusValidationError("stage must be a RunStage")
    if not isinstance(obj.result, RunResult):
        raise ArgusValidationError("result must be a RunResult")
    for pid in obj.product_ids:
        _non_empty_str("product_ids[]", pid)
    for f in obj.findings:
        validate_finding(f)
    for a in obj.selected_actions:
        validate_action_proposal(a)
