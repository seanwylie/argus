"""Load ``products/<id>/argus.policy.yaml``; reload on each call (no cache)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from argus.project_permissions.defaults import DEFAULT_PHASE1, merged_defaults
from argus.project_permissions.errors import ProjectPermissionPolicyError
from argus.project_permissions.model import ProjectPermissionPolicy
from argus.project_permissions.schema import PHASE1_KEYS, VALID_PERMISSION_VALUES
from argus.project_permissions.value_parse import (
    format_accepted_values,
    parse_strict_permission_value,
)


def policy_file_path(repo_root: Path, product_id: str, *, products_dir: Path | None = None) -> Path:
    root = repo_root.resolve()
    pdir = (root / "products") if products_dir is None else products_dir.resolve()
    return pdir / product_id.strip() / "argus.policy.yaml"


def default_policy_yaml_text() -> str:
    """
    Stable default file body for scaffold / onboarding.

    **Quoting:** Every value is written as a quoted string so PyYAML reloads strings, not YAML 1.1
    booleans (unquoted ``yes`` / ``no`` are *not* accepted by Argus — use ``\"yes\"``, etc.).
    """
    lines = ["schema: argus.project_permission_policy.v1"]
    for k in PHASE1_KEYS:
        v = DEFAULT_PHASE1[k]
        lines.append(f'{k}: "{v}"')
    return "\n".join(lines) + "\n"


def _load_policy_dict_from_file(path: Path) -> dict[str, Any]:
    try:
        import yaml  # type: ignore[import-untyped]
    except ImportError as e:  # pragma: no cover
        raise ImportError("YAML support requires the 'pyyaml' package.") from e
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        raise ProjectPermissionPolicyError(
            f"could not read {path}: {e}",
            policy_path=path,
        ) from e
    try:
        loaded = yaml.safe_load(text)
    except Exception as e:  # noqa: BLE001 — re-raise as policy error
        raise ProjectPermissionPolicyError(
            f"invalid YAML in {path}: {e}",
            policy_path=path,
        ) from e
    if loaded is None:
        return {}
    if not isinstance(loaded, dict):
        raise ProjectPermissionPolicyError(
            f"{path}: expected a YAML mapping (object) at the top level, got {type(loaded).__name__}",
            policy_path=path,
        )
    return {str(k): v for k, v in loaded.items()}


def _strict_overrides_from_raw(raw: dict[str, Any], *, policy_path: Path) -> dict[str, str]:
    """Build overrides from file keys; raises ``ProjectPermissionPolicyError`` on any invalid value."""
    errs: list[str] = []
    overrides: dict[str, str] = {}
    for k in PHASE1_KEYS:
        if k not in raw:
            continue
        try:
            overrides[k] = parse_strict_permission_value(k, raw[k])
        except ValueError as e:
            errs.append(str(e))
    if errs:
        msg = (
            f"Invalid Phase 1 policy file {policy_path}.\n"
            "Each Phase 1 key, if present, must be a quoted string: "
            f"{format_accepted_values()}.\n"
            + "\n".join(f"  - {line}" for line in errs)
        )
        raise ProjectPermissionPolicyError(msg, policy_path=policy_path, errors=errs)
    return overrides


def load_project_permission_policy(
    repo_root: Path,
    product_id: str,
    *,
    products_dir: Path | None = None,
) -> ProjectPermissionPolicy:
    """
    Load policy from ``products/<id>/argus.policy.yaml`` if present; else use defaults only.

    **Missing file:** Not an error — built-in defaults apply and ``load_warnings`` explains that
    no explicit policy file was found (visible, not silent).

    **Present file:** Parsed under **strict** rules. Invalid values raise
    :class:`~argus.project_permissions.errors.ProjectPermissionPolicyError` — never coerced.
    Unquoted YAML ``yes``/``no`` load as booleans and are rejected with an explicit message.

    **Omitted keys** in the file are filled from defaults (each Phase 1 key always has a defined
    effective value after load).
    """
    pid = str(product_id or "").strip()
    path = policy_file_path(repo_root, pid, products_dir=products_dir)
    warnings: list[str] = []

    if path.is_file():
        raw = _load_policy_dict_from_file(path)
        raw = {k: v for k, v in raw.items() if k != "schema"}
        overrides = _strict_overrides_from_raw(raw, policy_path=path)
    else:
        try:
            rel = path.relative_to(repo_root.resolve())
        except ValueError:
            rel = path
        warnings.append(
            f"no policy file at {rel} — using built-in defaults for all Phase 1 keys "
            f"({format_accepted_values()}). Add products/{pid}/argus.policy.yaml to set an explicit stance.",
        )
        overrides = {}

    merged = merged_defaults(overrides)
    for k in PHASE1_KEYS:
        if merged[k] not in VALID_PERMISSION_VALUES:
            # Defensive — merged_defaults should always yield valid tokens
            raise ProjectPermissionPolicyError(
                f"internal: coerced invalid {k!r} after merge",
                policy_path=path if path.is_file() else None,
            )

    return ProjectPermissionPolicy(
        product_id=pid,
        policy_path=path if path.is_file() else None,
        values=merged,
        load_warnings=warnings,
    )


def write_default_policy_file(product_root: Path) -> Path:
    """Write ``argus.policy.yaml`` under ``product_root`` (idempotent overwrite for scaffold)."""
    path = product_root / "argus.policy.yaml"
    path.write_text(default_policy_yaml_text(), encoding="utf-8")
    return path


__all__ = [
    "default_policy_yaml_text",
    "load_project_permission_policy",
    "policy_file_path",
    "write_default_policy_file",
]
