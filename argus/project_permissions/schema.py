"""Phase 1 project permission keys and allowed values (provider-agnostic)."""

from __future__ import annotations

from typing import Literal

PROJECT_PERMISSION_POLICY_SCHEMA = "argus.project_permission_policy.v1"

Phase1PermissionKey = Literal[
    "observe_prod_signals",
    "mutate_nonprod",
    "mutate_prod",
    "commit_local",
    "push_remote",
    "deploy",
    "change_experiments",
    "builder_execute",
]

PHASE1_KEYS: tuple[str, ...] = (
    "observe_prod_signals",
    "mutate_nonprod",
    "mutate_prod",
    "commit_local",
    "push_remote",
    "deploy",
    "change_experiments",
    "builder_execute",
)

PermissionValue = Literal["yes", "no", "confirm"]

VALID_PERMISSION_VALUES: frozenset[str] = frozenset({"yes", "no", "confirm"})
