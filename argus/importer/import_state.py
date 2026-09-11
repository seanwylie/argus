"""Durable ``import_state`` under ``product.yaml`` ``raw_extensions`` (machine-readable)."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

IMPORT_STATE_SCHEMA = "argus.import_state.v1"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def repo_relative_summary_path(product_id: str) -> str:
    return f"products/{product_id}/first_pass_argus_summary.md"


def build_import_state(
    *,
    source_repo_url: str,
    cache_slug: str,
    sync_excludes: list[str],
    include_cursor: bool,
    include_local_db_artifacts: bool,
    exclude_node_artifacts: bool,
    extra_excludes: list[str],
    imported_from_branch: str | None,
    imported_from_commit: str | None,
    imported_at_utc: str,
    first_pass_ran: bool,
    first_pass_status: str,
    first_pass_summary_path: str | None,
    first_pass_command_errors: list[str] | None = None,
    evaluation_error: str | None = None,
    preserve_product_git: bool = True,
) -> dict[str, Any]:
    """Stable keys for ``raw_extensions.import_state``."""
    state: dict[str, Any] = {
        "schema": IMPORT_STATE_SCHEMA,
        "import_mode": "cache_sync",
        "source_repo_url": source_repo_url,
        "cache_slug": cache_slug,
        "imported_at_utc": imported_at_utc,
        "sync_excludes": list(sync_excludes),
        "include_cursor": include_cursor,
        "include_local_db_artifacts": include_local_db_artifacts,
        "exclude_node_artifacts": exclude_node_artifacts,
        "extra_excludes": list(extra_excludes),
        "preserve_product_git": bool(preserve_product_git),
        "first_pass_ran": first_pass_ran,
        "first_pass_status": first_pass_status,
        "first_pass_summary_path": first_pass_summary_path,
    }
    if imported_from_branch is not None:
        state["imported_from_branch"] = imported_from_branch
    if imported_from_commit is not None:
        state["imported_from_commit"] = imported_from_commit
    if first_pass_command_errors:
        state["first_pass_command_errors"] = list(first_pass_command_errors)
    if evaluation_error:
        state["evaluation_error"] = evaluation_error
    return state


def merge_import_state_into_raw_extensions(
    raw_extensions: dict[str, Any],
    import_state: dict[str, Any],
) -> dict[str, Any]:
    out = dict(raw_extensions)
    out["import_state"] = import_state
    return out


def extract_import_state(product_yaml: dict[str, Any]) -> dict[str, Any] | None:
    raw = product_yaml.get("raw_extensions")
    if not isinstance(raw, dict):
        return None
    st = raw.get("import_state")
    return st if isinstance(st, dict) else None


def load_product_yaml_dict(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    from argus.products.loader import load_yaml_file

    data, err = load_yaml_file(path)
    if err or not isinstance(data, dict):
        return None, err or "product.yaml is not a mapping"
    return data, None


def patch_product_yaml_import_state(
    product_root: Path,
    import_state: dict[str, Any],
) -> None:
    """Load ``product.yaml``, replace ``raw_extensions.import_state``, write back."""
    import yaml

    path = product_root / "product.yaml"
    raw, err = load_product_yaml_dict(path)
    if err or raw is None:
        raise ValueError(err or "cannot load product.yaml")
    re = raw.get("raw_extensions")
    if not isinstance(re, dict):
        re = {}
    re = merge_import_state_into_raw_extensions(re, import_state)
    raw["raw_extensions"] = re
    text = yaml.safe_dump(raw, sort_keys=False, allow_unicode=True, default_flow_style=False)
    path.write_text(text, encoding="utf-8")
