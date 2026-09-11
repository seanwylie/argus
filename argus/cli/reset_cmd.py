"""CLI handler for ``argus reset``."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from argus.reset.execute import execute_reset
from argus.reset.plan import ResetMode, build_reset_plan, format_plan_report

CONFIRM_PORTFOLIO = "RESET_PORTFOLIO"
CONFIRM_ALL = "RESET_ALL"


def _mode_from_args(args: Any) -> ResetMode:
    if args.soft:
        return "soft"
    if args.portfolio:
        return "portfolio"
    if args.all:
        return "all"
    raise RuntimeError("reset mode not set")


def run_reset_command(args: Any, repo_root: Path) -> int:
    """Entry point from dispatch."""
    mode = _mode_from_args(args)
    dry_run: bool = bool(args.dry_run)
    root = repo_root.resolve()

    if not dry_run:
        if mode == "portfolio" and args.confirm != CONFIRM_PORTFOLIO:
            print(
                f"error: --portfolio requires --confirm {CONFIRM_PORTFOLIO} (or use --dry-run)",
                file=sys.stderr,
            )
            return 2
        if mode == "all" and args.confirm != CONFIRM_ALL:
            print(
                f"error: --all requires --confirm {CONFIRM_ALL} (or use --dry-run)",
                file=sys.stderr,
            )
            return 2

    plan = build_reset_plan(root, mode)
    sys.stdout.write(format_plan_report(plan))

    result = execute_reset(plan, dry_run=dry_run)

    if dry_run:
        sys.stdout.write(
            f"\nDry run: no changes made. Would touch {len(result.removed_paths)} top-level path(s); "
            f"~{result.files_deleted} file(s), ~{result.dirs_deleted} dir node(s) under those trees.\n"
        )
        return 0

    assert result.log_written is not None
    sys.stdout.write(
        f"\nReset complete ({mode}). Removed {len(result.removed_paths)} path(s); "
        f"files_deleted={result.files_deleted}, directories_deleted={result.dirs_deleted}.\n"
        f"Log: {result.log_written.relative_to(root)}\n"
    )
    return 0
