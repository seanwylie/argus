"""
CLI helper: write ``content/next_expansion.json`` ``primary_target`` for non-content-slot contract kinds.

Eligible kinds come from :func:`manual_set_target_kinds` (registry ``manual_set_target_eligible``).
Normal operator flow: ``set-target`` (optional ``--prepare`` to chain prepare) → ``prepare`` if
needed → ``invoke`` → ``reconcile`` → ``review`` / ``merge`` (see ``docs/builder-execution-contract.md``).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from argus.builder.contract_registry import (
    build_execution_contract_for_kind,
    manual_set_target_kinds,
)
from argus.builder.next_expansion_prepare import (
    SCHEMA_SUPPORTED,
    NextExpansionPrepareError,
    next_expansion_path,
    validate_next_expansion_payload,
)
from argus.core.serialize import dumps_json

MANUAL_TARGET_KINDS = manual_set_target_kinds()


@dataclass(frozen=True)
class SetManualTargetResult:
    path: Path
    payload: dict[str, Any]
    wrote: bool


def _default_schema() -> str:
    return next(iter(sorted(SCHEMA_SUPPORTED)))


def _read_existing_next_expansion(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise NextExpansionPrepareError(f"Invalid JSON in {path}: {e}") from e
    if not isinstance(raw, dict):
        raise NextExpansionPrepareError("next_expansion root must be an object")
    return raw


def build_primary_target_bug_fix(
    *,
    target_id: str,
    allowed_paths_exact: list[str],
    bug_statement: str,
    group_id: Any = None,
    allowed_path_patterns: list[str] | None = None,
    success_condition: str | None = None,
    stop_condition: str | None = None,
) -> dict[str, Any]:
    pt: dict[str, Any] = {
        "target_type": "bug_fix",
        "id": str(target_id).strip(),
        "group_id": group_id,
        "allowed_paths_exact": list(allowed_paths_exact),
        "bug_statement": str(bug_statement).strip(),
    }
    if allowed_path_patterns:
        pt["allowed_path_patterns"] = list(allowed_path_patterns)
    if success_condition:
        pt["success_condition"] = str(success_condition).strip()
    if stop_condition:
        pt["stop_condition"] = str(stop_condition).strip()
    return pt


def build_primary_target_signal_instrumentation(
    *,
    target_id: str,
    allowed_paths_exact: list[str],
    signal_statement: str,
    group_id: Any = None,
    allowed_path_patterns: list[str] | None = None,
    success_condition: str | None = None,
    stop_condition: str | None = None,
    expected_product_paths_exist: list[str] | None = None,
    instrumentation_touch_paths: list[str] | None = None,
) -> dict[str, Any]:
    pt: dict[str, Any] = {
        "target_type": "signal_instrumentation",
        "id": str(target_id).strip(),
        "group_id": group_id,
        "allowed_paths_exact": list(allowed_paths_exact),
        "signal_statement": str(signal_statement).strip(),
    }
    if allowed_path_patterns:
        pt["allowed_path_patterns"] = list(allowed_path_patterns)
    if success_condition:
        pt["success_condition"] = str(success_condition).strip()
    if stop_condition:
        pt["stop_condition"] = str(stop_condition).strip()
    if expected_product_paths_exist:
        pt["expected_product_paths_exist"] = list(expected_product_paths_exist)
    if instrumentation_touch_paths:
        pt["instrumentation_touch_paths"] = list(instrumentation_touch_paths)
    return pt


def apply_manual_builder_target(
    repo_root: Path,
    product_id: str,
    *,
    products_dir: Path | None,
    primary_target: dict[str, Any],
    dry_run: bool = False,
    stamp_as_of_utc: bool = True,
) -> SetManualTargetResult:
    """
    Merge ``primary_target`` into ``content/next_expansion.json``.

    Preserves unrelated top-level keys (e.g. ``explicit_non_targets``). Overwrites
    ``primary_target`` and ensures ``schema`` is set. Optionally sets ``as_of_utc``.
    Validates via :func:`validate_next_expansion_payload` and
    :func:`build_execution_contract_for_kind` for the declared kind.
    """
    kind = str(primary_target.get("target_type") or "").strip()
    if kind not in MANUAL_TARGET_KINDS:
        raise ValueError(f"apply_manual_builder_target supports only {sorted(MANUAL_TARGET_KINDS)}, got {kind!r}")

    path = next_expansion_path(repo_root, product_id, products_dir=products_dir)
    base = _read_existing_next_expansion(path)
    out: dict[str, Any] = {
        k: v for k, v in base.items() if k not in ("primary_target", "schema", "as_of_utc")
    }
    out["schema"] = str(base.get("schema") or _default_schema())
    out["primary_target"] = primary_target
    if stamp_as_of_utc:
        out["as_of_utc"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    elif "as_of_utc" in base:
        out["as_of_utc"] = base["as_of_utc"]

    validate_next_expansion_payload(out)
    try:
        build_execution_contract_for_kind(
            kind,
            repo_root=repo_root,
            product_id=product_id,
            products_dir=products_dir,
            raw=out,
            pt=primary_target,
        )
    except ValueError as e:
        raise NextExpansionPrepareError(str(e)) from e

    wrote = False
    if not dry_run:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(dumps_json(out) + "\n", encoding="utf-8")
        wrote = True

    return SetManualTargetResult(path=path, payload=out, wrote=wrote)


__all__ = [
    "MANUAL_TARGET_KINDS",
    "SetManualTargetResult",
    "apply_manual_builder_target",
    "build_primary_target_bug_fix",
    "build_primary_target_signal_instrumentation",
]
