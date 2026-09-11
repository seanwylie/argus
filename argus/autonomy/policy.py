"""Compatibility re-export for autonomous safety checks (implementation in ``safe_execution``)."""

from argus.autonomy.safe_execution import (
    ENV_AUTONOMOUS_SAFE_EXECUTION,
    AutonomyEvaluation,
    evaluate_safe_autonomy,
    is_autonomous_execution_enabled,
)

__all__ = [
    "ENV_AUTONOMOUS_SAFE_EXECUTION",
    "AutonomyEvaluation",
    "evaluate_safe_autonomy",
    "is_autonomous_execution_enabled",
]
