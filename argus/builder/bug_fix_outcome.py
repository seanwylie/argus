"""
Reconcile-time execution outcome for ``bug_fix`` Builder increments.

Dispatched from :mod:`argus.builder.contract_registry` (non-``content_slot`` outcome path).

Conservative filesystem + diff evidence only — does not verify runtime correctness.
"""

from __future__ import annotations

from typing import Any

from argus.builder.content_slot_outcome import (
    EXECUTION_OUTCOME_SCHEMA,
    OUTCOME_BLOCKED,
    OUTCOME_BREACHED,
    OUTCOME_COMPLETED,
    OUTCOME_PARTIAL,
    OUTCOME_UNKNOWN,
)


def _is_bug_fix(task_data: dict[str, Any] | None) -> bool:
    if not isinstance(task_data, dict):
        return False
    rt = task_data.get("resolved_target")
    if isinstance(rt, dict) and rt.get("target_type") == "bug_fix":
        return True
    ec = task_data.get("execution_contract")
    if isinstance(ec, dict) and ec.get("contract_kind") == "bug_fix":
        return True
    return False


def derive_bug_fix_execution_outcome(
    *,
    scope_check: dict[str, Any] | None,
    invoke_data: dict[str, Any] | None,
    task_data: dict[str, Any] | None,
    builder_diff_summary: dict[str, Any] | None,
) -> dict[str, Any]:
    """
    Combine scope, invoke status, and diff summary into ``argus.builder.execution_outcome.v1``.

    * **breached** — scope check failed (path or semantic).
    * **blocked** — invoke failed.
    * **completed** — invoke succeeded and diff reports at least one changed file (scope already verified).
    * **partial** — invoke succeeded but no changes, or invoke missing with some diff evidence.
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

    if not _is_bug_fix(task_data):
        base["reasons"].append("not a bug_fix task — use content_slot outcome classifier")
        return base

    ec = task_data.get("execution_contract") if isinstance(task_data, dict) else None
    bid = str((ec or {}).get("bug_id") or (ec or {}).get("increment_target_id") or "").strip()
    rt = task_data.get("resolved_target") if isinstance(task_data, dict) else None
    if not bid and isinstance(rt, dict) and rt.get("id"):
        bid = str(rt.get("id")).strip()
    base["increment_id"] = bid or None

    changed = int((builder_diff_summary or {}).get("changed_file_count") or 0)

    if scope_check and scope_check.get("scope_breach"):
        base["outcome"] = OUTCOME_BREACHED
        base["reasons"].append("scope_breach: path or semantic scope check failed")
        return base

    inv = invoke_data if isinstance(invoke_data, dict) else None
    base["file_evidence"] = {
        "changed_file_count": changed,
        "source": (builder_diff_summary or {}).get("source"),
    }

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
            if changed > 0:
                base["outcome"] = OUTCOME_COMPLETED
                base["reasons"].append(
                    "invoke ok and builder_diff_summary reports changed files (path scope already enforced)"
                )
            else:
                base["outcome"] = OUTCOME_PARTIAL
                base["reasons"].append(
                    "invoke ok but no changed files vs baseline — fix may be incomplete or already present"
                )
            return base

        if changed > 0:
            base["outcome"] = OUTCOME_PARTIAL
            base["reasons"].append(
                f"diff shows changes but invocation_status={inv_status!r} — cannot attest completed"
            )
        else:
            base["outcome"] = OUTCOME_UNKNOWN
            base["reasons"].append(f"invoke not ok and no diff evidence (invocation_status={inv_status!r})")
        return base

    if changed > 0:
        base["outcome"] = OUTCOME_PARTIAL
        base["reasons"].append("no invoke record; diff shows changes — unverified (not completed)")
    else:
        base["outcome"] = OUTCOME_UNKNOWN
        base["reasons"].append("no invoke record and no diff changes")
    return base


__all__ = ["derive_bug_fix_execution_outcome"]
