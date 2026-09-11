"""Human-readable labels for trust synthesis — mapping only; logic lives in derive_trust_operator_view."""

from __future__ import annotations

import pytest

from argus.builder.trust_operator_synthesis import (
    RECOMMENDED_NEXT_LABELS,
    RECOMMENDED_NEXT_REASON_PHRASES,
    TRUST_POSTURE_LABELS,
    TRUST_POSTURE_REASON_PHRASES,
    augment_trust_operator_view_for_display,
    human_summary_lines_for_trust_view,
    humanize_recommended_next_reason,
    humanize_trust_posture_reason,
    recommended_next_label,
    trust_posture_label,
)


@pytest.mark.parametrize(
    "code,expected_substr",
    [
        ("unsafe_scope_breach", "Unsafe scope"),
        ("blocked", "Blocked"),
        ("degraded_unsandboxed", "unsandboxed"),
        ("degraded_plain_argus_root", "Argus root"),
        ("ready_to_review", "Ready to review"),
        ("review_carefully", "Review carefully"),
        ("unknown_incomplete", "Incomplete"),
    ],
)
def test_trust_posture_label_maps_known_codes(code: str, expected_substr: str) -> None:
    assert expected_substr in trust_posture_label(code)


def test_trust_posture_label_unknown_passes_through() -> None:
    assert trust_posture_label("future_code_xyz") == "future_code_xyz"


@pytest.mark.parametrize(
    "code,expected_substr",
    [
        ("investigate_scope_breach", "Investigate scope"),
        ("review_and_merge", "merge"),
        ("inspect_diff", "Inspect diff"),
        ("rerun_prepare", "prepare"),
        ("fix_host_readiness", "host"),
        ("run_invoke_reconcile", "invoke"),
        ("review_escalation", "escalation"),
        ("fix_declaration", "declaration"),
        ("no_clear_action", "No required"),
    ],
)
def test_recommended_next_label_maps_known_codes(code: str, expected_substr: str) -> None:
    assert expected_substr.lower() in recommended_next_label(code).lower()


def test_recommended_next_label_unknown_passes_through() -> None:
    assert recommended_next_label("future_action") == "future_action"


def test_mapping_tables_cover_all_literals() -> None:
    from argus.builder.trust_operator_synthesis import RecommendedNext, TrustPosture

    for k in TrustPosture.__args__:  # type: ignore[attr-defined]
        assert k in TRUST_POSTURE_LABELS
    for k in RecommendedNext.__args__:  # type: ignore[attr-defined]
        assert k in RECOMMENDED_NEXT_LABELS


def test_human_summary_lines_two_entries() -> None:
    tv = {
        "trust_posture": "blocked",
        "trust_posture_reasons": ["merge blocked or failed execute invoke"],
        "recommended_next_action": "inspect_diff",
        "recommended_next_reasons": ["inspect diff, invoke record, and reconcile detail before merge"],
    }
    lines = human_summary_lines_for_trust_view(tv)
    assert len(lines) == 2
    assert lines[0].startswith("Trust —")
    assert "Blocked" in lines[0]
    assert "Merge is blocked" in lines[0]
    assert lines[1].startswith("Next —")
    assert "Inspect diff, invoke record" in lines[1]


def test_augment_adds_fields_preserves_machine() -> None:
    base = {
        "schema": "argus.builder_trust_operator_view.v1",
        "trust_posture": "ready_to_review",
        "trust_posture_reasons": ["x"],
        "recommended_next_action": "review_and_merge",
        "recommended_next_reasons": ["y"],
        "product_id": "p",
    }
    aug = augment_trust_operator_view_for_display(base)
    assert aug["trust_posture"] == "ready_to_review"
    assert aug["recommended_next_action"] == "review_and_merge"
    assert aug["trust_posture_label"] == TRUST_POSTURE_LABELS["ready_to_review"]
    assert aug["recommended_next_label"] == RECOMMENDED_NEXT_LABELS["review_and_merge"]
    assert isinstance(aug["human_summary_lines"], list)
    assert len(aug["human_summary_lines"]) == 2
    assert aug["trust_posture_reasons_display"] == [humanize_trust_posture_reason("x")]
    assert aug["recommended_next_reasons_display"] == [humanize_recommended_next_reason("y")]


def test_humanize_unknown_reason_passthrough() -> None:
    assert humanize_trust_posture_reason("totally unknown posture note") == "totally unknown posture note"
    assert humanize_recommended_next_reason("custom future reason") == "custom future reason"


def test_reason_phrase_tables_cover_derived_strings() -> None:
    """Guards drift: every machine string from derive_trust_operator_view should map."""
    for k in TRUST_POSTURE_REASON_PHRASES:
        assert humanize_trust_posture_reason(k) == TRUST_POSTURE_REASON_PHRASES[k]
    for k in RECOMMENDED_NEXT_REASON_PHRASES:
        assert humanize_recommended_next_reason(k) == RECOMMENDED_NEXT_REASON_PHRASES[k]
