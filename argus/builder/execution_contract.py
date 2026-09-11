"""
Bounded Builder execution contracts (``argus.builder.execution_contract.v1``).

Implements per-kind **contract builders** (content slot, bug fix, signal instrumentation).
:mod:`argus.builder.contract_registry` dispatches to these via
:func:`~argus.builder.contract_registry.build_execution_contract_for_kind` so prepare and
reconcile share one code path for the embedded ``execution_contract``.

Human-facing markdown for ``argus builder prepare`` lives in
:mod:`argus.builder.next_expansion_prepare`, not here.

Prompt scaffolding and reconcile-time scope checks use the same deterministic rules so
“allowed scope” is not prompt-only fiction.
"""

from __future__ import annotations

import fnmatch
import json
import re
import subprocess
from pathlib import Path
from typing import Any

# Must stay local: ``next_expansion_prepare`` imports this module (content-slot contract).
_NEXT_EXPANSION_RELPATH = "content/next_expansion.json"


def _next_expansion_file_path(
    repo_root: Path, product_id: str, *, products_dir: Path | None
) -> Path:
    if products_dir is not None:
        base = (repo_root / products_dir).resolve()
    else:
        base = (repo_root / "products").resolve()
    return (base / product_id / _NEXT_EXPANSION_RELPATH).resolve()


def _products_base(repo_root: Path, products_dir: Path | None) -> Path:
    if products_dir is not None:
        return (repo_root / products_dir).resolve()
    return (repo_root / "products").resolve()


EXECUTION_CONTRACT_SCHEMA = "argus.builder.execution_contract.v1"
BUILDER_SCOPE_CHECK_SCHEMA_V2 = "argus.builder_scope_check.v2"


def group_site_stem_from_group_id(group_id: str) -> str:
    """
    Map a catalog group id to its site filename stem (e.g. ``group_01_beginnings`` → ``group_01``).
    """
    raw = str(group_id or "").strip()
    if not raw:
        return "group_01"
    parts = raw.split("_")
    if len(parts) >= 2 and parts[0] == "group" and parts[1].isdigit():
        return f"{parts[0]}_{parts[1]}"
    return raw.split("_")[0] if "_" in raw else raw


def slot_file_glob_for_same_group(tid: str) -> str | None:
    """``group_01_slot_05`` → ``content/slots/group_01_slot_*.json`` (same group only)."""
    t = str(tid or "").strip()
    m = re.match(r"^(group_\d+_slot)_\d+$", t)
    if not m:
        return None
    return f"content/slots/{m.group(1)}_*.json"


def build_content_slot_execution_contract(
    *,
    repo_root: Path,
    product_id: str,
    products_dir: Path | None,
    raw: dict[str, Any],
    pt: dict[str, Any],
) -> dict[str, Any]:
    """
    Deterministic scope model for one content-slot increment on a catalog-backed content site.

    Assumes the conventional site layout (``content/slots/``, ``app/site/slot/``,
    ``app/site/group/``). Paired with the content-slot markdown in
    :mod:`argus.builder.next_expansion_prepare` and :func:`evaluate_path_scope`.
    """
    tid = str(pt.get("id") or "").strip()
    group_id = str(pt.get("group_id") or "").strip()
    stem = group_site_stem_from_group_id(group_id)
    group_hub = f"app/site/group/{stem}.html"
    slot_glob = slot_file_glob_for_same_group(tid)

    allowed_exact = [
        f"content/slots/{tid}.json",
        "content/content_catalog.json",
        "content/next_expansion.json",
        f"app/site/slot/{tid}.html",
        group_hub,
        "app/site/groups.html",
        "app/site/index.html",
        "metrics/content_evidence.json",
        "metrics/operational_validation_metrics.json",
        "metrics/activity_validation_exercise.jsonl",
    ]

    allowed_patterns = [
        "app/site/slot/*.html",
    ]
    if slot_glob:
        allowed_patterns.append(slot_glob)

    rr = repo_root.resolve()
    product_root_posix = (_products_base(repo_root, products_dir) / product_id).resolve().relative_to(rr).as_posix()

    return {
        "schema": EXECUTION_CONTRACT_SCHEMA,
        "contract_kind": "content_slot",
        "product_id": product_id,
        "increment_target_id": tid,
        "group_id": group_id,
        "repo_product_root_relative": product_root_posix,
        "allowed_paths_exact": allowed_exact,
        "allowed_path_patterns": allowed_patterns,
        "next_expansion_policy": "do_not_change_primary_target",
        "next_expansion_policy_detail": (
            "Do not change `primary_target` in `content/next_expansion.json` during this run. "
            "One increment = one content slot; advancing the declared target is done by "
            "`argus builder reconcile --generate-next-expansion` (or prepare), not by editing "
            "`primary_target` to the next slot in the same agent session."
        ),
        "forbidden_absolute": [
            "Edits outside the Argus product directory for this product.",
            "Edits under `argus/` core, `docs/` tree, or other `products/<other_id>/` directories.",
            "Creating or switching work to another content slot beyond neighbor HTML/slot JSON touch-ups.",
            "Broad refactors, dependency upgrades, or style sweeps not required for this slot.",
            "Fake users, traffic, revenue, or readiness claims.",
        ],
        "success_conditions_summary": (
            f"Slot `{tid}` is truthfully outlined or better in catalog + slot JSON; "
            f"site pages and nav for this slot are consistent; signals/findings still honest."
        ),
        "stop_conditions_summary": (
            "Stop after this slot is done; do not start the next planned slot in the same run. "
            "If blocked (missing files, ambiguity), stop and report the blocker instead of improvising."
        ),
        "output_contract_summary": (
            "End with a short report: files touched, whether scope was respected, blockers, "
            "and explicit non-claims if work is partial."
        ),
    }


def build_bug_fix_execution_contract(
    *,
    repo_root: Path,
    product_id: str,
    products_dir: Path | None,
    raw: dict[str, Any],
    pt: dict[str, Any],
) -> dict[str, Any]:
    """
    Bounded scope for a single bug-fix increment (``contract_kind``: ``bug_fix``).

    ``primary_target`` must include ``allowed_paths_exact`` (non-empty list of paths relative
    to the product root). Optional ``allowed_path_patterns`` uses :func:`fnmatch` semantics.
    """
    bug_id = str(pt.get("id") or "").strip()
    if not bug_id:
        raise ValueError("bug_fix primary_target.id is required")

    raw_allowed = pt.get("allowed_paths_exact")
    if not isinstance(raw_allowed, list) or not raw_allowed:
        raise ValueError("bug_fix primary_target.allowed_paths_exact must be a non-empty list")
    allowed_exact: list[str] = []
    for p in raw_allowed:
        s = str(p).strip().replace("\\", "/").lstrip("/")
        if not s:
            raise ValueError("bug_fix allowed_paths_exact entries must be non-empty path strings")
        allowed_exact.append(s)

    patterns_raw = pt.get("allowed_path_patterns")
    patterns: list[str] = []
    if isinstance(patterns_raw, list):
        patterns = [str(p).strip() for p in patterns_raw if str(p).strip()]

    bug_statement = str(pt.get("bug_statement") or pt.get("rationale") or "").strip()
    success_condition = str(pt.get("success_condition") or "").strip()
    stop_condition = str(pt.get("stop_condition") or "").strip()
    extra_forbid = pt.get("forbidden_changes")
    forbid_lines: list[str] = [
        "Edits outside the Argus product directory for this product.",
        "Edits under `argus/` core, `docs/` tree, or other `products/<other_id>/` directories.",
    ]
    if isinstance(extra_forbid, list):
        forbid_lines.extend(str(x).strip() for x in extra_forbid if str(x).strip())
    else:
        forbid_lines.extend(
            [
                "Broad refactors, dependency upgrades, or style sweeps not required for this bug.",
                "Adjacent features or unrelated files — fix only what the bug requires.",
            ]
        )

    rr = repo_root.resolve()
    product_root_posix = (_products_base(repo_root, products_dir) / product_id).resolve().relative_to(rr).as_posix()

    return {
        "schema": EXECUTION_CONTRACT_SCHEMA,
        "contract_kind": "bug_fix",
        "product_id": product_id,
        "increment_target_id": bug_id,
        "bug_id": bug_id,
        "repo_product_root_relative": product_root_posix,
        "allowed_paths_exact": allowed_exact,
        "allowed_path_patterns": patterns,
        "next_expansion_policy": "do_not_change_primary_target",
        "next_expansion_policy_detail": (
            "Do not change `primary_target` in `content/next_expansion.json` during this run. "
            "Replacing or advancing the declared bug target is owned by prepare/reconcile — "
            "not by editing `primary_target` mid-session."
        ),
        "bug_statement": bug_statement,
        "success_condition": success_condition,
        "stop_condition": stop_condition,
        "forbidden_absolute": forbid_lines,
        "success_conditions_summary": (
            success_condition
            if success_condition
            else f"Bug `{bug_id}` is addressed within allowed paths; no unrelated churn."
        ),
        "stop_conditions_summary": (
            stop_condition
            if stop_condition
            else "Stop after the minimal fix; do not refactor broadly or pick up extra work."
        ),
        "output_contract_summary": (
            "End with a short report: files touched, whether scope was respected, blockers, "
            "and explicit non-claims if work is partial."
        ),
    }


def build_signal_instrumentation_execution_contract(
    *,
    repo_root: Path,
    product_id: str,
    products_dir: Path | None,
    raw: dict[str, Any],
    pt: dict[str, Any],
) -> dict[str, Any]:
    """
    Bounded scope for signal/instrumentation work (``contract_kind``: ``signal_instrumentation``).

    ``primary_target`` must include ``allowed_paths_exact`` (non-empty list, product-relative paths).
    Optional ``expected_product_paths_exist`` lists files/dirs under the product that reconcile
    can check for existence. Optional ``instrumentation_touch_paths`` (product-relative, fnmatch)
    refines outcome: at least one git-touched file should match when the list is non-empty.
    """
    sig_id = str(pt.get("id") or "").strip()
    if not sig_id:
        raise ValueError("signal_instrumentation primary_target.id is required")

    raw_allowed = pt.get("allowed_paths_exact")
    if not isinstance(raw_allowed, list) or not raw_allowed:
        raise ValueError(
            "signal_instrumentation primary_target.allowed_paths_exact must be a non-empty list"
        )
    allowed_exact: list[str] = []
    for p in raw_allowed:
        s = str(p).strip().replace("\\", "/").lstrip("/")
        if not s:
            raise ValueError(
                "signal_instrumentation allowed_paths_exact entries must be non-empty path strings"
            )
        allowed_exact.append(s)

    patterns_raw = pt.get("allowed_path_patterns")
    patterns: list[str] = []
    if isinstance(patterns_raw, list):
        patterns = [str(p).strip() for p in patterns_raw if str(p).strip()]

    signal_statement = str(
        pt.get("signal_statement")
        or pt.get("instrumentation_objective")
        or pt.get("rationale")
        or ""
    ).strip()
    success_condition = str(pt.get("success_condition") or "").strip()
    stop_condition = str(pt.get("stop_condition") or "").strip()

    expected_exist: list[str] = []
    raw_exist = pt.get("expected_product_paths_exist")
    if isinstance(raw_exist, list):
        for x in raw_exist:
            s = str(x).strip().replace("\\", "/").lstrip("/")
            if s:
                expected_exist.append(s)

    touch_paths: list[str] = []
    raw_touch = pt.get("instrumentation_touch_paths")
    if isinstance(raw_touch, list):
        for x in raw_touch:
            s = str(x).strip().replace("\\", "/").lstrip("/")
            if s:
                touch_paths.append(s)

    extra_forbid = pt.get("forbidden_changes")
    forbid_lines: list[str] = [
        "Edits outside the Argus product directory for this product.",
        "Edits under `argus/` core, `docs/` tree, or other `products/<other_id>/` directories.",
        "Broad observability redesigns, new metrics frameworks, or cross-product signal refactors.",
        "Feature creep unrelated to wiring the declared signal/instrumentation surface.",
    ]
    if isinstance(extra_forbid, list):
        forbid_lines.extend(str(x).strip() for x in extra_forbid if str(x).strip())

    rr = repo_root.resolve()
    product_root_posix = (_products_base(repo_root, products_dir) / product_id).resolve().relative_to(rr).as_posix()

    return {
        "schema": EXECUTION_CONTRACT_SCHEMA,
        "contract_kind": "signal_instrumentation",
        "product_id": product_id,
        "increment_target_id": sig_id,
        "signal_id": sig_id,
        "repo_product_root_relative": product_root_posix,
        "allowed_paths_exact": allowed_exact,
        "allowed_path_patterns": patterns,
        "next_expansion_policy": "do_not_change_primary_target",
        "next_expansion_policy_detail": (
            "Do not change `primary_target` in `content/next_expansion.json` during this run. "
            "Advancing the declared instrumentation target is owned by prepare/reconcile — "
            "not by editing `primary_target` mid-session."
        ),
        "signal_statement": signal_statement,
        "success_condition": success_condition,
        "stop_condition": stop_condition,
        "expected_product_paths_exist": expected_exist,
        "instrumentation_touch_paths": touch_paths,
        "forbidden_absolute": forbid_lines,
        "success_conditions_summary": (
            success_condition
            if success_condition
            else (
                f"Instrumentation `{sig_id}` is wired within allowed paths; "
                f"signals remain honest and bounded."
            )
        ),
        "stop_conditions_summary": (
            stop_condition
            if stop_condition
            else (
                "Stop after the minimal instrumentation change; do not refactor callers or "
                "expand observability beyond this task."
            )
        ),
        "output_contract_summary": (
            "End with a short report: files touched, what signal surface was added/changed, "
            "whether postcondition paths exist, blockers, and explicit non-claims if partial."
        ),
    }


def _norm_rel_path(s: str) -> str:
    return s.replace("\\", "/").strip()


def _norm_primary_target(d: dict[str, Any] | None) -> dict[str, Any] | None:
    if not d or not isinstance(d, dict):
        return None
    return {
        "id": d.get("id"),
        "target_type": d.get("target_type"),
        "group_id": d.get("group_id"),
    }


def _primary_target_equal(a: dict[str, Any] | None, b: dict[str, Any] | None) -> bool:
    if not a or not b:
        return False
    return (
        a.get("id") == b.get("id")
        and a.get("target_type") == b.get("target_type")
        and a.get("group_id") == b.get("group_id")
    )


def path_allowed_by_contract(rel_to_product: str, contract: dict[str, Any]) -> bool:
    """``rel_to_product`` uses forward slashes, no ``products/`` prefix."""
    rel = _norm_rel_path(rel_to_product).lstrip("/")
    if rel in (contract.get("allowed_paths_exact") or []):
        return True
    for pat in contract.get("allowed_path_patterns") or []:
        if fnmatch.fnmatch(rel, str(pat)):
            return True
    return False


def _git_available(repo_root: Path) -> bool:
    return (repo_root / ".git").is_dir() and subprocess.run(
        ["git", "--version"],
        capture_output=True,
        timeout=5,
    ).returncode == 0


def _git_changed_paths(repo_root: Path) -> tuple[list[str], str | None]:
    """Paths relative to repo root (posix). Union of staged, unstaged, untracked."""
    if not _git_available(repo_root):
        return [], "not_git_repo_or_no_git"
    out: set[str] = set()
    for extra in ([], ["--cached"]):
        p = subprocess.run(
            ["git", "-C", str(repo_root), "diff", "--name-only", *extra, "HEAD"],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if p.returncode != 0:
            return [], (p.stderr or p.stdout or "git diff failed")[:400]
        for line in (p.stdout or "").splitlines():
            line = line.strip()
            if line:
                out.add(_norm_rel_path(line))
    u = subprocess.run(
        ["git", "-C", str(repo_root), "ls-files", "--others", "--exclude-standard"],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if u.returncode == 0:
        for line in (u.stdout or "").splitlines():
            line = line.strip()
            if line:
                out.add(_norm_rel_path(line))
    return sorted(out), None


def _read_json_dict(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return raw if isinstance(raw, dict) else None


def evaluate_semantic_primary_target_scope(
    repo_root: Path,
    *,
    product_id: str,
    products_dir: Path | None,
    task_data: dict[str, Any] | None,
    execution_contract: dict[str, Any] | None,
) -> dict[str, Any]:
    """
    Compare ``resolved_target`` in ``builder_task.json`` to ``primary_target`` in on-disk
    ``content/next_expansion.json`` when the contract requires **do_not_change_primary_target**.

    Catches in-path-but-wrong-field edits that path-level allow-lists cannot see.
    """
    out: dict[str, Any] = {
        "status": "skipped",
        "semantic_scope_breach": False,
        "semantic_breach_reasons": [],
        "sensitive_file_repo_relative": None,
        "expected_primary_target": None,
        "observed_primary_target": None,
    }
    if not execution_contract:
        out["status"] = "no_execution_contract"
        return out
    if execution_contract.get("next_expansion_policy") != "do_not_change_primary_target":
        out["status"] = "policy_not_applicable"
        return out
    if not task_data or not isinstance(task_data.get("resolved_target"), dict):
        out["status"] = "no_resolved_target_in_task"
        return out

    expected = _norm_primary_target(task_data["resolved_target"])
    if not expected or not expected.get("id"):
        out["status"] = "invalid_resolved_target_in_task"
        return out

    ne_path = _next_expansion_file_path(repo_root, product_id, products_dir=products_dir)
    rr = repo_root.resolve()
    try:
        rel_sf = ne_path.resolve().relative_to(rr).as_posix()
    except ValueError:
        rel_sf = str(ne_path)
    out["sensitive_file_repo_relative"] = rel_sf

    ne_raw = _read_json_dict(ne_path)
    pt = ne_raw.get("primary_target") if ne_raw else None
    observed = _norm_primary_target(pt if isinstance(pt, dict) else None)

    out["expected_primary_target"] = expected
    out["observed_primary_target"] = observed

    if observed is None:
        out["status"] = "missing_or_invalid_primary_target"
        out["semantic_scope_breach"] = True
        out["semantic_breach_reasons"].append(
            "semantic_scope: content/next_expansion.json missing or has no valid primary_target object"
        )
        return out

    if not _primary_target_equal(expected, observed):
        out["status"] = "primary_target_mismatch"
        out["semantic_scope_breach"] = True
        out["semantic_breach_reasons"].append(
            "semantic_scope: primary_target does not match builder_task.json resolved_target "
            f"(expected id={expected.get('id')!r} type={expected.get('target_type')!r} group_id={expected.get('group_id')!r}; "
            f"observed id={observed.get('id')!r} type={observed.get('target_type')!r} group_id={observed.get('group_id')!r})"
        )
        return out

    out["status"] = "ok"
    return out


def build_builder_scope_check(
    repo_root: Path,
    *,
    product_id: str,
    products_dir: Path | None,
    task_data: dict[str, Any] | None,
    changed_paths_repo_relative: list[str] | None = None,
) -> dict[str, Any]:
    """
    Path-level + semantic primary_target checks for reconcile records (``argus.builder_scope_check.v2``).

    ``scope_breach`` is True if either path or semantic scope failed (prepare-next should not run).
    """
    ec = task_data.get("execution_contract") if isinstance(task_data, dict) else None
    path = evaluate_path_scope(
        repo_root,
        product_id=product_id,
        products_dir=products_dir,
        execution_contract=ec,
        changed_paths_repo_relative=changed_paths_repo_relative,
    )
    sem = evaluate_semantic_primary_target_scope(
        repo_root,
        product_id=product_id,
        products_dir=products_dir,
        task_data=task_data,
        execution_contract=ec,
    )

    path_breach = bool(path.get("scope_breach"))
    sem_breach = bool(sem.get("semantic_scope_breach"))

    reasons: list[str] = []
    reasons.extend(path.get("breach_reasons") or [])
    reasons.extend(sem.get("semantic_breach_reasons") or [])

    overall_breach = path_breach or sem_breach

    if overall_breach:
        st = "breach"
    elif path.get("status") == "no_contract_in_task":
        st = "incomplete_contract"
    elif path.get("status") == "git_unavailable":
        st = "git_unavailable"
    else:
        st = "ok"

    return {
        "schema": BUILDER_SCOPE_CHECK_SCHEMA_V2,
        "status": st,
        "path_scope": path,
        "semantic_scope": sem,
        "path_scope_breach": path_breach,
        "semantic_scope_breach": sem_breach,
        "scope_breach": overall_breach,
        "breach_reasons": reasons,
        "contract_schema": ec.get("schema") if isinstance(ec, dict) else None,
    }


def evaluate_path_scope(
    repo_root: Path,
    *,
    product_id: str,
    products_dir: Path | None,
    execution_contract: dict[str, Any] | None,
    changed_paths_repo_relative: list[str] | None = None,
) -> dict[str, Any]:
    """
    Compare git working tree changes to ``execution_contract`` allowed paths/patterns.

    Only paths under ``products/<product_id>/`` are evaluated; changes outside that tree
    count as **product-external** breach (Builder products should stay in-tree).

    Paths under ``argus/`` are classified separately (**argus_core_breach**) with reason prefix
    ``path_scope:modified_argus_core`` — product-scoped execution must not self-modify Argus.
    Other repo-root changes use ``path_scope:modified_non_product_root`` (config/, docs/, etc.).

    If ``changed_paths_repo_relative`` is set (from reconcile ``builder_diff_summary`` / invoke
    baseline diff), it is the canonical change list; otherwise Argus working-tree git inspection
    is used (see ``path_scope_source`` on the result).
    """
    rr = repo_root.resolve()
    prod_rel = (_products_base(repo_root, products_dir) / product_id).resolve().relative_to(rr)
    prefix = prod_rel.as_posix() + "/"
    product_root_posix = execution_contract.get("repo_product_root_relative") if execution_contract else prod_rel.as_posix()

    result: dict[str, Any] = {
        "schema": "argus.builder_scope_check.v1",
        "status": "skipped",
        "git_error": None,
        "product_id": product_id,
        "expected_product_root": product_root_posix,
        "changed_paths_repo_relative": [],
        "changed_paths_under_product": [],
        "paths_outside_product": [],
        "modified_argus_paths": [],
        "modified_non_product_paths": [],
        "argus_core_breach": False,
        "non_product_root_breach": False,
        "unexpected_under_product": [],
        "scope_breach": False,
        "breach_reasons": [],
        "contract_schema": execution_contract.get("schema") if execution_contract else None,
        "path_scope_source": None,
    }

    if not execution_contract:
        result["status"] = "no_contract_in_task"
        result["path_scope_source"] = None
        result["breach_reasons"].append("builder_task.json has no execution_contract — cannot verify scope")
        return result

    if changed_paths_repo_relative is not None:
        all_changed = list(changed_paths_repo_relative)
        result["path_scope_source"] = "builder_diff_summary"
    else:
        all_changed, err = _git_changed_paths(repo_root.resolve())
        if err:
            result["status"] = "git_unavailable"
            result["git_error"] = err
            result["path_scope_source"] = "argus_working_tree_unavailable"
            return result
        result["path_scope_source"] = "argus_working_tree"

    result["changed_paths_repo_relative"] = all_changed
    result["status"] = "ok"

    outside: list[str] = []
    under: list[str] = []
    unexpected: list[str] = []

    for p in all_changed:
        if not p.startswith(prefix):
            outside.append(p)
            continue
        rel = p[len(prefix) :]
        under.append(rel)
        if not path_allowed_by_contract(rel, execution_contract):
            unexpected.append(rel)

    result["paths_outside_product"] = outside
    result["changed_paths_under_product"] = under

    argus_paths = sorted(
        p for p in outside if p == "argus" or p.startswith("argus/")
    )
    non_argus_outside = sorted(
        p for p in outside if p != "argus" and not p.startswith("argus/")
    )
    result["modified_argus_paths"] = argus_paths
    result["modified_non_product_paths"] = non_argus_outside
    result["argus_core_breach"] = bool(argus_paths)
    result["non_product_root_breach"] = bool(non_argus_outside)

    if outside:
        result["scope_breach"] = True
        if argus_paths:
            result["breach_reasons"].append(
                "path_scope:modified_argus_core "
                f"({len(argus_paths)} path(s) under argus/ — product-scoped Builder must not modify Argus core)"
            )
        if non_argus_outside:
            result["breach_reasons"].append(
                "path_scope:modified_non_product_root "
                f"({len(non_argus_outside)} path(s) outside products/{product_id}/ "
                f"— e.g. config/, docs/, other products, repo root)"
            )
    if unexpected:
        result["unexpected_under_product"] = unexpected
        result["scope_breach"] = True
        result["breach_reasons"].append(
            f"Paths under product not in execution contract allow-list ({len(unexpected)} path(s))"
        )

    if not all_changed:
        result["note"] = "no_git_changes_detected"

    return result


__all__ = [
    "BUILDER_SCOPE_CHECK_SCHEMA_V2",
    "EXECUTION_CONTRACT_SCHEMA",
    "group_site_stem_from_group_id",
    "build_builder_scope_check",
    "build_bug_fix_execution_contract",
    "build_signal_instrumentation_execution_contract",
    "build_content_slot_execution_contract",
    "evaluate_path_scope",
    "evaluate_semantic_primary_target_scope",
    "path_allowed_by_contract",
    "slot_file_glob_for_same_group",
]
