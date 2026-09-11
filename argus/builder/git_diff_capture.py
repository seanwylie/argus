"""
Git baseline and diff capture for Builder (inspectable change records).

Uses a **nested** git repo under ``products/<product_id>/`` when ``.git`` exists there; otherwise
falls back to the Argus repo root for path listing (honest limitations documented on artifacts).

Diff snapshots are **bounded** (truncated); they are evidence, not proof of correctness.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

from argus.products.git_lifecycle import init_argus_product_git

GIT_DIFF_SUMMARY_SCHEMA = "argus.builder.git_diff_summary.v1"
GIT_BASELINE_SCHEMA = "argus.builder.git_baseline.v1"

# Bounded patch for records (honest: not full truth).
MAX_DIFF_CHARS = 48_000
MAX_DIFF_LINES = 400


def _products_base(repo_root: Path, products_dir: Path | None) -> Path:
    if products_dir is not None:
        return (repo_root / products_dir).resolve()
    return (repo_root / "products").resolve()


def product_directory(repo_root: Path, product_id: str, products_dir: Path | None) -> Path:
    return (_products_base(repo_root, products_dir) / product_id).resolve()


def ensure_product_git_workspace(
    repo_root: Path,
    product_id: str,
    *,
    products_dir: Path | None,
    initial_message: str = "chore(argus): initial product workspace (Builder)",
) -> dict[str, Any]:
    """
    Ensure ``products/<id>/`` has a local ``.git`` and at least one commit when possible.

    Does **not** touch the Argus repository root. Reuses :func:`init_argus_product_git`.
    """
    pr = product_directory(repo_root, product_id, products_dir)
    pr.mkdir(parents=True, exist_ok=True)
    return init_argus_product_git(pr, initial_message=initial_message)


def resolve_git_workspace(
    repo_root: Path,
    product_id: str,
    *,
    products_dir: Path | None,
) -> dict[str, Any]:
    """
    Where Builder should run git commands for this product.

    - **nested_product** — ``products/<id>/.git`` exists: cwd = product dir; map paths with prefix.
    - **argus_root** — no nested repo: cwd = Argus root; paths already repo-relative.
    """
    rr = repo_root.resolve()
    pd = product_directory(repo_root, product_id, products_dir)
    try:
        prod_rel = pd.relative_to(rr).as_posix()
    except ValueError:
        prod_rel = str(pd)

    prefix = prod_rel + "/" if prod_rel else ""
    nested_git = (pd / ".git").is_dir()
    if nested_git:
        return {
            "kind": "nested_product",
            "git_cwd": pd,
            "argus_path_prefix": prefix,
        }
    return {
        "kind": "argus_root",
        "git_cwd": rr,
        "argus_path_prefix": "",
    }


def _git_ok() -> bool:
    return shutil.which("git") is not None


def working_tree_dirty(git_cwd: Path) -> tuple[bool, str | None]:
    if not _git_ok():
        return False, "git not on PATH"
    p = subprocess.run(
        ["git", "-C", str(git_cwd), "status", "--porcelain"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if p.returncode != 0:
        return False, (p.stderr or p.stdout or "git status failed")[:400]
    dirty = bool((p.stdout or "").strip())
    return dirty, None


def capture_baseline_commit(git_cwd: Path) -> dict[str, Any]:
    """
    Record ``HEAD`` and whether the working tree was dirty **before** subsequent work.

    If not a git repo or git fails, returns null fields and an error string.
    """
    out: dict[str, Any] = {
        "schema": GIT_BASELINE_SCHEMA,
        "baseline_commit": None,
        "working_tree_dirty_before": None,
        "error": None,
    }
    if not _git_ok():
        out["error"] = "git not on PATH"
        return out
    if not (git_cwd / ".git").is_dir():
        out["error"] = "not a git repository"
        return out
    dirty, derr = working_tree_dirty(git_cwd)
    if derr:
        out["error"] = derr
        return out
    out["working_tree_dirty_before"] = dirty
    hp = subprocess.run(
        ["git", "-C", str(git_cwd), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if hp.returncode != 0:
        out["error"] = (hp.stderr or hp.stdout or "rev-parse failed")[:400]
        return out
    out["baseline_commit"] = (hp.stdout or "").strip() or None
    return out


def _changed_paths_since_in_repo(git_cwd: Path, baseline_commit: str) -> tuple[list[str], str | None]:
    """Paths relative to ``git_cwd`` (posix). Union: diff vs baseline, cached, untracked."""
    out: set[str] = set()
    for extra in ([], ["--cached"]):
        p = subprocess.run(
            ["git", "-C", str(git_cwd), "diff", "--name-only", *extra, baseline_commit],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if p.returncode != 0:
            return [], (p.stderr or p.stdout or "git diff --name-only failed")[:400]
        for line in (p.stdout or "").splitlines():
            line = line.strip().replace("\\", "/")
            if line:
                out.add(line)
    u = subprocess.run(
        ["git", "-C", str(git_cwd), "ls-files", "--others", "--exclude-standard"],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if u.returncode == 0:
        for line in (u.stdout or "").splitlines():
            line = line.strip().replace("\\", "/")
            if line:
                out.add(line)
    return sorted(out), None


def _norm_argus_paths(paths_relative_to_git_cwd: list[str], argus_prefix: str) -> list[str]:
    out: list[str] = []
    for p in paths_relative_to_git_cwd:
        p2 = p.replace("\\", "/")
        out.append(f"{argus_prefix}{p2}" if argus_prefix else p2)
    return out


def collect_diff_since_baseline(
    git_cwd: Path,
    baseline_commit: str,
    *,
    argus_path_prefix: str,
) -> dict[str, Any]:
    """
    Produce name list, stat, and truncated unified diff from ``baseline_commit`` to now.
    Paths in ``changed_files_argus_relative`` are relative to the Argus repo root.
    """
    summary: dict[str, Any] = {
        "schema": GIT_DIFF_SUMMARY_SCHEMA,
        "baseline_commit": baseline_commit,
        "changed_files_argus_relative": [],
        "changed_file_count": 0,
        "diff_stat": None,
        "truncated_diff": None,
        "truncated_diff_bytes": None,
        "truncated_diff_lines": None,
        "diff_truncated": False,
        "error": None,
    }
    if not _git_ok():
        summary["error"] = "git not on PATH"
        return summary
    rel_paths, err = _changed_paths_since_in_repo(git_cwd, baseline_commit)
    if err:
        summary["error"] = err
        return summary
    argus_paths = _norm_argus_paths(rel_paths, argus_path_prefix)
    summary["changed_files_argus_relative"] = argus_paths
    summary["changed_file_count"] = len(argus_paths)

    st = subprocess.run(
        ["git", "-C", str(git_cwd), "diff", "--stat", baseline_commit],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if st.returncode == 0:
        summary["diff_stat"] = (st.stdout or "").strip() or None

    dp = subprocess.run(
        ["git", "-C", str(git_cwd), "diff", baseline_commit],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if dp.returncode != 0:
        summary["error"] = (dp.stderr or dp.stdout or "git diff failed")[:400]
        return summary
    raw = dp.stdout or ""
    lines = raw.splitlines()
    truncated = False
    if len(raw) > MAX_DIFF_CHARS or len(lines) > MAX_DIFF_LINES:
        truncated = True
        if len(lines) > MAX_DIFF_LINES:
            raw = "\n".join(lines[:MAX_DIFF_LINES]) + "\n… [truncated: line limit] …\n"
        if len(raw) > MAX_DIFF_CHARS:
            raw = raw[: MAX_DIFF_CHARS - 80] + "\n… [truncated: char limit] …\n"
    summary["truncated_diff"] = raw if raw.strip() else None
    summary["truncated_diff_bytes"] = len(raw.encode("utf-8", errors="replace")) if raw else 0
    summary["truncated_diff_lines"] = len(raw.splitlines()) if raw else 0
    summary["diff_truncated"] = truncated
    return summary


def _merge_parent_repo_paths_for_scope(out: dict[str, Any], repo_root: Path) -> None:
    """
    Union nested-product diff paths with Argus repo ``git diff`` paths.

    Agent cwd is typically the Argus root; nested product git does not see ``argus/`` edits.
    Scope evaluation needs the combined set (honest limitation on truncated_diff vs file list).
    """
    from argus.builder.execution_contract import _git_changed_paths

    parent_paths, perr = _git_changed_paths(repo_root.resolve())
    if perr:
        out.setdefault("limitations", []).append(
            f"merge_parent_repo_paths_skipped: {perr}"
        )
        return
    cur = list(out.get("changed_files_argus_relative") or [])
    merged = sorted(set(cur) | set(parent_paths))
    if set(merged) != set(cur):
        out.setdefault("limitations", []).append(
            "merged_parent_repo_paths: union with Argus repo working tree so argus/ and other "
            "paths outside the nested product repo are visible to scope (file list is broader than nested diff alone)"
        )
    out["changed_files_argus_relative"] = merged
    out["changed_file_count"] = len(merged)


def build_reconcile_diff_summary(
    repo_root: Path,
    product_id: str,
    *,
    products_dir: Path | None,
    invoke_data: dict[str, Any] | None,
) -> dict[str, Any]:
    """
    Prefer invoke-record baseline + workspace; else fall back to Argus working-tree listing.

    Returns a ``git_diff_summary``-shaped dict plus ``source`` and ``limitations`` notes.
    """
    rr = repo_root.resolve()
    ws = resolve_git_workspace(repo_root, product_id, products_dir=products_dir)
    inv = invoke_data if isinstance(invoke_data, dict) else {}
    gb = inv.get("git_baseline") if isinstance(inv.get("git_baseline"), dict) else {}
    baseline = gb.get("baseline_commit")
    dirty_before = gb.get("working_tree_dirty_before")
    git_cwd_invoke = gb.get("git_cwd")
    prefix_invoke = str(gb.get("argus_path_prefix") or "")
    kind_invoke = gb.get("git_workspace_kind")

    out: dict[str, Any] = {
        "schema": GIT_DIFF_SUMMARY_SCHEMA,
        "source": None,
        "git_workspace_kind": kind_invoke or ws["kind"],
        "baseline_commit": baseline,
        "working_tree_dirty_before": dirty_before,
        "changed_files_argus_relative": [],
        "changed_file_count": 0,
        "diff_stat": None,
        "truncated_diff": None,
        "truncated_diff_bytes": None,
        "truncated_diff_lines": None,
        "diff_truncated": False,
        "fallback_used": False,
        "limitations": [],
        "error": None,
    }

    if baseline and isinstance(baseline, str) and baseline.strip():
        gcwd = Path(git_cwd_invoke) if git_cwd_invoke else ws["git_cwd"]
        pfx = prefix_invoke if git_cwd_invoke else str(ws.get("argus_path_prefix") or "")
        sub = collect_diff_since_baseline(
            gcwd,
            baseline.strip(),
            argus_path_prefix=pfx,
        )
        out["source"] = "invoke_baseline_diff"
        for k in (
            "changed_files_argus_relative",
            "changed_file_count",
            "diff_stat",
            "truncated_diff",
            "truncated_diff_bytes",
            "truncated_diff_lines",
            "diff_truncated",
            "error",
        ):
            if k in sub:
                out[k] = sub[k]
        if dirty_before:
            out["limitations"].append(
                "working_tree_dirty_before_invoke: baseline diff may include pre-existing uncommitted changes"
            )
        if out.get("diff_truncated"):
            out["limitations"].append("truncated_diff: not a complete patch")
        out["limitations"].append(
            "diff shows repository changes only; it does not prove correctness or task success"
        )
        _merge_parent_repo_paths_for_scope(out, rr)
        return out

    # Fallback: Argus repo root paths (no invoke baseline).
    out["source"] = "argus_working_tree_fallback"
    out["fallback_used"] = True
    out["limitations"].append(
        "no invoke baseline_commit in latest invoke record — using Argus repo working tree (not comparable to a single increment baseline)"
    )
    from argus.builder.execution_contract import _git_changed_paths

    paths, err = _git_changed_paths(rr)
    if err:
        out["error"] = err
        return out
    out["changed_files_argus_relative"] = paths
    out["changed_file_count"] = len(paths)
    out["limitations"].append(
        "diff_stat and truncated_diff omitted in fallback mode (use nested product git + invoke for full diff)"
    )
    _merge_parent_repo_paths_for_scope(out, rr)
    return out
