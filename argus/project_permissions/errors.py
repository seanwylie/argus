"""Errors for Phase 1 project permission policy loading."""

from __future__ import annotations

from pathlib import Path


class ProjectPermissionPolicyError(Exception):
    """
    ``argus.policy.yaml`` is present but invalid (strict validation).

    Values must be literal strings ``"yes"``, ``"no"``, or ``"confirm"`` in YAML —
    never unquoted ``yes``/``no`` (YAML 1.1 booleans) or JSON-style booleans.
    """

    def __init__(
        self,
        message: str,
        *,
        policy_path: Path | None = None,
        errors: list[str] | None = None,
    ) -> None:
        super().__init__(message)
        self.policy_path = policy_path
        self.errors: list[str] = list(errors) if errors else [message]


__all__ = ["ProjectPermissionPolicyError"]
