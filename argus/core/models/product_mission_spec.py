"""Structured product mission (``product.yaml``) — kept in core to avoid import cycles with ``argus.mission``."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

PRODUCT_MISSION_SPEC_SCHEMA = "argus.product_mission_spec.v1"


@dataclass(frozen=True)
class ProductMissionSpec:
    """
    Composable mission for one product.

    ``objective`` is the primary registry profile id. ``drivers`` / ``guardrails`` reference additional
    profiles layered by :mod:`argus.policy.mission_mapping`.
    """

    objective: str
    drivers: tuple[str, ...] = ()
    guardrails: tuple[str, ...] = ()
    risk_posture: str | None = None

    def to_jsonable(self) -> dict[str, Any]:
        return {
            "schema": PRODUCT_MISSION_SPEC_SCHEMA,
            "objective": self.objective,
            "drivers": list(self.drivers),
            "guardrails": list(self.guardrails),
            "risk_posture": self.risk_posture,
        }
