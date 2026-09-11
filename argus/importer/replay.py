"""Replay and drift inspection for importer-managed products (``raw_extensions.import_state``)."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from argus.importer.cache import ensure_git_repo_cache
from argus.importer.constants import build_exclude_list
from argus.importer.evaluate import (
    classify_first_pass_status,
    run_first_pass_evaluation,
    write_first_pass_summary,
)
from argus.importer.git_info import read_git_branch, read_git_commit
from argus.importer.import_state import (
    IMPORT_STATE_SCHEMA,
    build_import_state,
    extract_import_state,
    load_product_yaml_dict,
    patch_product_yaml_import_state,
    repo_relative_summary_path,
    utc_now_iso,
)
from argus.importer.status_cmd import default_cache_root
from argus.importer.sync import sync_tree
from argus.importer.url import parse_github_repo
from argus.products.loader import load_yaml_file
from argus.products.validate import validate_manifest


def _bool_coerce(val: Any, default: bool) -> bool:
    if isinstance(val, bool):
        return val
    return default


def validate_import_state_for_replay(ist: dict[str, Any] | None) -> list[str]:
    """
    Return human-readable errors; empty list means replay/drift can proceed.

    Stricter than manifest validation: requires fields needed to reconstruct excludes and cache.
    """
    errs: list[str] = []
    if not ist:
        return ["import_state missing"]
    if not isinstance(ist, dict):
        return ["import_state must be a mapping"]
    sch = ist.get("schema")
    if sch != IMPORT_STATE_SCHEMA:
        errs.append(f"import_state.schema must be {IMPORT_STATE_SCHEMA!r}, got {sch!r}")
    url = ist.get("source_repo_url")
    if not isinstance(url, str) or not str(url).strip():
        errs.append("import_state.source_repo_url must be a non-empty string")
    slug = ist.get("cache_slug")
    if not isinstance(slug, str) or not str(slug).strip():
        errs.append("import_state.cache_slug must be a non-empty string")
    sync_ex = ist.get("sync_excludes")
    if sync_ex is not None and not isinstance(sync_ex, list):
        errs.append("import_state.sync_excludes must be a list when present")
    extra = ist.get("extra_excludes")
    if extra is not None and not isinstance(extra, list):
        errs.append("import_state.extra_excludes must be a list when present")
    elif isinstance(extra, list):
        for i, x in enumerate(extra):
            if not isinstance(x, str):
                errs.append(f"import_state.extra_excludes[{i}] must be a string")
    ic = ist.get("imported_from_commit")
    if ic is not None and not isinstance(ic, str):
        errs.append("import_state.imported_from_commit must be a string when present")
    return errs


def exclude_list_from_import_state(ist: dict[str, Any]) -> list[str]:
    """
    Rebuild rsync exclude list from persisted import_state flags.

    Prefer ``sync_excludes`` when present and non-empty (exact list from last import);
    otherwise derive from boolean flags + ``extra_excludes`` (same as ``build_exclude_list``).
    """
    sync_ex = ist.get("sync_excludes")
    if isinstance(sync_ex, list) and len(sync_ex) > 0 and all(isinstance(x, str) for x in sync_ex):
        return list(sync_ex)
    extra = ist.get("extra_excludes")
    extra_list = [str(x) for x in extra] if isinstance(extra, list) else []
    return build_exclude_list(
        include_cursor=_bool_coerce(ist.get("include_cursor"), False),
        include_local_db=_bool_coerce(ist.get("include_local_db_artifacts"), False),
        exclude_node_artifacts=_bool_coerce(ist.get("exclude_node_artifacts"), True),
        extra=extra_list,
        preserve_product_git=_bool_coerce(ist.get("preserve_product_git"), True),
    )


def reconstruct_import_argv(
    product_id: str,
    ist: dict[str, Any],
    *,
    cache_dir: str | None = None,
    no_delete: bool = False,
    skip_first_pass: bool = False,
    no_uv: bool = False,
) -> list[str]:
    """
    Reconstruct argv equivalent to a full ``import_main`` invocation (for logging and tests).

    Omits ``--repo-url`` when only flags matter; callers should prepend
    ``--repo-url <source_repo_url> --product-id …``.
    """
    url = str(ist.get("source_repo_url") or "").strip()
    out: list[str] = ["--repo-url", url, "--product-id", product_id]
    if cache_dir:
        out.extend(["--cache-dir", cache_dir])
    if no_delete:
        out.append("--no-delete")
    if _bool_coerce(ist.get("include_cursor"), False):
        out.append("--include-cursor")
    if _bool_coerce(ist.get("include_local_db_artifacts"), False):
        out.append("--include-local-db-artifacts")
    if not _bool_coerce(ist.get("exclude_node_artifacts"), True):
        out.append("--no-exclude-node-artifacts")
    for pat in ist.get("extra_excludes") or []:
        if isinstance(pat, str) and pat.strip():
            out.extend(["--extra-exclude", pat])
    if skip_first_pass:
        out.append("--skip-first-pass")
    if no_uv:
        out.append("--no-uv")
    if not _bool_coerce(ist.get("preserve_product_git"), True):
        out.append("--no-preserve-product-git")
    return out


def git_cat_file_commit_exists(repo: Path, rev: str) -> bool:
    r = rev.strip()
    if not r:
        return False
    spec = f"{r}^{{commit}}"
    proc = subprocess.run(
        ["git", "-C", str(repo), "cat-file", "-e", spec],
        capture_output=True,
        text=True,
    )
    return proc.returncode == 0


def checkout_cache_revision(repo: Path, rev: str) -> tuple[bool, str | None]:
    """
    ``git checkout --detach <rev>`` (or branch). Returns (ok, error_message).
    """
    r = rev.strip()
    if not r:
        return False, "empty revision"
    proc = subprocess.run(
        ["git", "-C", str(repo), "checkout", "--force", r],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()
        return False, err or f"git checkout failed (exit {proc.returncode})"
    return True, None


def inspect_import_drift(
    repo_root: Path,
    product_id: str,
    *,
    fetch_cache: bool = False,
) -> dict[str, Any]:
    """
    Read-only drift report (no product sync). Optionally ``git fetch`` in cache when ``fetch_cache``.

    Compares remembered ``imported_from_commit`` to current cache HEAD (local clone).
    """
    repo_root = repo_root.resolve()
    product_root = (repo_root / "products" / product_id).resolve()
    py_path = product_root / "product.yaml"

    out: dict[str, Any] = {
        "product_id": product_id,
        "product_yaml_path": str(py_path),
        "import_state_present": False,
        "import_state_complete": False,
        "import_state_errors": [],
        "source_repo_url": None,
        "imported_from_branch_stored": None,
        "imported_from_commit_stored": None,
        "imported_at_utc": None,
        "cache_path": None,
        "cache_exists": False,
        "cache_fetch_ran": False,
        "cache_head_commit": None,
        "cache_head_matches_stored_commit": None,
        "cache_has_stored_commit": None,
        "drift_summary": "",
    }

    data, err = load_product_yaml_dict(py_path)
    if err or data is None:
        out["import_state_errors"] = [f"cannot read product.yaml: {err or 'unknown'}"]
        out["drift_summary"] = "product.yaml missing or unreadable"
        return out

    ist = extract_import_state(data)
    out["import_state_present"] = ist is not None
    errs = validate_import_state_for_replay(ist)
    out["import_state_errors"] = errs
    out["import_state_complete"] = len(errs) == 0 and ist is not None
    if not ist:
        out["drift_summary"] = "raw_extensions.import_state missing"
        return out
    if errs:
        out["drift_summary"] = "import_state incomplete or invalid: " + "; ".join(errs[:3])
        return out

    out["source_repo_url"] = ist.get("source_repo_url")
    out["imported_from_branch_stored"] = ist.get("imported_from_branch")
    out["imported_at_utc"] = ist.get("imported_at_utc")
    stored_commit = ist.get("imported_from_commit")
    if isinstance(stored_commit, str) and stored_commit.strip():
        out["imported_from_commit_stored"] = stored_commit.strip()
    else:
        out["imported_from_commit_stored"] = None

    slug = str(ist.get("cache_slug") or "").strip()
    cache_path = (default_cache_root(repo_root) / slug).resolve()
    out["cache_path"] = str(cache_path)
    out["cache_exists"] = cache_path.is_dir() and (cache_path / ".git").is_dir()

    if not out["cache_exists"]:
        out["drift_summary"] = "cache clone missing — cannot compare HEAD to stored commit"
        return out

    if fetch_cache:
        subprocess.run(
            ["git", "-C", str(cache_path), "fetch", "--all", "--prune"],
            capture_output=True,
            text=True,
        )
        out["cache_fetch_ran"] = True

    head = read_git_commit(cache_path)
    out["cache_head_commit"] = head

    sc = out["imported_from_commit_stored"]
    if head and sc:
        full_match = len(head) >= 7 and len(sc) >= 7 and (head == sc or head.startswith(sc[:7]) or sc.startswith(head[:7]))
        out["cache_head_matches_stored_commit"] = bool(full_match)
    elif head and not sc:
        out["cache_head_matches_stored_commit"] = None
        out["drift_summary"] = "no imported_from_commit in import_state — re-run import to pin commit"
        return out
    else:
        out["cache_head_matches_stored_commit"] = None

    if sc:
        out["cache_has_stored_commit"] = git_cat_file_commit_exists(cache_path, sc)
    else:
        out["cache_has_stored_commit"] = None

    if head and sc:
        if out.get("cache_head_matches_stored_commit"):
            out["drift_summary"] = "cache HEAD matches stored imported_from_commit"
        else:
            out["drift_summary"] = (
                "cache HEAD differs from stored imported_from_commit "
                "(local clone moved forward or different branch; use replay to sync product tree to stored commit)"
            )
    elif not head:
        out["drift_summary"] = "could not read cache HEAD"

    return out


def run_import_replay(
    repo_root: Path,
    product_id: str,
    *,
    cache_dir: Path | None = None,
    no_delete: bool = False,
    skip_first_pass: bool = False,
    no_uv: bool = False,
    dry_run: bool = False,
) -> tuple[int, dict[str, Any]]:
    """
    Re-sync product tree from cached GitHub clone using ``import_state``.

    Updates ``import_state`` with new ``imported_at_utc`` and current branch/commit after sync.
    """
    repo_root = repo_root.resolve()
    product_root = (repo_root / "products" / product_id).resolve()
    py_path = product_root / "product.yaml"

    report: dict[str, Any] = {
        "dry_run": dry_run,
        "product_id": product_id,
        "product_root": str(product_root),
    }

    data, err = load_product_yaml_dict(py_path)
    if err or data is None:
        report["error"] = err or "cannot load product.yaml"
        return 2, report

    ist = extract_import_state(data)
    v_errs = validate_import_state_for_replay(ist)
    if v_errs:
        report["import_state_errors"] = v_errs
        report["error"] = "invalid import_state"
        return 3, report

    assert ist is not None
    argv_equiv = reconstruct_import_argv(
        product_id,
        ist,
        cache_dir=str(cache_dir) if cache_dir else None,
        no_delete=no_delete,
        skip_first_pass=skip_first_pass,
        no_uv=no_uv,
    )
    report["reconstructed_argv"] = argv_equiv

    if dry_run:
        report["message"] = "dry_run: no sync performed"
        return 0, report

    try:
        gh = parse_github_repo(str(ist.get("source_repo_url")))
    except ValueError as e:
        report["error"] = str(e)
        return 2, report

    cache_root = Path(cache_dir) if cache_dir else default_cache_root(repo_root)
    clone_path = ensure_git_repo_cache(cache_root, gh)

    stored_commit = ist.get("imported_from_commit")
    checkout_note: str | None = None
    if isinstance(stored_commit, str) and stored_commit.strip():
        ok, cerr = checkout_cache_revision(clone_path, stored_commit.strip())
        if not ok:
            checkout_note = f"checkout {stored_commit!r} failed: {cerr}; syncing from current HEAD"
            report["checkout_warning"] = checkout_note

    excludes = exclude_list_from_import_state(ist)
    report["exclude_count"] = len(excludes)

    product_root.mkdir(parents=True, exist_ok=True)
    sync_tree(clone_path, product_root, excludes=excludes, delete=not no_delete)

    branch = read_git_branch(clone_path)
    commit = read_git_commit(clone_path)
    imported_at = utc_now_iso()

    exclude_node = _bool_coerce(ist.get("exclude_node_artifacts"), True)
    extra_ex = [str(x) for x in (ist.get("extra_excludes") or []) if isinstance(x, str)]

    if skip_first_pass:
        new_ist = build_import_state(
            source_repo_url=str(ist.get("source_repo_url")),
            cache_slug=str(ist.get("cache_slug")),
            sync_excludes=excludes,
            include_cursor=_bool_coerce(ist.get("include_cursor"), False),
            include_local_db_artifacts=_bool_coerce(ist.get("include_local_db_artifacts"), False),
            exclude_node_artifacts=exclude_node,
            extra_excludes=extra_ex,
            imported_from_branch=branch,
            imported_from_commit=commit,
            imported_at_utc=imported_at,
            first_pass_ran=bool(ist.get("first_pass_ran")),
            first_pass_status=str(ist.get("first_pass_status") or "pending"),
            first_pass_summary_path=ist.get("first_pass_summary_path"),
            first_pass_command_errors=ist.get("first_pass_command_errors") if ist.get("first_pass_command_errors") else None,
            evaluation_error=ist.get("evaluation_error") if isinstance(ist.get("evaluation_error"), str) else None,
            preserve_product_git=_bool_coerce(ist.get("preserve_product_git"), True),
        )
    else:
        fp = run_first_pass_evaluation(repo_root, product_id, use_uv=not no_uv)
        fp_status = classify_first_pass_status(skipped=False, metrics=fp)
        summary_rel = repo_relative_summary_path(product_id)
        write_first_pass_summary(
            product_root,
            repo_root,
            product_id,
            fp,
            import_instrumentation={"replay": True, "product_id": product_id},
            use_uv=not no_uv,
        )
        new_ist = build_import_state(
            source_repo_url=str(ist.get("source_repo_url")),
            cache_slug=str(ist.get("cache_slug")),
            sync_excludes=excludes,
            include_cursor=_bool_coerce(ist.get("include_cursor"), False),
            include_local_db_artifacts=_bool_coerce(ist.get("include_local_db_artifacts"), False),
            exclude_node_artifacts=exclude_node,
            extra_excludes=extra_ex,
            imported_from_branch=branch,
            imported_from_commit=commit,
            imported_at_utc=imported_at,
            first_pass_ran=True,
            first_pass_status=fp_status,
            first_pass_summary_path=summary_rel,
            first_pass_command_errors=fp.errors if fp.errors else None,
            evaluation_error=fp.evaluation_error,
            preserve_product_git=_bool_coerce(ist.get("preserve_product_git"), True),
        )
        report["first_pass_status"] = fp_status

    try:
        patch_product_yaml_import_state(product_root, new_ist)
    except ValueError as e:
        report["error"] = f"patch import_state: {e}"
        return 3, report

    raw, err2 = load_yaml_file(py_path)
    if err2 or raw is None:
        report["validation_skipped"] = err2
    else:
        val = validate_manifest(
            raw,
            repo_root=repo_root,
            product_root=product_root,
            config_path=py_path,
        )
        report["validation_errors"] = val.errors
        if val.errors:
            report["error"] = "product.yaml validation failed after replay"
            return 3, report

    report["import_state_updated"] = True
    report["cache_clone_path"] = str(clone_path)
    if checkout_note:
        report["checkout_warning"] = checkout_note
    return 0, report


__all__ = [
    "checkout_cache_revision",
    "exclude_list_from_import_state",
    "git_cat_file_commit_exists",
    "inspect_import_drift",
    "reconstruct_import_argv",
    "run_import_replay",
    "validate_import_state_for_replay",
]
