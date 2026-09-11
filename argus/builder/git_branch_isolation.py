"""
Per-run git branch isolation for nested product workspaces (``products/<id>/.git``).

Creates ``builder/<slug>-<shortid>`` before agent execution so work is attributable to a
dedicated branch. Does **not** merge, push, or delete branches.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import Any

from argus.builder.git_diff_capture import working_tree_dirty

GIT_BRANCH_ISOLATION_SCHEMA = "argus.builder.git_branch_isolation.v1"


def _git_ok() -> bool:
    return shutil.which("git") is not None


def read_git_branch(git_cwd: Path) -> tuple[str | None, str | None]:
    """Return current branch name (or ``HEAD`` if detached) or error."""
    if not _git_ok():
        return None, "git not on PATH"
    if not (git_cwd / ".git").is_dir():
        return None, "not a git repository"
    p = subprocess.run(
        ["git", "-C", str(git_cwd), "rev-parse", "--abbrev-ref", "HEAD"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if p.returncode != 0:
        return None, (p.stderr or p.stdout or "rev-parse failed")[:400]
    return (p.stdout or "").strip() or None, None


def _skipped(status: str, *, reason: str | None = None) -> dict[str, Any]:
    return {
        "schema": GIT_BRANCH_ISOLATION_SCHEMA,
        "branch_isolation_status": status,
        "git_branch_before": None,
        "git_builder_branch": None,
        "git_branch_created": False,
        "trust_degraded_dirty_tree": False,
        "branch_isolation_error": reason,
    }


def compute_git_branch_isolation(
    *,
    execute: bool,
    stale_execute_blocked: bool,
    permission_execute_blocked: bool = False,
    ws: dict[str, Any],
    increment_id: str | None,
    product_id: str,
) -> dict[str, Any]:
    """
    When ``execute`` and nested product git: create and switch to a Builder branch.

    **Dirty tree policy:** if the working tree was dirty before ``checkout -b``, we still
    proceed (same as baseline trust model) but set ``trust_degraded_dirty_tree`` and
    ``branch_isolation_status`` = ``degraded_dirty_tree``. We do **not** stash or auto-commit.

    If ``git checkout -b`` fails, ``branch_isolation_status`` = ``failed`` and invoke should
    not run the agent.

    When ``permission_execute_blocked`` is true (project policy denied ``builder_execute``),
    branch creation is skipped so no builder branch is created for a run that will not execute.
    """
    if not execute:
        return _skipped("skipped_not_executing")
    if stale_execute_blocked:
        return _skipped("skipped_stale_contract")
    if permission_execute_blocked:
        return _skipped(
            "skipped_permission_denied",
            reason="project permission gate denied builder_execute (see invoke record project_permission_decision)",
        )
    if ws.get("kind") != "nested_product":
        return _skipped(
            "skipped_not_nested_product_repo",
            reason="no products/<id>/.git — branch isolation applies only to nested product git",
        )
    git_cwd = ws["git_cwd"]
    assert isinstance(git_cwd, Path)
    return prepare_builder_isolation_branch(
        git_cwd,
        increment_id=increment_id,
        product_id=product_id,
    )


def prepare_builder_isolation_branch(
    git_cwd: Path,
    *,
    increment_id: str | None,
    product_id: str,
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "schema": GIT_BRANCH_ISOLATION_SCHEMA,
        "branch_isolation_status": "failed",
        "git_branch_before": None,
        "git_builder_branch": None,
        "git_branch_created": False,
        "trust_degraded_dirty_tree": False,
        "branch_isolation_error": None,
    }

    if not _git_ok():
        out["branch_isolation_error"] = "git not on PATH"
        return out
    if not (git_cwd / ".git").is_dir():
        out["branch_isolation_error"] = "not a git repository"
        return out

    br_before, br_err = read_git_branch(git_cwd)
    if br_err:
        out["branch_isolation_error"] = br_err
        return out
    out["git_branch_before"] = br_before

    dirty, derr = working_tree_dirty(git_cwd)
    if derr:
        out["branch_isolation_error"] = derr
        return out
    if dirty:
        out["trust_degraded_dirty_tree"] = True

    raw = (increment_id or product_id or "run").strip()
    safe = re.sub(r"[^a-zA-Z0-9._-]+", "-", raw).strip("-")[:48] or "run"
    short = uuid.uuid4().hex[:10]
    new_branch = f"builder/{safe}-{short}"

    p = subprocess.run(
        ["git", "-C", str(git_cwd), "checkout", "-b", new_branch],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if p.returncode != 0:
        out["branch_isolation_error"] = (p.stderr or p.stdout or "git checkout -b failed")[:800]
        return out

    out["git_builder_branch"] = new_branch
    out["git_branch_created"] = True
    out["branch_isolation_status"] = "degraded_dirty_tree" if dirty else "ok"
    return out


def reconcile_branch_snapshot(
    repo_root: Path,
    product_id: str,
    *,
    products_dir: Path | None,
    invoke_data: dict[str, Any] | None,
) -> dict[str, Any]:
    """Optional current branch in the product git repo at reconcile time (audit)."""
    from argus.builder.git_diff_capture import resolve_git_workspace

    out: dict[str, Any] = {
        "git_branch_at_reconcile": None,
        "git_cwd_used": None,
        "read_error": None,
    }
    inv = invoke_data if isinstance(invoke_data, dict) else {}
    gb = inv.get("git_baseline") if isinstance(inv.get("git_baseline"), dict) else {}
    gc = gb.get("git_cwd")
    if gc:
        p = Path(str(gc))
        if p.exists():
            out["git_cwd_used"] = str(p.resolve())
            br, err = read_git_branch(p)
            if err:
                out["read_error"] = err
            else:
                out["git_branch_at_reconcile"] = br
            return out
        gbi = inv.get("git_branch_isolation") if isinstance(inv.get("git_branch_isolation"), dict) else {}
        if gb.get("git_workspace_kind") == "argus_root_worktree" and gbi.get("git_builder_branch"):
            out["git_cwd_used"] = str(repo_root.resolve())
            out["git_branch_at_reconcile"] = gbi.get("git_builder_branch")
            out["read_error"] = (
                "invoke_git_cwd_removed_after_invoke; branch name retained from invoke record"
            )
            return out

    ws = resolve_git_workspace(repo_root, product_id, products_dir=products_dir)
    if ws.get("kind") == "nested_product":
        gcwd = ws["git_cwd"]
        out["git_cwd_used"] = str(gcwd.resolve())
        br, err = read_git_branch(gcwd)
        if err:
            out["read_error"] = err
        else:
            out["git_branch_at_reconcile"] = br
    return out
