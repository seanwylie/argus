"""Product node and lifecycle models."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

from argus.core.models.enums import LifecycleStage
from argus.core.models.product_mission_spec import ProductMissionSpec
from argus.core.models.signal_manifest import ProductSignalManifest

# Directed edges: from_stage -> allowed next stages (including terminal KILL from most states).
_LIFECYCLE_EDGES: dict[LifecycleStage, frozenset[LifecycleStage]] = {
    LifecycleStage.IDEA: frozenset(
        {LifecycleStage.BUILD, LifecycleStage.KILL}
    ),
    LifecycleStage.BUILD: frozenset(
        {LifecycleStage.VALIDATE, LifecycleStage.IDEA, LifecycleStage.KILL}
    ),
    LifecycleStage.VALIDATE: frozenset(
        {LifecycleStage.GROW, LifecycleStage.BUILD, LifecycleStage.KILL}
    ),
    LifecycleStage.GROW: frozenset(
        {LifecycleStage.MAINTAIN, LifecycleStage.DECLINE, LifecycleStage.KILL}
    ),
    LifecycleStage.MAINTAIN: frozenset(
        {LifecycleStage.GROW, LifecycleStage.DECLINE, LifecycleStage.KILL}
    ),
    LifecycleStage.DECLINE: frozenset(
        {LifecycleStage.MAINTAIN, LifecycleStage.KILL}
    ),
    LifecycleStage.KILL: frozenset(),
}


@dataclass
class OwnerInfo:
    """Owning team and operator for accountability."""

    team: str
    operator: str = ""


@dataclass
class MetricsDefinition:
    """Where and what metrics this product exposes."""

    local_paths: list[str] = field(default_factory=list)
    primary: list[str] = field(default_factory=list)


@dataclass
class CostDefinition:
    """Declared or estimated cost envelope."""

    monthly_usd: float | None = None
    notes: str | None = None


@dataclass
class SignalDefinition:
    """Per-product signal wiring from product.yaml."""

    type: str
    enabled: bool = True


@dataclass
class ActionsMap:
    """Named shell commands or scripts keyed by logical action name."""

    start: str | None = None
    stop: str | None = None
    analyze: str | None = None
    extra: dict[str, str] = field(default_factory=dict)


@dataclass
class ConstraintsDefinition:
    """Budget and activity guardrails."""

    max_monthly_cost_usd: float | None = None
    min_activity_threshold: float | None = None


@dataclass
class ProductTypeInfo:
    """Loose typing for product category (not enforced as enum yet)."""

    type: str
    status: str = "unknown"
    state: str | None = None


@dataclass
class ProductLifecycle:
    """
    Lifecycle metadata for a product.

    ``stage`` uses :class:`LifecycleStage`. ``next_gate`` is a human gate
    description (from product.yaml or policy).
    """

    stage: LifecycleStage
    next_gate: str | None = None

    def allowed_transitions(self) -> frozenset[LifecycleStage]:
        """Stages that may follow the current stage."""
        return _LIFECYCLE_EDGES.get(self.stage, frozenset())

    def can_transition_to(self, target: LifecycleStage) -> bool:
        """Whether ``target`` is an allowed immediate next stage."""
        return target in self.allowed_transitions()

    @staticmethod
    def valid_transitions_from(stage: LifecycleStage) -> frozenset[LifecycleStage]:
        """Allowed next stages from ``stage`` (pure helper)."""
        return _LIFECYCLE_EDGES.get(stage, frozenset())


@dataclass
class ProductNode:
    """
    Canonical in-memory view of a product after loading ``product.yaml``
    (and optional enrichment).

    ``product_root`` is the absolute or repo-relative directory containing the
    product. ``config_path`` points at the loaded ``product.yaml``.
    """

    id: str
    name: str
    owner: OwnerInfo
    metrics: MetricsDefinition
    cost: CostDefinition
    signals: list[SignalDefinition]
    actions: ActionsMap
    constraints: ConstraintsDefinition
    lifecycle: ProductLifecycle
    product_root: str
    config_path: str
    type_info: ProductTypeInfo | None = None
    tags: list[str] = field(default_factory=list)
    raw_extensions: Mapping[str, object] = field(default_factory=dict)
    #: Optional product-owned signal declarations (``signals.yaml`` or ``signal_manifest``).
    signal_manifest: ProductSignalManifest | None = None
    #: Primary mission profile id (``objective``); set for legacy ``mission_id`` or structured ``mission``.
    mission_id: str | None = None
    #: Structured composable mission when ``product.yaml`` uses ``mission:`` (else None for legacy-only).
    mission: ProductMissionSpec | None = None
