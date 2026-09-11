"""
Canonical Phase 1 fields embedded in ``execution_detail`` for orchestration feedback.

Legacy feedback files may omit these keys; new writes from :func:`execute_orchestration_action`
always include ``phase1_embed_schema_version`` and ``phase1_evaluated`` so operators can
distinguish old vs new without rewriting history.
"""

from __future__ import annotations

from typing import Any

# Bump when embedded shape changes (operators / tools may key off this).
PHASE1_EMBED_SCHEMA_VERSION = 1


def embed_phase1_evaluated(
    base_detail: dict[str, Any],
    *,
    permission_decision: dict[str, Any],
    audit_path_repo_relative: str,
) -> dict[str, Any]:
    """
    Merge Phase 1 truth into handler ``execution_detail`` after a successful policy allow.

    ``permission_decision`` is the same payload written to ``runs/policy/phase1_decisions/``
    (``argus.project_permission_decision.v1``), minus redundant artifact path duplication
    handled here.
    """
    out: dict[str, Any] = dict(base_detail)
    out["phase1_embed_schema_version"] = PHASE1_EMBED_SCHEMA_VERSION
    out["phase1_evaluated"] = True
    out["phase1_not_evaluated_reason"] = None
    out["phase1_permission_decision"] = permission_decision
    out["phase1_decision_audit_path"] = audit_path_repo_relative
    return out


def embed_phase1_blocked(
    *,
    permission_decision: dict[str, Any],
    audit_path_repo_relative: str,
) -> dict[str, Any]:
    """Execution detail for refused / blocked_pending_confirmation / capability_mismatch (orchestration gate)."""
    return {
        "phase1_embed_schema_version": PHASE1_EMBED_SCHEMA_VERSION,
        "phase1_evaluated": True,
        "phase1_not_evaluated_reason": None,
        "phase1_permission_decision": permission_decision,
        "phase1_decision_audit_path": audit_path_repo_relative,
    }


def embed_phase1_not_evaluated(
    base_detail: dict[str, Any] | None,
    *,
    reason: str,
) -> dict[str, Any]:
    """Explicit absence: Phase 1 was not evaluated for this feedback row."""
    out: dict[str, Any] = dict(base_detail) if isinstance(base_detail, dict) else {}
    out["phase1_embed_schema_version"] = PHASE1_EMBED_SCHEMA_VERSION
    out["phase1_evaluated"] = False
    out["phase1_not_evaluated_reason"] = reason
    out["phase1_permission_decision"] = None
    out["phase1_decision_audit_path"] = None
    return out


__all__ = [
    "PHASE1_EMBED_SCHEMA_VERSION",
    "embed_phase1_blocked",
    "embed_phase1_evaluated",
    "embed_phase1_not_evaluated",
]
