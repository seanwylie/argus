"""Presentation-only grouping of Builder attention reason codes (no new signals)."""

from __future__ import annotations

from argus.portfolio.builder_activity_attention import (
    ATTENTION_GROUP_KEYS,
    ATTENTION_GROUP_TITLES,
    group_attention_products_by_category,
)


def test_group_scope_safety_reasons() -> None:
    att = [
        {
            "product_id": "p1",
            "reasons": ["scope_breach", "path_scope_breach"],
            "headline": "p1: scope_breach",
        }
    ]
    g = group_attention_products_by_category(att)
    assert g["scope_safety"] == att
    assert g["blocked_failed"] == []
    assert g["needs_review"] == []


def test_group_blocked_failed_reasons() -> None:
    att = [{"product_id": "b1", "reasons": ["invoke_failed", "review_blocked"], "headline": "x"}]
    g = group_attention_products_by_category(att)
    assert g["blocked_failed"] == att
    assert g["scope_safety"] == []
    assert g["needs_review"] == []


def test_group_needs_review_escalation_reasons() -> None:
    att = [
        {
            "product_id": "e1",
            "reasons": ["reconcile_escalation_emitted"],
            "headline": "x",
        }
    ]
    g = group_attention_products_by_category(att)
    assert g["needs_review"] == att
    assert g["scope_safety"] == []
    assert g["blocked_failed"] == []


def test_product_can_appear_in_multiple_groups() -> None:
    att = [
        {
            "product_id": "multi",
            "reasons": ["scope_breach", "escalation_inbox_visible"],
            "headline": "x",
        }
    ]
    g = group_attention_products_by_category(att)
    assert att[0] in g["scope_safety"]
    assert att[0] in g["needs_review"]
    assert g["blocked_failed"] == []


def test_unknown_reason_code_goes_to_needs_review() -> None:
    att = [{"product_id": "u1", "reasons": ["future_reason_code_xyz"], "headline": "x"}]
    g = group_attention_products_by_category(att)
    assert g["needs_review"] == att
    assert g["scope_safety"] == []
    assert g["blocked_failed"] == []


def test_empty_attention() -> None:
    g = group_attention_products_by_category([])
    assert all(g[k] == [] for k in ATTENTION_GROUP_KEYS)


def test_group_titles_cover_keys() -> None:
    for k in ATTENTION_GROUP_KEYS:
        assert k in ATTENTION_GROUP_TITLES
        assert ATTENTION_GROUP_TITLES[k]


def test_render_operator_summary_markdown_uses_grouped_sections() -> None:
    from argus.dashboard.operator_summary import render_operator_summary_markdown

    att = [
        {"product_id": "s1", "reasons": ["scope_breach"], "headline": "s1"},
        {"product_id": "f1", "reasons": ["invoke_failed"], "headline": "f1"},
        {"product_id": "n1", "reasons": ["escalation_inbox_visible"], "headline": "n1"},
    ]
    grouped = group_attention_products_by_category(att)
    md = render_operator_summary_markdown(
        {
            "headline_status": "healthy",
            "confidence_level": "high",
            "recommended_next_step": "—",
            "lifecycle_snapshot": {},
            "learning_snapshot": {},
            "outcome_summary": {},
            "intervention_inbox_summary": {},
            "builder_activity_snapshot": {
                "artifact_present": True,
                "source_artifact": "runs/portfolio/builder_activity/latest.json",
                "generated_at_utc": "2026-01-01T00:00:00Z",
                "run_id": "r1",
                "trust_summary_line": "3 products",
                "next_action_hint": "Review",
                "attention_products": att,
                "attention_products_grouped": grouped,
                "recent_runs": [],
            },
        }
    )
    assert "#### Scope / safety" in md
    assert "#### Blocked / failed" in md
    assert "#### Needs review" in md
    assert "`s1`" in md and "scope_breach" in md
