"""Autonomy mode and policy models (central safety configuration)."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class AutonomyMode(StrEnum):
    """How much Argus may do without explicit human steps."""

    OFF = "off"
    MANUAL = "manual"
    SUPERVISED = "supervised"
    LIMITED = "limited"
    ACTIVE = "active"


@dataclass
class AutonomyPolicy:
    """
    Machine-enforced limits for automated and CLI-triggered execution.

    ``max_actions_per_run`` is enforced against a per–UTC-day counter (and optional
    ``ARGUS_AUTONOMY_RUN_ID`` session) — the canonical safety budget for executions.
    """

    max_actions_per_run: int
    max_cost_per_day: float
    allowed_action_types: tuple[str, ...] = ()
    forbidden_action_types: tuple[str, ...] = ()
    require_approval_for: tuple[str, ...] = ()
    auto_execute_types: tuple[str, ...] = ()
    escalation_thresholds: dict[str, float] = field(default_factory=dict)
    #: Daily caps for product scaffold spawns and experiment file creation (UTC day, see ``argus.autonomy.quotas``).
    max_product_spawns_per_utc_day: int = 999
    max_experiments_per_utc_day: int = 999
    #: Daily cap for shutdown ``--apply`` operations (UTC day; see ``argus.autonomy.quotas``).
    max_shutdowns_per_utc_day: int = 999
    #: Minimum decision-assessment confidence (0–1) to allow autonomous execution when enabled; ``0`` disables.
    min_confidence_autonomous: float = 0.0

    def to_jsonable(self) -> dict[str, Any]:
        return {
            "max_actions_per_run": self.max_actions_per_run,
            "max_cost_per_day": self.max_cost_per_day,
            "allowed_action_types": list(self.allowed_action_types),
            "forbidden_action_types": list(self.forbidden_action_types),
            "require_approval_for": list(self.require_approval_for),
            "auto_execute_types": list(self.auto_execute_types),
            "escalation_thresholds": dict(self.escalation_thresholds),
            "max_product_spawns_per_utc_day": self.max_product_spawns_per_utc_day,
            "max_experiments_per_utc_day": self.max_experiments_per_utc_day,
            "max_shutdowns_per_utc_day": self.max_shutdowns_per_utc_day,
            "min_confidence_autonomous": self.min_confidence_autonomous,
        }

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> AutonomyPolicy:
        def _tup(key: str) -> tuple[str, ...]:
            v = data.get(key)
            if not v:
                return ()
            if not isinstance(v, list):
                raise ValueError(f"{key} must be a list of strings")
            return tuple(str(x).strip().lower() for x in v if str(x).strip())

        esc = data.get("escalation_thresholds") or {}
        if not isinstance(esc, dict):
            raise ValueError("escalation_thresholds must be a mapping")
        esc_out: dict[str, float] = {}
        for k, v in esc.items():
            esc_out[str(k)] = float(v)

        return cls(
            max_actions_per_run=int(data["max_actions_per_run"]),
            max_cost_per_day=float(data["max_cost_per_day"]),
            allowed_action_types=_tup("allowed_action_types"),
            forbidden_action_types=_tup("forbidden_action_types"),
            require_approval_for=_tup("require_approval_for"),
            auto_execute_types=_tup("auto_execute_types"),
            escalation_thresholds=esc_out,
            max_product_spawns_per_utc_day=int(data.get("max_product_spawns_per_utc_day", 999)),
            max_experiments_per_utc_day=int(data.get("max_experiments_per_utc_day", 999)),
            max_shutdowns_per_utc_day=int(data.get("max_shutdowns_per_utc_day", 999)),
            min_confidence_autonomous=float(data.get("min_confidence_autonomous", 0.0) or 0.0),
        )


@dataclass
class AutonomyCheckResult:
    """Outcome of an autonomy gate check (before subprocess execution)."""

    allowed: bool
    reasons: list[str] = field(default_factory=list)
    escalate_recommended: bool = False
