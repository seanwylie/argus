"""
Argus-root Builder isolation via ``git worktree`` (Phase 2C).

When the product has no nested ``products/<id>/.git``, branch isolation cannot use
``checkout -b`` in the product repo. Instead, create a dedicated worktree under
``runs/builder/worktrees/<product_id>/`` on a ``builder/...`` branch at the
current Argus ``HEAD``.

Does **not** merge, push, or delete branches. Worktrees are removed after invoke
when execution completes so refs remain in the main repository.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import Any

from argus.builder.git_branch_isolation import GIT_BRANCH_ISOLATION_SCHEMA, read_git_branch
from argus.builder.git_diff_capture import product_directory, working_tree_dirty

ENV_ARGUS_ROOT_WORKTREE = "ARGUS_BUILDER_ARGUS_ROOT_WORKTREE"


def argus_root_worktree_enabled() -> bool:
    """Default on; set ``ARGUS_BUILDER_ARGUS_ROOT_WORKTREE=0`` to disable."""
    v = (os.environ.get(ENV_ARGUS_ROOT_WORKTREE) or "1").strip().lower()
    return v not in ("0", "false", "no", "off")


def _git_ok() -> bool:
    return shutil.which("git") is not None


def _base_out_failed(msg: str) -> dict[str, Any]:
    return {
        "schema": GIT_BRANCH_ISOLATION_SCHEMA,
        "branch_isolation_status": "failed",
        "isolation_mode": "argus_root_worktree",
        "git_branch_before": None,
        "git_builder_branch": None,
        "git_branch_created": False,
        "git_worktree_path": None,
        "trust_degraded_dirty_tree": False,
        "branch_isolation_error": msg,
        "worktree_removed_after_invoke": None,
    }


def sync_builder_artifacts_into_worktree(
    main_repo_root: Path,
    worktree_root: Path,
    product_id: str,
    *,
    products_dir: Path | None,
    artifact_source: str,
) -> None:
    """
    Copy prepared product (and optional runs/prepare) tree from the main working tree.

    ``git worktree add`` materializes committed ``HEAD`` only; nested isolation keeps
    uncommitted files in-tree. Mirroring the product subtree preserves that behavior.
    """
    src_pd = product_directory(main_repo_root, product_id, products_dir)
    dst_pd = product_directory(worktree_root, product_id, products_dir)
    _copy_tree_merge(src_pd, dst_pd)

    if artifact_source == "runs_prepare":
        src_r = (main_repo_root / "runs" / "builder" / "prepare" / product_id).resolve()
        dst_r = (worktree_root / "runs" / "builder" / "prepare" / product_id).resolve()
        _copy_tree_merge(src_r, dst_r)


def _copy_tree_merge(src: Path, dst: Path) -> None:
    if not src.is_dir():
        return
    dst.mkdir(parents=True, exist_ok=True)
    for path in src.rglob("*"):
        rel = path.relative_to(src)
        target = dst / rel
        if path.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)


def prepare_argus_root_worktree_isolation(
    repo_root: Path,
    product_id: str,
    *,
    increment_id: str | None,
) -> dict[str, Any]:
    """
    Create ``git worktree add`` on a new ``builder/...`` branch at ``HEAD``.

    Caller must only invoke for Argus-root workspaces when agent execute is allowed.
    """
    rr = repo_root.resolve()
    out: dict[str, Any] = {
        "schema": GIT_BRANCH_ISOLATION_SCHEMA,
        "branch_isolation_status": "failed",
        "isolation_mode": "argus_root_worktree",
        "git_branch_before": None,
        "git_builder_branch": None,
        "git_branch_created": False,
        "git_worktree_path": None,
        "trust_degraded_dirty_tree": False,
        "branch_isolation_error": None,
        "worktree_removed_after_invoke": None,
    }

    if not _git_ok():
        out["branch_isolation_error"] = "git not on PATH"
        return out

    if not (rr / ".git").exists():
        out["branch_isolation_error"] = "Argus repo root is not a git repository"
        return out

    br_before, br_err = read_git_branch(rr)
    if br_err:
        out["branch_isolation_error"] = br_err
        return out
    out["git_branch_before"] = br_before

    dirty, derr = working_tree_dirty(rr)
    if derr:
        out["branch_isolation_error"] = derr
        return out
    if dirty:
        out["trust_degraded_dirty_tree"] = True

    raw = (increment_id or product_id or "run").strip()
    safe = re.sub(r"[^a-zA-Z0-9._-]+", "-", raw).strip("-")[:48] or "run"
    short = uuid.uuid4().hex[:10]
    new_branch = f"builder/{safe}-{short}"

    wt_parent = rr / "runs" / "builder" / "worktrees" / product_id
    wt_parent.mkdir(parents=True, exist_ok=True)
    wt_path = wt_parent / f"wt_{uuid.uuid4().hex[:12]}"
    if wt_path.exists():
        out["branch_isolation_error"] = f"worktree path already exists: {wt_path}"
        return out

    p = subprocess.run(
        [
            "git",
            "-C",
            str(rr),
            "worktree",
            "add",
            str(wt_path),
            "-b",
            new_branch,
            "HEAD",
        ],
        capture_output=True,
        text=True,
        timeout=180,
    )
    if p.returncode != 0:
        err = (p.stderr or p.stdout or "git worktree add failed")[:800]
        out["branch_isolation_error"] = err
        return out

    out["git_worktree_path"] = str(wt_path.resolve())
    out["git_builder_branch"] = new_branch
    out["git_branch_created"] = True
    out["branch_isolation_status"] = "degraded_dirty_tree" if dirty else "ok"
    return out


def remove_argus_root_worktree(repo_root: Path, worktree_path: Path) -> tuple[bool, str | None]:
    """
    ``git worktree remove --force`` from the main repository; branch ref is retained.
    """
    rr = repo_root.resolve()
    wt = worktree_path.resolve()
    if not wt.is_dir():
        return True, None
    if not _git_ok():
        return False, "git not on PATH"
    p = subprocess.run(
        ["git", "-C", str(rr), "worktree", "remove", "--force", str(wt)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if p.returncode != 0:
        return False, (p.stderr or p.stdout or "git worktree remove failed")[:800]
    return True, None
