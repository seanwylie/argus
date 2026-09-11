"""Autonomy: operator safety limits + heuristics for unapproved execution."""

from __future__ import annotations

from argus.autonomy.models import AutonomyCheckResult, AutonomyMode, AutonomyPolicy
from argus.autonomy.operator_policy import (
    autonomy_config_path,
    default_policy_for_mode,
    effective_policy,
    load_autonomy_config,
    save_autonomy_config,
)
from argus.autonomy.safe_execution import (
    ENV_AUTONOMOUS_SAFE_EXECUTION,
    AutonomyEvaluation,
    evaluate_safe_autonomy,
    is_autonomous_execution_enabled,
)

__all__ = [
    "ENV_AUTONOMOUS_SAFE_EXECUTION",
    "AutonomyCheckResult",
    "AutonomyEvaluation",
    "AutonomyMode",
    "AutonomyPolicy",
    "autonomy_config_path",
    "default_policy_for_mode",
    "effective_policy",
    "evaluate_safe_autonomy",
    "is_autonomous_execution_enabled",
    "load_autonomy_config",
    "save_autonomy_config",
]
