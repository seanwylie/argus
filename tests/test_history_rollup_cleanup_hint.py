"""cleanup_hint derivation and compact recent-run formatting (display-only)."""

from __future__ import annotations

import pytest

from argus.builder.history_rollup import (
    derive_cleanup_hint,
    format_recent_run_compact_line,
)


@pytest.mark.parametrize(
    "mr,tp,expected_substr",
    [
        ("unsafe", "ready_to_review", "unsafe"),
        ("merge_candidate", "unsafe_scope_breach", "unsafe"),
        ("blocked", "review_carefully", "blocked"),
        ("merge_candidate", "blocked", "blocked"),
        ("merge_candidate", "degraded_unsandboxed", "containment"),
        ("merge_candidate", "degraded_plain_argus_root", "containment"),
        ("merge_candidate", "unknown_incomplete", "Finish declaration"),
        ("merge_candidate", "ready_to_review", "option"),
        ("review_required", "ready_to_review", "human"),
        ("merge_candidate", "review_carefully", "human"),
    ],
)
def test_derive_cleanup_hint_known_pairs(mr: str, tp: str, expected_substr: str) -> None:
    h = derive_cleanup_hint(mr, tp)
    assert h is not None
    assert expected_substr.lower() in h.lower()


def test_derive_cleanup_hint_merge_candidate_fallback() -> None:
    h = derive_cleanup_hint("merge_candidate", "some_future_posture")
    assert h is not None
    assert "policy" in h.lower() or "trust" in h.lower()


def test_derive_cleanup_hint_empty_inputs() -> None:
    assert derive_cleanup_hint(None, None) is None
    assert derive_cleanup_hint("", "") is None


def test_derive_cleanup_hint_default_detail() -> None:
    h = derive_cleanup_hint("unknown_mr", "unknown_tp")
    assert h is not None
    assert "argus builder status" in h.lower()


def test_format_recent_run_compact_line() -> None:
    line = format_recent_run_compact_line(
        {
            "product_id": "p1",
            "execution_contract_kind": "content_slot",
            "trust_posture": "blocked",
            "recommended_next_label": "Inspect diff and records",
        }
    )
    assert line == "p1 | content_slot | blocked | Inspect diff and records"


def test_format_recent_run_prefers_trust_posture_label() -> None:
    line = format_recent_run_compact_line(
        {
            "product_id": "p1",
            "execution_contract_kind": "content_slot",
            "trust_posture": "blocked",
            "trust_posture_label": "Blocked",
            "recommended_next_label": "Inspect diff and records",
        }
    )
    assert " | Blocked | " in line


def test_format_recent_run_prefers_label_over_action() -> None:
    line = format_recent_run_compact_line(
        {
            "product_id": "x",
            "declared_target_type": "bug_fix",
            "trust_posture": "ready_to_review",
            "recommended_next_label": "Review diff, then merge if appropriate",
            "recommended_next_action": "inspect_diff",
        }
    )
    assert "Review diff" in line
    assert "inspect_diff" not in line


def test_format_recent_run_long_next_truncated() -> None:
    long_next = "x" * 60
    line = format_recent_run_compact_line(
        {
            "product_id": "p",
            "declared_target_type": "t",
            "trust_posture": "t2",
            "recommended_next_label": long_next,
        }
    )
    assert len(line.split(" | ")[-1]) <= 48
