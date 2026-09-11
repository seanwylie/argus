"""Lifecycle readiness assessment (deterministic scores)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from argus.core.models.enums import LifecycleStage


@dataclass(frozen=True)
class LifecycleAssessment:
    """
    Five readiness dimensions in ``[0, 1]``, computed from stage priors + findings.

    Dimensions:
    - **move_forward**: proceed to next gate / widen investment
    - **hold**: wait without major change
    - **improve**: fix quality, cost posture, or data before advancing
    - **deprecate**: sunsetting path is appropriate
    - **kill**: terminal wind-down (stronger than deprecate in this model)

    ``kill_candidate`` is a derived flag (see docs): high kill score with low forward momentum.
    """

    product_id: str
    stage: LifecycleStage
    move_forward: float
    hold: float
    improve: float
    deprecate: float
    kill: float
    reasoning: dict[str, str] = field(default_factory=dict)
    kill_candidate: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, float]:
        return {
            "move_forward": self.move_forward,
            "hold": self.hold,
            "improve": self.improve,
            "deprecate": self.deprecate,
            "kill": self.kill,
        }
