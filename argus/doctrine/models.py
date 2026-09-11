"""Parsed product doctrine (``products/<id>/doctrine.yaml``)."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class DoctrineConstraints:
    """Hard checks that may emit :class:`FindingKind.DOCTRINE_VIOLATION` findings."""

    max_monthly_cost_usd: float | None = None
    require_human_review_when_kill_candidate: bool = False


@dataclass(frozen=True)
class DoctrineScoring:
    """Nudges applied to decision and experiment scores (deterministic)."""

    intent_priority_multiplier: dict[str, float] = field(default_factory=dict)
    experiment_score_boost: float = 0.0


@dataclass(frozen=True)
class ProductDoctrine:
    """Validated machine-readable doctrine for one product."""

    schema_id: str
    summary: str | None
    principles: tuple[str, ...]
    constraints: DoctrineConstraints
    scoring: DoctrineScoring
