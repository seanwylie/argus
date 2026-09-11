"""
Reconcile-time execution outcome for ``content_slot`` Builder increments.

Dispatched from :mod:`argus.builder.contract_registry` when
:func:`~argus.builder.contract_registry.resolve_reconcile_outcome_kind` yields ``content_slot``.

Classifies whether the **prior declared increment** (``prior_resolved_target`` / task) plausibly
finished on disk, separately from path/semantic scope.

Honest limits:

* Filesystem checks are **evidence**, not proof of quality.
* Invoke ``invocation_status`` is recorded fact; exit 0 does not mean correct content.
* Without a successful invoke record, Argus will not report **completed** (max **partial** if files look done).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

EXECUTION_OUTCOME_SCHEMA = "argus.builder.execution_outcome.v1"

OUTCOME_BREACHED = "breached"
OUTCOME_BLOCKED = "blocked"
OUTCOME_PARTIAL = "partial"
OUTCOME_COMPLETED = "completed"
OUTCOME_UNKNOWN = "unknown"


def _products_base(repo_root: Path, products_dir: Path | None) -> Path:
    if products_dir is not None:
        return (repo_root / products_dir).resolve()
    return (repo_root / "products").resolve()


def _slot_paths(
    repo_root: Path,
    product_id: str,
    products_dir: Path | None,
    slot_id: str,
) -> tuple[Path, Path]:
    base = _products_base(repo_root, products_dir) / product_id
    sj = base / "content" / "slots" / f"{slot_id}.json"
    html = base / "app" / "site" / "slot" / f"{slot_id}.html"
    return sj, html


def assess_content_slot_increment_files(
    repo_root: Path,
    product_id: str,
    products_dir: Path | None,
    slot_id: str,
) -> dict[str, Any]:
    """
    Inspect expected content-slot artifacts under the product tree.

    Returns structured booleans + paths (repo-relative where possible).
    """
    rr = repo_root.resolve()
    sj, html = _slot_paths(repo_root, product_id, products_dir, slot_id)

    def _rel(p: Path) -> str:
        try:
            return p.resolve().relative_to(rr).as_posix()
        except ValueError:
            return str(p)

    out: dict[str, Any] = {
        "slot_json_path": _rel(sj),
        "slot_html_path": _rel(html),
        "slot_json_present": False,
        "slot_json_valid_non_empty": False,
        "slot_html_present": False,
        "slot_html_non_trivial": False,
    }

    if sj.is_file():
        out["slot_json_present"] = True
        try:
            raw = sj.read_text(encoding="utf-8")
            if raw.strip():
                data = json.loads(raw)
                if isinstance(data, dict) and len(data) > 0:
                    # Minimal structural signal (not content QA).
                    blob = json.dumps(data)
                    if len(blob) > 40 or "schema" in data or "title" in data:
                        out["slot_json_valid_non_empty"] = True
        except (OSError, json.JSONDecodeError):
            pass

    if html.is_file():
        out["slot_html_present"] = True
        try:
            if html.stat().st_size >= 80:
                out["slot_html_non_trivial"] = True
        except OSError:
            pass

    return out


def _increment_is_content_slot(
    prior: dict[str, Any] | None,
    task_data: dict[str, Any] | None,
) -> bool:
    for src in (
        prior,
        task_data.get("resolved_target") if isinstance(task_data, dict) else None,
    ):
        if isinstance(src, dict) and src.get("target_type") == "content_slot":
            return True
    ec = task_data.get("execution_contract") if isinstance(task_data, dict) else None
    if isinstance(ec, dict) and ec.get("contract_kind") == "content_slot":
        return True
    return False


def _resolve_increment_id(
    prior: dict[str, Any] | None,
    task_data: dict[str, Any] | None,
) -> str | None:
    if isinstance(prior, dict) and prior.get("id"):
        return str(prior.get("id")).strip() or None
    rt = task_data.get("resolved_target") if isinstance(task_data, dict) else None
    if isinstance(rt, dict) and rt.get("id"):
        return str(rt.get("id")).strip() or None
    return None


def derive_content_slot_execution_outcome(
    repo_root: Path,
    product_id: str,
    *,
    products_dir: Path | None,
    scope_check: dict[str, Any] | None,
    invoke_data: dict[str, Any] | None,
    prior_resolved_target: dict[str, Any] | None,
    task_data: dict[str, Any] | None,
) -> dict[str, Any]:
    """
    Combine scope, invoke status, and filesystem evidence into a single outcome label.

    Precedence: **breached** (scope) → **blocked** (failed invoke) → **completed** / **partial** /
    **unknown** from evidence + invoke attestation rules.
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

    if scope_check and scope_check.get("scope_breach"):
        base["outcome"] = OUTCOME_BREACHED
        base["reasons"].append("scope_breach: path or semantic scope check failed")
        return base

    if not _increment_is_content_slot(prior_resolved_target, task_data):
        base["reasons"].append("target_type is not content_slot (or no content_slot contract_kind) — not classified")
        return base

    sid = _resolve_increment_id(prior_resolved_target, task_data)
    if not sid:
        base["reasons"].append("no increment id (resolved_target.id) to assess")
        return base

    base["increment_id"] = sid
    evidence = assess_content_slot_increment_files(
        repo_root, product_id, products_dir, sid
    )
    base["file_evidence"] = evidence

    files_ok = bool(
        evidence.get("slot_json_valid_non_empty") and evidence.get("slot_html_non_trivial")
    )
    files_partial = bool(evidence.get("slot_json_valid_non_empty") or evidence.get("slot_html_present"))

    inv = invoke_data if isinstance(invoke_data, dict) else None
    if inv:
        inv_status = str(inv.get("invocation_status") or "").strip()
        base["invoke_attestation"] = {
            "invocation_status": inv_status,
            "mode": inv.get("mode"),
            "exit_code": inv.get("exit_code"),
        }
        # Optional: future structured fields from agent JSON — advisory only.
        if inv.get("agent_output_completion_claim") is not None:
            base["advisory_agent_claims"] = {
                "completion_claim": inv.get("agent_output_completion_claim"),
                "note": "advisory only; not used for completed classification",
            }

        if inv_status == "failed":
            base["outcome"] = OUTCOME_BLOCKED
            err = inv.get("error")
            if err:
                base["reasons"].append(f"invoke failed: {str(err)[:500]}")
            else:
                base["reasons"].append("invoke invocation_status=failed")
            return base

        if inv_status == "ok":
            if files_ok:
                base["outcome"] = OUTCOME_COMPLETED
                base["reasons"].append(
                    "invoke ok and slot JSON + slot HTML present with minimal structural checks"
                )
            else:
                base["outcome"] = OUTCOME_PARTIAL
                if not evidence.get("slot_json_valid_non_empty"):
                    base["reasons"].append("invoke ok but slot JSON missing or failed JSON/size checks")
                elif not evidence.get("slot_html_non_trivial"):
                    base["reasons"].append("invoke ok but slot HTML missing or too small")
            return base

        # not_executed (or other)
        if files_ok:
            base["outcome"] = OUTCOME_PARTIAL
            base["reasons"].append(
                "artifacts look present on disk but invoke was not successful execution "
                f"(invocation_status={inv_status!r}) — cannot attest completed"
            )
        elif files_partial:
            base["outcome"] = OUTCOME_PARTIAL
            base["reasons"].append(
                f"incomplete filesystem evidence for increment; invocation_status={inv_status!r}"
            )
        else:
            base["outcome"] = OUTCOME_UNKNOWN
            base["reasons"].append(
                f"invoke not ok and no convincing artifacts (invocation_status={inv_status!r})"
            )
        return base

    # No invoke record
    if files_ok:
        base["outcome"] = OUTCOME_PARTIAL
        base["reasons"].append(
            "no invoke record; slot JSON/HTML look present — unverified (not completed)"
        )
    elif files_partial:
        base["outcome"] = OUTCOME_PARTIAL
        base["reasons"].append("no invoke record; partial filesystem evidence only")
    else:
        base["outcome"] = OUTCOME_UNKNOWN
        base["reasons"].append("no invoke record and insufficient filesystem evidence")

    return base


def outcome_blocks_prepare_next(outcome_payload: dict[str, Any] | None) -> bool:
    """Whether automatic prepare-next should be blocked (conservative)."""
    if not outcome_payload or not isinstance(outcome_payload, dict):
        return False
    o = outcome_payload.get("outcome")
    return o in (OUTCOME_BREACHED, OUTCOME_BLOCKED, OUTCOME_PARTIAL)
