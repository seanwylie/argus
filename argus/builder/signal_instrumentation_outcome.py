"""
Reconcile-time execution outcome for ``signal_instrumentation`` Builder increments.

Dispatched from :mod:`argus.builder.contract_registry` (non-``content_slot`` outcome path).

Uses scope + invoke + git diff **and** optional on-disk postconditions declared on the contract
(``expected_product_paths_exist``) plus optional overlap with ``instrumentation_touch_paths``.
Does **not** verify that telemetry fires or is semantically correct at runtime.
"""

from __future__ import annotations

import fnmatch
from pathlib import Path
from typing import Any

from argus.builder.content_slot_outcome import (
    EXECUTION_OUTCOME_SCHEMA,
    OUTCOME_BLOCKED,
    OUTCOME_BREACHED,
    OUTCOME_COMPLETED,
    OUTCOME_PARTIAL,
    OUTCOME_UNKNOWN,
)


def _is_signal_instrumentation(task_data: dict[str, Any] | None) -> bool:
    if not isinstance(task_data, dict):
        return False
    rt = task_data.get("resolved_target")
    if isinstance(rt, dict) and rt.get("target_type") == "signal_instrumentation":
        return True
    ec = task_data.get("execution_contract")
    if isinstance(ec, dict) and ec.get("contract_kind") == "signal_instrumentation":
        return True
    return False


def _products_base(repo_root: Path, products_dir: Path | None) -> Path:
    if products_dir is not None:
        return (repo_root / products_dir).resolve()
    return (repo_root / "products").resolve()


def _changed_product_relative(
    changed_files_argus_relative: list[str] | None, product_id: str
) -> list[str]:
    prefix = f"products/{product_id}/"
    out: list[str] = []
    for p in changed_files_argus_relative or []:
        n = str(p).replace("\\", "/")
        if n.startswith(prefix):
            out.append(n[len(prefix) :].lstrip("/"))
    return out


def _path_under_product(base: Path, rel: str) -> Path | None:
    """Resolve ``rel`` under ``base``; return None if traversal escapes."""
    rel = rel.replace("\\", "/").strip().lstrip("/")
    if not rel or rel.startswith("..") or "/../" in f"/{rel}/":
        return None
    cand = (base / rel).resolve()
    try:
        cand.relative_to(base.resolve())
    except ValueError:
        return None
    return cand


def _evaluate_postconditions(
    repo_root: Path,
    product_id: str,
    products_dir: Path | None,
    rel_paths: list[str],
) -> tuple[list[str], list[str]]:
    """Return (present, missing) product-relative paths."""
    base = _products_base(repo_root, products_dir) / product_id
    present: list[str] = []
    missing: list[str] = []
    for rel in rel_paths:
        p = _path_under_product(base, rel)
        if p is None:
            missing.append(rel)
            continue
        if p.is_file() or p.is_dir():
            present.append(rel)
        else:
            missing.append(rel)
    return present, missing


def _touch_overlap(product_changed: list[str], touch_hints: list[str]) -> bool:
    """Each hint must match at least one changed product-relative path (fnmatch or exact)."""
    if not touch_hints:
        return True
    for hint in touch_hints:
        hint_n = hint.replace("\\", "/").strip().lstrip("/")
        if not any(fnmatch.fnmatch(ch, hint_n) or ch == hint_n for ch in product_changed):
            return False
    return True


def derive_signal_instrumentation_execution_outcome(
    *,
    repo_root: Path,
    product_id: str,
    products_dir: Path | None,
    scope_check: dict[str, Any] | None,
    invoke_data: dict[str, Any] | None,
    task_data: dict[str, Any] | None,
    builder_diff_summary: dict[str, Any] | None,
) -> dict[str, Any]:
    """
    * **breached** — path or semantic scope failed.
    * **blocked** — invoke failed.
    * **completed** — invoke ok, diff shows changes, optional postconditions + touch hints satisfied.
    * **partial** — invoke ok but weak evidence (no diff, missing postconditions, or touch mismatch).
    * **unknown** — insufficient evidence.
    """
    base: dict[str, Any] = {
        "schema": EXECUTION_OUTCOME_SCHEMA,
        "outcome": OUTCOME_UNKNOWN,
        "reasons": [],
        "increment_id": None,
        "file_evidence": None,
        "invoke_attestation": None,
        "advisory_agent_claims": None,
    }

    if not _is_signal_instrumentation(task_data):
        base["reasons"].append(
            "not a signal_instrumentation task — use bug_fix or content_slot outcome classifier"
        )
        return base

    ec = task_data.get("execution_contract") if isinstance(task_data, dict) else None
    ec = ec if isinstance(ec, dict) else {}
    sid = str(ec.get("signal_id") or ec.get("increment_target_id") or "").strip()
    rt = task_data.get("resolved_target") if isinstance(task_data, dict) else None
    if not sid and isinstance(rt, dict) and rt.get("id"):
        sid = str(rt.get("id")).strip()
    base["increment_id"] = sid or None

    raw_ep = ec.get("expected_product_paths_exist")
    post_rel: list[str] = []
    if isinstance(raw_ep, list):
        post_rel = [str(x).strip().replace("\\", "/").lstrip("/") for x in raw_ep if str(x).strip()]
    raw_touch = ec.get("instrumentation_touch_paths")
    touch_hints: list[str] = []
    if isinstance(raw_touch, list):
        touch_hints = [
            str(x).strip().replace("\\", "/").lstrip("/") for x in raw_touch if str(x).strip()
        ]

    changed = int((builder_diff_summary or {}).get("changed_file_count") or 0)
    changed_paths = (builder_diff_summary or {}).get("changed_files_argus_relative")
    if not isinstance(changed_paths, list):
        changed_paths = []
    product_changed = _changed_product_relative(changed_paths, product_id)

    present_post, missing_post = _evaluate_postconditions(
        repo_root, product_id, products_dir, post_rel
    )
    touch_ok = _touch_overlap(product_changed, touch_hints)

    base["file_evidence"] = {
        "changed_file_count": changed,
        "source": (builder_diff_summary or {}).get("source"),
        "product_relative_changed_paths_sample": product_changed[:24],
        "expected_product_paths_exist": post_rel,
        "postcondition_paths_present": present_post,
        "postcondition_paths_missing": missing_post,
        "instrumentation_touch_paths": touch_hints,
        "instrumentation_touch_satisfied": touch_ok,
    }

    if scope_check and scope_check.get("scope_breach"):
        base["outcome"] = OUTCOME_BREACHED
        base["reasons"].append("scope_breach: path or semantic scope check failed")
        return base

    inv = invoke_data if isinstance(invoke_data, dict) else None
    if inv:
        inv_status = str(inv.get("invocation_status") or "").strip()
        base["invoke_attestation"] = {
            "invocation_status": inv_status,
            "mode": inv.get("mode"),
            "exit_code": inv.get("exit_code"),
        }
        if inv.get("agent_output_completion_claim") is not None:
            base["advisory_agent_claims"] = {
                "completion_claim": inv.get("agent_output_completion_claim"),
                "note": "advisory only; not used for completed classification",
            }

        if inv_status == "failed":
            base["outcome"] = OUTCOME_BLOCKED
            err = inv.get("error")
            base["reasons"].append(f"invoke failed: {str(err)[:500]}" if err else "invoke invocation_status=failed")
            return base

        if inv_status == "ok":
            if changed <= 0:
                base["outcome"] = OUTCOME_PARTIAL
                base["reasons"].append(
                    "invoke ok but no changed files vs baseline — instrumentation likely incomplete"
                )
                return base
            if missing_post:
                base["outcome"] = OUTCOME_PARTIAL
                base["reasons"].append(
                    f"invoke ok and diff present but postcondition paths missing: {missing_post[:8]}"
                )
                return base
            if not touch_ok:
                base["outcome"] = OUTCOME_PARTIAL
                base["reasons"].append(
                    "invoke ok but git diff does not overlap instrumentation_touch_paths as required"
                )
                return base
            base["outcome"] = OUTCOME_COMPLETED
            base["reasons"].append(
                "invoke ok, diff shows product changes, postconditions (if any) on disk, "
                "and touch hints (if any) satisfied — does not assert runtime telemetry correctness"
            )
            return base

        # invoke exists but not ok / unknown status
        base["outcome"] = OUTCOME_PARTIAL if changed > 0 else OUTCOME_UNKNOWN
        base["reasons"].append(
            f"invoke not clearly successful (invocation_status={inv_status!r}); "
            f"diff_changed={changed}"
        )
        return base

    # No invoke record
    if changed > 0 and not missing_post and touch_ok:
        base["outcome"] = OUTCOME_PARTIAL
        base["reasons"].append(
            "no invoke record; diff and postconditions/touch hints look satisfied — unverified (not completed)"
        )
    elif changed > 0:
        base["outcome"] = OUTCOME_PARTIAL
        base["reasons"].append(
            "no invoke record; diff present but postconditions or touch hints not fully satisfied"
        )
    else:
        base["outcome"] = OUTCOME_UNKNOWN
        base["reasons"].append("no invoke record and no diff changes")
    return base


__all__ = ["derive_signal_instrumentation_execution_outcome"]
