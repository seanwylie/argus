"""Deterministic trust posture + recommended next action (derived from status-shaped payloads)."""

from __future__ import annotations

from argus.builder.trust_operator_synthesis import derive_trust_operator_view


def _base_payload(**kwargs: object) -> dict:
    p = {
        "product_id": "p1",
        "alignment_summary": "aligned",
        "latest_invoke": {"present": True, "mode": "execute", "execution_backend": "agent"},
        "latest_reconcile": {
            "present": True,
            "review_status": "merge_candidate",
            "execution_outcome": "completed",
            "scope_breach": False,
            "diff_fallback_used": False,
        },
        "last_escalation": {},
    }
    p.update(kwargs)
    return p


def test_trust_posture_unsafe_scope_first() -> None:
    v = derive_trust_operator_view(
        _base_payload(
            latest_reconcile={
                "present": True,
                "review_status": "blocked",
                "execution_outcome": "completed",
                "scope_breach": True,
                "diff_fallback_used": False,
            },
        )
    )
    assert v["trust_posture"] == "unsafe_scope_breach"
    assert v["recommended_next_action"] == "investigate_scope_breach"


def test_trust_posture_blocked_failed_execute() -> None:
    v = derive_trust_operator_view(
        _base_payload(
            latest_invoke={
                "present": True,
                "mode": "execute",
                "invocation_status": "failed",
                "execution_backend": "agent",
            },
            latest_reconcile={
                "present": True,
                "review_status": "blocked",
                "execution_outcome": "unknown",
                "scope_breach": False,
                "diff_fallback_used": False,
            },
        )
    )
    assert v["trust_posture"] == "blocked"
    assert v["recommended_next_action"] == "inspect_diff"


def test_trust_posture_degraded_unsandboxed() -> None:
    v = derive_trust_operator_view(
        _base_payload(
            latest_invoke={
                "present": True,
                "mode": "execute",
                "execution_backend": "agent",
                "invocation_status": "ok",
                "trust_degraded_unsandboxed": True,
                "containment_fallback_used": False,
            },
        )
    )
    assert v["trust_posture"] == "degraded_unsandboxed"
    assert v["recommended_next_action"] == "inspect_diff"


def test_trust_posture_degraded_plain_argus_root() -> None:
    v = derive_trust_operator_view(
        _base_payload(
            latest_invoke={
                "present": True,
                "mode": "execute",
                "execution_backend": "agent",
                "invocation_status": "ok",
                "builder_workspace_kind": "argus_root",
                "trust_degraded_workspace_scope": True,
            },
        )
    )
    assert v["trust_posture"] == "degraded_plain_argus_root"
    assert v["recommended_next_action"] == "inspect_diff"


def test_ready_to_review_merge_candidate() -> None:
    v = derive_trust_operator_view(_base_payload())
    assert v["trust_posture"] == "ready_to_review"
    assert v["recommended_next_action"] == "review_and_merge"


def test_review_carefully_review_required() -> None:
    v = derive_trust_operator_view(
        _base_payload(
            latest_reconcile={
                "present": True,
                "review_status": "review_required",
                "execution_outcome": "completed",
                "scope_breach": False,
                "diff_fallback_used": False,
            },
        )
    )
    assert v["trust_posture"] == "review_carefully"
    assert v["recommended_next_action"] == "inspect_diff"


def test_unknown_incomplete_no_records() -> None:
    v = derive_trust_operator_view(
        {
            "product_id": "x",
            "alignment_summary": "records_missing",
            "latest_invoke": {"present": False},
            "latest_reconcile": {"present": False},
            "last_escalation": {},
        }
    )
    assert v["trust_posture"] == "unknown_incomplete"
    assert v["recommended_next_action"] == "run_invoke_reconcile"


def test_recommended_fix_declaration_before_escalation() -> None:
    v = derive_trust_operator_view(
        {
            "product_id": "x",
            "alignment_summary": "missing_declared_target",
            "latest_invoke": {"present": False},
            "latest_reconcile": {"present": False},
            "last_escalation": {"present": True, "severity": "high"},
        }
    )
    assert v["recommended_next_action"] == "fix_declaration"


def test_recommended_escalation_before_merge_candidate() -> None:
    v = derive_trust_operator_view(
        _base_payload(
            last_escalation={"present": True, "severity": "high", "short_reason": "x"},
        )
    )
    assert v["trust_posture"] == "ready_to_review"
    assert v["recommended_next_action"] == "review_escalation"


def test_recommended_host_readiness_after_merge_gates() -> None:
    v = derive_trust_operator_view(
        _base_payload(
            last_escalation={},
            latest_invoke={
                "present": True,
                "mode": "execute",
                "execution_backend": "agent",
                "invocation_status": "ok",
                "trust_degraded_missing_no_new_privs": True,
            },
            latest_reconcile={
                "present": True,
                "review_status": "review_required",
                "execution_outcome": "completed",
                "scope_breach": False,
                "diff_fallback_used": False,
            },
        )
    )
    assert v["trust_posture"] == "review_carefully"
    assert v["recommended_next_action"] == "fix_host_readiness"


def test_drift_excludes_ready_to_review() -> None:
    v = derive_trust_operator_view(
        _base_payload(
            alignment_summary="drift_detected",
            latest_reconcile={
                "present": True,
                "review_status": "merge_candidate",
                "execution_outcome": "completed",
                "scope_breach": False,
                "diff_fallback_used": False,
            },
        )
    )
    assert v["trust_posture"] != "ready_to_review"
    assert v["recommended_next_action"] == "rerun_prepare"


def test_schema_and_reasons_capped() -> None:
    v = derive_trust_operator_view(_base_payload())
    assert v["schema"] == "argus.builder_trust_operator_view.v1"
    assert isinstance(v["trust_posture_reasons"], list)
    assert isinstance(v["recommended_next_reasons"], list)
