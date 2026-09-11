"""
Local-only merge helper for nested product git Builder branches.

Refuses unless ``builder_branch_review.review_status == merge_candidate``.
Does **not** push, open PRs, or delete branches.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from argus.builder.branch_review import REVIEW_MERGE_CANDIDATE
from argus.builder.invoke import invoke_record_dir
from argus.builder.status import compute_builder_status
from argus.core.serialize import dumps_json

BUILDER_MERGE_RECORD_SCHEMA = "argus.builder.merge_record.v1"


def merge_record_dir(repo_root: Path, product_id: str) -> Path:
    return (repo_root / "runs" / "builder" / "merge" / product_id).resolve()


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return raw if isinstance(raw, dict) else None


def _git_ok() -> bool:
    return shutil.which("git") is not None


def _run_git(git_cwd: Path, args: list[str], *, timeout: int = 120) -> tuple[int, str]:
    p = subprocess.run(
        ["git", "-C", str(git_cwd), *args],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    msg = (p.stderr or p.stdout or "").strip()
    return p.returncode, msg[:8000]


def _working_tree_clean(git_cwd: Path) -> tuple[bool, str | None]:
    code, out = _run_git(git_cwd, ["status", "--porcelain"], timeout=60)
    if code != 0:
        return False, out[:400] if out else "git status failed"
    return not (out or "").strip(), None


def _branch_exists(git_cwd: Path, branch: str) -> bool:
    code, _ = _run_git(git_cwd, ["rev-parse", "--verify", branch], timeout=30)
    return code == 0


def _resolve_default_integration_branch(git_cwd: Path) -> tuple[str | None, str | None]:
    for name in ("main", "master"):
        if _branch_exists(git_cwd, name):
            return name, None
    return None, "no local branch named main or master — pass --into <branch>"


def run_builder_merge_local(
    repo_root: Path,
    product_id: str,
    *,
    products_dir: Path | None,
    into_branch: str | None,
    dry_run: bool = False,
    no_record: bool = False,
) -> tuple[dict[str, Any], int]:
    """
    Merge ``builder_branch`` into the integration branch in the **nested** product git repo.

    Returns ``(record, exit_code)``: ``0`` success or dry-run allowed, ``1`` git error, ``2`` refused.
    """
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    repo_root = repo_root.resolve()

    base: dict[str, Any] = {
        "schema": BUILDER_MERGE_RECORD_SCHEMA,
        "product_id": product_id,
        "merged_at_utc": now,
        "review_status_at_merge_time": None,
        "builder_branch": None,
        "target_branch": None,
        "git_cwd": None,
        "baseline_commit": None,
        "changed_file_count": None,
        "merge_status": "failed",
        "refusal_reason": None,
        "error": None,
        "git_merge_exit_code": None,
        "dry_run": dry_run,
    }

    if not _git_ok():
        base["merge_status"] = "refused"
        base["refusal_reason"] = "git not on PATH"
        return base, 2

    status = compute_builder_status(repo_root, product_id, products_dir=products_dir)
    lr = status.get("latest_reconcile") or {}
    review_status = lr.get("review_status")
    builder_branch = lr.get("review_builder_branch")
    baseline_commit = lr.get("review_baseline_commit")
    changed_file_count = lr.get("review_changed_file_count")

    base["review_status_at_merge_time"] = review_status
    base["builder_branch"] = builder_branch
    base["baseline_commit"] = baseline_commit
    base["changed_file_count"] = changed_file_count

    if review_status != REVIEW_MERGE_CANDIDATE:
        base["merge_status"] = "refused"
        base["refusal_reason"] = (
            f"review_status is {review_status!r}; only {REVIEW_MERGE_CANDIDATE!r} is allowed"
        )
        _maybe_write_record(repo_root, product_id, base, no_record)
        return base, 2

    if not builder_branch or not isinstance(builder_branch, str):
        base["merge_status"] = "refused"
        base["refusal_reason"] = "review_builder_branch missing on status (cannot merge)"
        _maybe_write_record(repo_root, product_id, base, no_record)
        return base, 2

    inv_path = invoke_record_dir(repo_root, product_id) / "latest.json"
    inv = _read_json(inv_path)
    gb = inv.get("git_baseline") if inv else None
    gb = gb if isinstance(gb, dict) else {}

    if gb.get("git_workspace_kind") != "nested_product":
        base["merge_status"] = "refused"
        base["refusal_reason"] = (
            "merge helper only runs in nested product git (git_baseline.git_workspace_kind must be nested_product)"
        )
        _maybe_write_record(repo_root, product_id, base, no_record)
        return base, 2

    git_cwd_raw = gb.get("git_cwd")
    if not git_cwd_raw:
        base["merge_status"] = "refused"
        base["refusal_reason"] = "invoke git_baseline.git_cwd missing"
        _maybe_write_record(repo_root, product_id, base, no_record)
        return base, 2

    git_cwd = Path(str(git_cwd_raw)).resolve()
    base["git_cwd"] = str(git_cwd)

    if not (git_cwd / ".git").exists():
        base["merge_status"] = "refused"
        base["refusal_reason"] = "git_cwd is not a git repository"
        _maybe_write_record(repo_root, product_id, base, no_record)
        return base, 2

    target = into_branch.strip() if into_branch else None
    if not target:
        target, err = _resolve_default_integration_branch(git_cwd)
        if not target:
            base["merge_status"] = "refused"
            base["refusal_reason"] = err or "could not resolve integration branch"
            _maybe_write_record(repo_root, product_id, base, no_record)
            return base, 2

    base["target_branch"] = target

    if not _branch_exists(git_cwd, builder_branch):
        base["merge_status"] = "refused"
        base["refusal_reason"] = f"builder branch not found in product repo: {builder_branch!r}"
        _maybe_write_record(repo_root, product_id, base, no_record)
        return base, 2

    if not _branch_exists(git_cwd, target):
        base["merge_status"] = "refused"
        base["refusal_reason"] = f"integration branch not found: {target!r}"
        _maybe_write_record(repo_root, product_id, base, no_record)
        return base, 2

    clean, cerr = _working_tree_clean(git_cwd)
    if not clean:
        base["merge_status"] = "refused"
        base["refusal_reason"] = (
            "working tree not clean in product git — commit or stash before merge"
            + (f": {cerr}" if cerr else "")
        )
        _maybe_write_record(repo_root, product_id, base, no_record)
        return base, 2

    if dry_run:
        base["merge_status"] = "dry_run_ok"
        base["error"] = None
        base["refusal_reason"] = None
        _maybe_write_record(repo_root, product_id, base, no_record)
        return base, 0

    code_co, out_co = _run_git(git_cwd, ["checkout", target])
    if code_co != 0:
        base["merge_status"] = "failed"
        base["error"] = out_co or f"git checkout {target} failed"
        base["git_merge_exit_code"] = code_co
        _maybe_write_record(repo_root, product_id, base, no_record)
        return base, 1

    code_mg, out_mg = _run_git(
        git_cwd,
        ["merge", "--no-edit", builder_branch],
        timeout=180,
    )
    base["git_merge_exit_code"] = code_mg
    if code_mg != 0:
        base["merge_status"] = "failed"
        base["error"] = out_mg or f"git merge exit {code_mg}"
        _maybe_write_record(repo_root, product_id, base, no_record)
        return base, 1

    base["merge_status"] = "ok"
    base["error"] = None
    base["refusal_reason"] = None
    _maybe_write_record(repo_root, product_id, base, no_record)
    return base, 0


def _maybe_write_record(repo_root: Path, product_id: str, record: dict[str, Any], no_record: bool) -> None:
    if no_record:
        return
    d = merge_record_dir(repo_root, product_id)
    d.mkdir(parents=True, exist_ok=True)
    (d / "latest.json").write_text(dumps_json(record), encoding="utf-8")
