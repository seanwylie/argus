"""Action contracts: formal commands Argus may run (validation, dry-run, gated execute)."""

from __future__ import annotations

from typing import Any

from argus.actions.models import ActionContract, DryRunResult, ExecuteResult, FileCheckResult
from argus.actions.validate import load_action_file, validate_action_contract

__all__ = [
    "ActionContract",
    "DryRunResult",
    "ExecuteResult",
    "FileCheckResult",
    "dry_run",
    "execute_action",
    "load_action_file",
    "validate_action_contract",
]


def __getattr__(name: str) -> Any:
    # Lazy: avoid circular import (executor → gate → approval.rules while rules imports actions.models).
    if name == "dry_run":
        from argus.actions.executor import dry_run as fn

        return fn
    if name == "execute_action":
        from argus.actions.executor import execute_action as fn

        return fn
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
