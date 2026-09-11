"""Canonical Argus domain models (dataclasses)."""

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
from argus.core.models.product import (
    ActionsMap,
    ConstraintsDefinition,
    CostDefinition,
    MetricsDefinition,
    OwnerInfo,
    ProductLifecycle,
    ProductNode,
    ProductTypeInfo,
    SignalDefinition,
)
from argus.core.models.run import RunRecord
from argus.core.models.signal import SignalRecord
from argus.core.models.validation import (
    ArgusValidationError,
    validate_action_proposal,
    validate_decision_candidate,
    validate_finding,
    validate_product_lifecycle,
    validate_product_node,
    validate_run_record,
    validate_signal_record,
)

__all__ = [
    "ActionProposal",
    "ActionType",
    "ActionsMap",
    "ConstraintsDefinition",
    "CostDefinition",
    "DecisionCandidate",
    "EffortBucket",
    "Finding",
    "FindingKind",
    "LifecycleStage",
    "MetricsDefinition",
    "OwnerInfo",
    "ProductLifecycle",
    "ProductNode",
    "ProductTypeInfo",
    "RunRecord",
    "RunResult",
    "RunStage",
    "SeverityLevel",
    "SignalDefinition",
    "SignalRecord",
    "SignalType",
    "ArgusValidationError",
    "validate_action_proposal",
    "validate_decision_candidate",
    "validate_finding",
    "validate_product_lifecycle",
    "validate_product_node",
    "validate_run_record",
    "validate_signal_record",
]
