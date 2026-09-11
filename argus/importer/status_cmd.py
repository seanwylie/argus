"""Inspect importer metadata for a product without re-importing."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from argus.importer.import_state import extract_import_state, load_product_yaml_dict


def default_cache_root(repo_root: Path) -> Path:
    return repo_root / ".import_cache"


def run_status(
    repo_root: Path,
    product_id: str,
    *,
    json_out: bool,
) -> int:
    """Print importer-oriented status for ``products/<product_id>/``."""
    product_root = (repo_root / "products" / product_id).resolve()
    py_path = product_root / "product.yaml"

    data, err = load_product_yaml_dict(py_path)
    if err or data is None:
        print(f"error: cannot read product.yaml: {err}", file=sys.stderr)
        return 2

    ist = extract_import_state(data)
    if not ist:
        print(
            "error: raw_extensions.import_state missing or not a mapping — "
            "not an importer-managed product or corrupt product.yaml.",
            file=sys.stderr,
        )
        return 3

    sch = ist.get("schema")
    if sch and sch != "argus.import_state.v1":
        print(f"warning: unexpected import_state.schema: {sch!r}", file=sys.stderr)

    cache_slug = ist.get("cache_slug")
    cache_path: Path | None = None
    if isinstance(cache_slug, str) and cache_slug.strip():
        cache_path = (default_cache_root(repo_root) / cache_slug).resolve()

    summary_rel = ist.get("first_pass_summary_path")
    summary_path = product_root / "first_pass_argus_summary.md"
    if isinstance(summary_rel, str) and summary_rel.strip():
        summary_path = (repo_root / summary_rel).resolve()

    payload: dict[str, Any] = {
        "product_id": product_id,
        "source_repo_url": ist.get("source_repo_url"),
        "import_mode": ist.get("import_mode"),
        "cache_slug": cache_slug,
        "cache_path": str(cache_path) if cache_path else None,
        "cache_dir_exists": cache_path.is_dir() if cache_path else False,
        "imported_from_branch": ist.get("imported_from_branch"),
        "imported_from_commit": ist.get("imported_from_commit"),
        "imported_at_utc": ist.get("imported_at_utc"),
        "product_root": str(product_root),
        "product_dir_exists": product_root.is_dir(),
        "first_pass_ran": ist.get("first_pass_ran"),
        "first_pass_status": ist.get("first_pass_status"),
        "first_pass_summary_path": str(summary_path) if summary_path else None,
        "first_pass_summary_exists": summary_path.is_file() if summary_path else False,
        "sync_excludes_count": len(ist.get("sync_excludes") or []) if isinstance(ist.get("sync_excludes"), list) else 0,
        "include_cursor": ist.get("include_cursor"),
        "include_local_db_artifacts": ist.get("include_local_db_artifacts"),
        "exclude_node_artifacts": ist.get("exclude_node_artifacts"),
        "extra_excludes": ist.get("extra_excludes"),
    }

    if json_out:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0

    lines = [
        f"product_id: {product_id}",
        f"source_repo_url: {payload['source_repo_url']}",
        f"import_mode: {payload['import_mode']}",
        f"cache_slug: {cache_slug}",
        f"cache_path: {payload['cache_path']}",
        f"cache_dir_exists: {payload['cache_dir_exists']}",
        f"imported_from_branch: {payload['imported_from_branch']!r}",
        f"imported_from_commit: {payload['imported_from_commit']!r}",
        f"imported_at_utc: {payload['imported_at_utc']}",
        f"product_root: {product_root}",
        f"product_dir_exists: {product_root.is_dir()}",
        f"first_pass_ran: {ist.get('first_pass_ran')}",
        f"first_pass_status: {ist.get('first_pass_status')}",
        f"first_pass_summary: {summary_path} (exists={summary_path.is_file()})",
        "",
        "import flags (from import_state):",
        f"  include_cursor: {ist.get('include_cursor')}",
        f"  include_local_db_artifacts: {ist.get('include_local_db_artifacts')}",
        f"  exclude_node_artifacts (node_modules etc. excluded when true): {ist.get('exclude_node_artifacts')}",
        f"  extra_excludes: {ist.get('extra_excludes')}",
        f"  sync_excludes: {len(ist.get('sync_excludes') or [])} patterns (see product.yaml raw_extensions.import_state.sync_excludes)",
    ]
    ev = ist.get("evaluation_error")
    if ev:
        lines.extend(["", f"evaluation_error (last run): {ev}"])
    errs = ist.get("first_pass_command_errors")
    if isinstance(errs, list) and errs:
        lines.extend(["", "first_pass_command_errors (truncated in console; see list in product.yaml):"])
        for e in errs[:5]:
            lines.append(f"  - {e}")
        if len(errs) > 5:
            lines.append(f"  ... and {len(errs) - 5} more")

    print("\n".join(lines))
    return 0
