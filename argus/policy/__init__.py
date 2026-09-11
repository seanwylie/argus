"""Operator and system policy surfaces (tunable defaults + config overrides)."""

from __future__ import annotations

from typing import Any

from argus.policy.operator_policy import (
    OPERATOR_POLICY_SCHEMA,
    clear_operator_policy_cache,
    default_operator_policy,
    load_operator_policy,
    write_operator_policy_effective_artifact,
    write_operator_policy_effective_markdown,
)

__all__ = [
    "OPERATOR_POLICY_SCHEMA",
    "clear_operator_policy_cache",
    "default_operator_policy",
    "evaluate_policy_experiment",
    "load_operator_policy",
    "run_policy_experiment",
    "write_operator_policy_effective_artifact",
    "write_operator_policy_effective_markdown",
]


def __getattr__(name: str) -> Any:
    """Lazy exports for policy experiment helpers (avoids import cycles with portfolio/orchestrator)."""
    if name == "evaluate_policy_experiment":
        from argus.policy.experiment import evaluate_policy_experiment as fn

        return fn
    if name == "run_policy_experiment":
        from argus.policy.experiment import run_policy_experiment as fn

        return fn
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
