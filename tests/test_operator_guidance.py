"""Pure guidance helpers for the operator console (no Streamlit)."""

from __future__ import annotations

from argus.dashboard.operator_guidance import (
    compute_start_here,
    enrich_escalation_item,
    pick_highest_priority_escalation,
    recommended_product_actions,
)


def test_enrich_approval_needed_routes_to_governance() -> None:
    g = enrich_escalation_item(
        {
            "item_id": "i1",
            "category": "approval_needed",
            "severity": "high",
            "product_id": "p",
            "evidence_summary": "needs confirm",
            "requested_action": "approve field X",
            "source": "runner",
        },
    )
    assert g["blocker_label"] == "Approval / confirmation needed"
    assert any("Governance" in s for s in g["recommended_steps"])
    assert "governance_pending" in g["jump_hints"]


def test_pick_highest_priority_prefers_critical() -> None:
    rows = [
        {"item_id": "a", "severity": "medium", "category": "review_needed"},
        {"item_id": "b", "severity": "critical", "category": "unsafe_to_continue"},
    ]
    top = pick_highest_priority_escalation(rows)
    assert top and top["item_id"] == "b"


def test_compute_start_here_escalation_wins_over_pending() -> None:
    sh = compute_start_here(
        escalation_actionable_raw=[
            {
                "item_id": "x",
                "category": "review_needed",
                "severity": "high",
                "product_id": "p1",
                "evidence_summary": "look",
                "requested_action": "r",
                "source": "s",
            },
        ],
        pending_approval_count=99,
        autonomous_primary_status="stopped_quiescent",
        autonomous_stop_reason="quiescence_recommendation",
        runner_stale_note="stale",
        operator_headline="ok",
        operator_next_step="step",
    )
    assert "review" in sh["headline"].lower() or "Review" in sh["headline"]
    assert sh["jump_hints"]


def test_compute_start_here_pending_when_no_escalation() -> None:
    sh = compute_start_here(
        escalation_actionable_raw=[],
        pending_approval_count=2,
        autonomous_primary_status=None,
        autonomous_stop_reason=None,
        runner_stale_note=None,
        operator_headline=None,
        operator_next_step=None,
    )
    assert "pending" in sh["headline"].lower()
    assert "tab_governance" in sh["jump_hints"]


def test_compute_start_here_autonomous_attention() -> None:
    sh = compute_start_here(
        escalation_actionable_raw=[],
        pending_approval_count=0,
        autonomous_primary_status="stopped_quiescent",
        autonomous_stop_reason="quiescence_recommendation",
        runner_stale_note=None,
        operator_headline=None,
        operator_next_step=None,
    )
    assert "quiescence" in sh["headline"].lower() or "Autonomous" in sh["headline"]
    assert "tab_autonomous" in sh["jump_hints"]


def test_compute_start_here_zero_state_appends_advisory_world_context() -> None:
    sh = compute_start_here(
        escalation_actionable_raw=[],
        pending_approval_count=0,
        autonomous_primary_status=None,
        autonomous_stop_reason=None,
        runner_stale_note=None,
        operator_headline=None,
        operator_next_step="Next from summary.",
        portfolio_zero_state=True,
        zero_state_world_context="2 advisory signal(s) from manual_seed. Fresh: 2, stale: 0. Advisory only.",
    )
    assert "zero-state" in sh["headline"].lower()
    joined = " ".join(sh["why_lines"])
    assert "External context (advisory" in joined
    assert "manual_seed" in joined


def test_compute_start_here_zero_state_creation_hint() -> None:
    sh = compute_start_here(
        escalation_actionable_raw=[],
        pending_approval_count=0,
        autonomous_primary_status=None,
        autonomous_stop_reason=None,
        runner_stale_note=None,
        operator_headline=None,
        operator_next_step="Next.",
        portfolio_zero_state=True,
        zero_state_world_context=None,
        zero_state_creation_hint="Advisory creation directions (hypothesis-level): (1) Do X — see operator summary.",
    )
    joined = " ".join(sh["why_lines"])
    assert "Advisory creation directions" in joined


def test_recommended_product_actions_policy_error_short_circuits() -> None:
    out = recommended_product_actions(
        {"policy_load_error": "missing file"},
        pending_count_for_product=0,
        active_grants=0,
    )
    assert len(out) == 1
    assert "argus.policy.yaml" in out[0]


def test_recommended_product_actions_no_action_when_clean() -> None:
    out = recommended_product_actions(
        {"policy_environment_mismatches": []},
        pending_count_for_product=0,
        active_grants=0,
    )
    assert any("no action" in x.lower() for x in out)
