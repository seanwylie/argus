"""
Strict parsing of Phase 1 permission values from YAML.

YAML 1.1 (PyYAML) interprets unquoted ``yes`` / ``no`` / ``on`` / ``off`` as booleans.
Argus requires **string** values only: ``"yes"``, ``"no"``, ``"confirm"``.
"""

from __future__ import annotations

from typing import Any

from argus.project_permissions.schema import VALID_PERMISSION_VALUES


def format_accepted_values() -> str:
    return ', '.join(f'"{v}"' for v in sorted(VALID_PERMISSION_VALUES))


def parse_strict_permission_value(field: str, raw: Any) -> str:
    """
    Return canonical ``yes`` | ``no`` | ``confirm``.

    Raises ``ValueError`` with an operator-actionable message (no silent coercion).
    """
    if raw is None:
        raise ValueError(
            f"{field}: value is null. Omit the key to use the built-in default, or set one of "
            f"{format_accepted_values()} as a quoted string.",
        )

    if isinstance(raw, bool):
        hint = (
            "YAML interpreted this as a boolean (common when `yes` or `no` is written without quotes). "
            "Use quoted strings only."
        )
        raise ValueError(f"{field}: invalid value (boolean: {raw!r}). {hint} Accepted: {format_accepted_values()}.")

    if not isinstance(raw, str):
        raise ValueError(
            f"{field}: expected a YAML string, got {type(raw).__name__} ({raw!r}). "
            f"Use one of {format_accepted_values()} as quoted strings.",
        )

    s = raw.strip().lower()
    if not s:
        raise ValueError(
            f"{field}: empty string. Use one of {format_accepted_values()} as quoted strings.",
        )

    if s not in VALID_PERMISSION_VALUES:
        raise ValueError(
            f'{field}: invalid value {raw!r} (normalized: {s!r}). '
            f"Use exactly one of {format_accepted_values()} — no synonyms such as allow/deny.",
        )

    return s


__all__ = ["parse_strict_permission_value", "format_accepted_values"]
