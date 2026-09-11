"""Execute reset plans and emit durable logs (non–dry-run only)."""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from argus.reset.plan import ResetPlan, plan_summary_counts

RESET_LOG_SCHEMA = "argus.reset_log.v1"
RESET_LOG_REL = "runs/debug/reset/latest.json"


@dataclass
class ResetResult:
    dry_run: bool
    removed_paths: list[str]
    files_deleted: int
    dirs_deleted: int
    log_written: Path | None
    preserved_categories: tuple[str, ...]


def _delete_target(root: Path, rel_posix: str) -> tuple[int, int]:
    """Remove file or directory under *root*. Returns (files, dirs) deleted counts."""
    path = (root / rel_posix).resolve()
    if not path.exists():
        return 0, 0
    try:
        path.relative_to(root.resolve())
    except ValueError as e:
        raise ValueError(f"Refusing to delete path outside repo: {path}") from e

    if path.is_file():
        path.unlink()
        return 1, 0
    if path.is_dir():
        nf = sum(1 for p in path.rglob("*") if p.is_file())
        nd = sum(1 for p in path.rglob("*") if p.is_dir()) + 1
        shutil.rmtree(path)
        return nf, nd
    return 0, 0


def execute_reset(plan: ResetPlan, *, dry_run: bool) -> ResetResult:
    """
    Apply *plan*. If *dry_run* is True, no mutations (counts from :func:`plan_summary_counts`).

    Writes ``runs/debug/reset/latest.json`` after a successful non–dry-run reset
    (creates directories as needed; ``runs/README.md`` is never a target).
    """
    root = plan.repo_root

    if dry_run:
        counts = plan_summary_counts(plan)
        return ResetResult(
            dry_run=True,
            removed_paths=list(plan.paths_targets),
            files_deleted=counts["files"],
            dirs_deleted=counts["directories"],
            log_written=None,
            preserved_categories=plan.preserved_categories,
        )

    removed: list[str] = []
    total_files = 0
    total_dirs = 0
    for rel in plan.paths_targets:
        p = root / rel
        if not p.exists():
            continue
        nf, nd = _delete_target(root, rel)
        removed.append(rel)
        total_files += nf
        total_dirs += nd

    log_path = _write_reset_log(
        root,
        mode=plan.mode,
        dry_run=False,
        removed_paths=removed,
        files_deleted=total_files,
        dirs_deleted=total_dirs,
        preserved=tuple(plan.preserved_categories),
    )
    return ResetResult(
        dry_run=False,
        removed_paths=removed,
        files_deleted=total_files,
        dirs_deleted=total_dirs,
        log_written=log_path,
        preserved_categories=plan.preserved_categories,
    )


def _write_reset_log(
    repo_root: Path,
    *,
    mode: str,
    dry_run: bool,
    removed_paths: list[str],
    files_deleted: int,
    dirs_deleted: int,
    preserved: tuple[str, ...],
) -> Path:
    log_path = (repo_root / RESET_LOG_REL).resolve()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "schema": RESET_LOG_SCHEMA,
        "timestamp_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "mode": mode,
        "dry_run": dry_run,
        "paths_removed": sorted(removed_paths),
        "counts": {
            "files_deleted": files_deleted,
            "directories_deleted": dirs_deleted,
        },
        "preserved_categories": list(preserved),
    }
    log_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return log_path
