"""argus.builder_outcome.v1 — minimal Phase 3 bridge (artifact-grounded, non-causal)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from argus.builder.outcome import (
    BUILDER_OUTCOME_SCHEMA,
    _builder_blocks_positive_attribution,
    _derive_attribution_and_delta,
    build_builder_outcome_payload,
    build_recent_observation_summary,
    comparison_evidence_strength,
    derive_observation_timing_context,
    derive_recent_observation_pattern,
    format_builder_outcome_compact_line,
    format_outcome_operator_one_liner,
    outcome_summary_for_portfolio,
    read_builder_outcome_latest,
    write_builder_outcome_artifact,
)
from tests.test_builder_status import (
    _write_invoke,
    _write_next_expansion,
    _write_prepared,
    _write_reconcile,
)
from tests.test_products_inventory import _minimal_valid_yaml, _write


def _write_product(root: Path, product_id: str) -> None:
    pr = root / "products" / product_id
    _write(pr / "product.yaml", _minimal_valid_yaml(product_id))
    (pr / "scripts").mkdir(parents=True, exist_ok=True)
    (pr / "scripts" / "s.sh").write_text("#!/bin/sh\necho\n", encoding="utf-8")


def test_outcome_schema_and_write_read(tmp_path: Path) -> None:
    pid = "bo1"
    _write_product(tmp_path, pid)
    _write_next_expansion(tmp_path, pid)
    _write_prepared(tmp_path, pid)
    _write_invoke(tmp_path, pid)
    _write_reconcile(tmp_path, pid)
    oc = build_builder_outcome_payload(tmp_path, pid)
    assert oc.get("schema") == BUILDER_OUTCOME_SCHEMA
    assert oc.get("product_id") == pid
    assert oc.get("attribution_status") == "not_enough_data"
    prov = oc.get("comparison_provenance") or {}
    assert prov.get("comparison_window_status") == "unavailable"
    assert prov.get("comparison_basis") == "none"
    p = write_builder_outcome_artifact(tmp_path, oc)
    assert p.name == "latest.json"
    raw = read_builder_outcome_latest(tmp_path, pid)
    assert raw and raw.get("attribution_status") == oc.get("attribution_status")


def test_attribution_not_enough_without_signals(tmp_path: Path) -> None:
    pid = "nosig"
    _write_product(tmp_path, pid)
    _write_next_expansion(tmp_path, pid)
    _write_prepared(tmp_path, pid)
    _write_invoke(tmp_path, pid)
    _write_reconcile(tmp_path, pid)
    oc = build_builder_outcome_payload(tmp_path, pid)
    assert oc["attribution_status"] == "not_enough_data"
    assert (oc.get("comparison_provenance") or {}).get("comparison_window_status") == "unavailable"


@patch("argus.builder.outcome.load_latest_bundle")
def test_comparison_provenance_continuity_based(mock_load: object, tmp_path: Path) -> None:
    """Embedded signal_continuity with schema v1 → continuity_based pairwise basis."""
    pid = "cont1"
    _write_product(tmp_path, pid)
    _write_next_expansion(tmp_path, pid)
    _write_prepared(tmp_path, pid)
    _write_invoke(tmp_path, pid)
    _write_reconcile(tmp_path, pid)
    mock_load.return_value = SimpleNamespace(
        collected_at_utc="2026-01-02T12:00:00+00:00",
        signal_continuity={
            "schema": "argus.signal_continuity.v1",
            "compared": True,
            "appeared": [{"k": "a"}],
            "disappeared": [],
            "freshness_regressed": [],
            "window_continuity_broken": [],
            "prior_collected_at_utc": "2026-01-01T12:00:00+00:00",
            "current_collected_at_utc": "2026-01-02T12:00:00+00:00",
        },
    )
    oc = build_builder_outcome_payload(tmp_path, pid)
    prov = oc.get("comparison_provenance") or {}
    assert prov.get("comparison_window_status") == "continuity_based"
    assert prov.get("comparison_basis") == "argus.signal_continuity.v1_pairwise"
    assert "compared_artifact_refs" in prov


@patch("argus.builder.outcome.load_latest_bundle")
def test_comparison_provenance_latest_only(mock_load: object, tmp_path: Path) -> None:
    """Bundle on disk but no pairwise continuity → latest_only."""
    pid = "lat1"
    _write_product(tmp_path, pid)
    _write_next_expansion(tmp_path, pid)
    _write_prepared(tmp_path, pid)
    _write_invoke(tmp_path, pid)
    _write_reconcile(tmp_path, pid)
    mock_load.return_value = SimpleNamespace(
        collected_at_utc="2026-01-01T12:00:00+00:00",
        signal_continuity=None,
    )
    oc = build_builder_outcome_payload(tmp_path, pid)
    prov = oc.get("comparison_provenance") or {}
    assert prov.get("comparison_window_status") == "latest_only"
    assert prov.get("comparison_basis") == "signals_latest_snapshot_only"


@patch("argus.builder.outcome.load_latest_bundle")
def test_comparison_provenance_bounded_when_schema_unexpected(mock_load: object, tmp_path: Path) -> None:
    """compared=true but non-v1 schema label → bounded_recent_window, conservative basis string."""
    pid = "sch1"
    _write_product(tmp_path, pid)
    _write_next_expansion(tmp_path, pid)
    _write_prepared(tmp_path, pid)
    _write_invoke(tmp_path, pid)
    _write_reconcile(tmp_path, pid)
    mock_load.return_value = SimpleNamespace(
        collected_at_utc="2026-01-02T12:00:00+00:00",
        signal_continuity={
            "schema": "custom.unknown.v1",
            "compared": True,
            "appeared": [],
            "disappeared": [],
            "freshness_regressed": [],
            "window_continuity_broken": [],
        },
    )
    oc = build_builder_outcome_payload(tmp_path, pid)
    prov = oc.get("comparison_provenance") or {}
    assert prov.get("comparison_window_status") == "bounded_recent_window"
    assert "unlabeled" in str(prov.get("comparison_basis") or "")


def test_attribution_blocks_positive_when_scope_breach(tmp_path: Path) -> None:
    row = {
        "scope_breach": True,
        "review_status": "merge_candidate",
        "execution_outcome": "completed",
    }
    osum = {"trust_posture": "ready_to_review"}
    assert _builder_blocks_positive_attribution(row, osum) is True
    cont = {
        "compared": True,
        "appeared": [{"k": "1"}],
        "disappeared": [],
        "freshness_regressed": [],
        "window_continuity_broken": [],
    }
    att, msg = _derive_attribution_and_delta(continuity=cont, blocks_positive=True)
    assert att != "possible_positive_signal_change"
    assert "risky" in msg.lower() or "blocked" in msg.lower() or "not assessed" in msg.lower()


def test_attribution_possible_negative_from_continuity() -> None:
    cont = {
        "compared": True,
        "appeared": [],
        "disappeared": [{"k": "1"}, {"k": "2"}],
        "freshness_regressed": [{"x": 1}],
        "window_continuity_broken": [],
    }
    att, _ = _derive_attribution_and_delta(continuity=cont, blocks_positive=False)
    assert att == "possible_negative_signal_change"


def test_unknown_reason_string_in_continuity_passthrough() -> None:
    """No crash on minimal continuity dict."""
    att, _ = _derive_attribution_and_delta(
        continuity={"compared": True, "appeared": [], "disappeared": [], "freshness_regressed": [], "window_continuity_broken": []},
        blocks_positive=False,
    )
    assert att == "no_material_change_detected"


def test_portfolio_payload_includes_outcome_summaries(tmp_path: Path) -> None:
    from argus.portfolio.builder_activity import (
        build_portfolio_builder_activity_payload,
        public_builder_activity_payload,
    )

    pid = "po"
    _write_product(tmp_path, pid)
    _write_next_expansion(tmp_path, pid)
    _write_prepared(tmp_path, pid)
    _write_invoke(tmp_path, pid)
    _write_reconcile(tmp_path, pid)
    pl = build_portfolio_builder_activity_payload(tmp_path)
    pub = public_builder_activity_payload(pl)
    assert "_builder_outcome_payloads" not in pub
    assert pub.get("builder_outcome_schema") == BUILDER_OUTCOME_SCHEMA
    sums = pub.get("builder_outcome_summaries") or []
    assert len(sums) == 1
    assert sums[0].get("product_id") == pid
    assert sums[0].get("attribution_status") in (
        "not_enough_data",
        "no_material_change_detected",
    )
    assert sums[0].get("comparison_window_status") == "unavailable"
    assert sums[0].get("comparison_basis") == "none"
    assert sums[0].get("comparison_window_note")
    assert sums[0].get("comparison_evidence_strength") == "none"
    assert "Outcome:" in str(sums[0].get("outcome_compact_line") or "")
    assert "unavailable" in str(sums[0].get("outcome_compact_line") or "")
    assert sums[0].get("outcome_disclaimer_short")


def test_operator_summary_markdown_surfaces_comparison_provenance() -> None:
    from argus.dashboard.operator_summary import render_operator_summary_markdown

    ocl = format_builder_outcome_compact_line(
        attribution_status="not_enough_data",
        comparison_window_status="unavailable",
        comparison_basis="none",
    )
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
                "trust_summary_line": "1 product",
                "next_action_hint": "Review",
                "product_count": 1,
                "attention_products": [],
                "recent_runs": [],
                "builder_outcome_schema": BUILDER_OUTCOME_SCHEMA,
                "builder_outcome_lines_preview": [ocl],
                "builder_outcome_summaries": [
                    {
                        "product_id": "x1",
                        "attribution_status": "not_enough_data",
                        "delta_summary": "No signals bundle.",
                        "comparison_window_status": "unavailable",
                        "comparison_basis": "none",
                        "comparison_window_note": "No snapshot.",
                        "outcome_compact_line": ocl,
                        "outcome_disclaimer_short": "Observational only.",
                        "artifact_path_repo": "runs/builder/outcome/x1/latest.json",
                    }
                ],
            },
        }
    )
    assert "Outcome:" in md
    assert "Phase 3 `builder_outcome`" in md
    assert "unavailable" in md
    assert "Observational only." in md


def test_operator_summary_markdown_hint_when_outcomes_missing_from_snapshot() -> None:
    from argus.dashboard.operator_summary import render_operator_summary_markdown

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
                "trust_summary_line": "1 product",
                "next_action_hint": "Review",
                "product_count": 1,
                "attention_products": [],
                "recent_runs": [],
                "builder_outcome_summaries": [],
                "builder_outcome_schema": BUILDER_OUTCOME_SCHEMA,
            },
        }
    )
    assert "re-run" in md.lower()
    assert "builder-activity" in md.lower()


def test_compact_line_weaker_than_continuity() -> None:
    w = format_builder_outcome_compact_line(
        attribution_status="no_material_change_detected",
        comparison_window_status="latest_only",
        comparison_basis="signals_latest_snapshot_only",
    )
    assert "latest_only" in w
    assert comparison_evidence_strength("latest_only") == "weak"
    assert comparison_evidence_strength("continuity_based") == "moderate"
    assert comparison_evidence_strength("unavailable") == "none"


def test_outcome_summary_fallback_rebuilds_compact_line(tmp_path: Path) -> None:
    row = outcome_summary_for_portfolio(
        {
            "product_id": "p",
            "attribution_status": "possible_positive_signal_change",
            "delta_summary": "x",
            "comparison_provenance": {
                "comparison_window_status": "continuity_based",
                "comparison_basis": "argus.signal_continuity.v1_pairwise",
                "comparison_window_note": "n",
                "compared_artifact_refs": {},
            },
        },
        tmp_path,
    )
    assert row.get("comparison_evidence_strength") == "moderate"
    assert "continuity_based" in str(row.get("outcome_compact_line") or "")


def test_derive_observation_timing_same_cycle() -> None:
    t = derive_observation_timing_context(
        builder_last_artifact_at_utc="2026-04-16T12:00:00+00:00",
        signal_latest_collected_at_utc="2026-04-16T12:10:00+00:00",
    )
    assert t["observation_timing_status"] == "same_cycle_or_adjacent"
    assert t.get("timing_delta_summary")


def test_derive_observation_timing_signal_older_than_builder() -> None:
    t = derive_observation_timing_context(
        builder_last_artifact_at_utc="2026-04-16T14:00:00+00:00",
        signal_latest_collected_at_utc="2026-04-16T10:00:00+00:00",
    )
    assert t["observation_timing_status"] == "delayed_or_uncertain"


def test_derive_observation_timing_unknown_missing_signal_ts() -> None:
    t = derive_observation_timing_context(
        builder_last_artifact_at_utc="2026-04-16T12:00:00+00:00",
        signal_latest_collected_at_utc=None,
    )
    assert t["observation_timing_status"] == "unknown"


def test_recent_observation_pattern_labels() -> None:
    assert derive_recent_observation_pattern([]) == "insufficient_recent_history"
    assert derive_recent_observation_pattern(["not_enough_data"]) == "single_observation_only"
    assert (
        derive_recent_observation_pattern(
            ["possible_negative_signal_change", "possible_negative_signal_change"]
        )
        == "repeated_negative_observations"
    )
    assert (
        derive_recent_observation_pattern(
            ["no_material_change_detected", "no_material_change_detected"]
        )
        == "repeated_no_material_change"
    )
    assert (
        derive_recent_observation_pattern(["possible_positive_signal_change", "not_enough_data"])
        == "mixed_recent_observations"
    )


def test_build_recent_observation_summary_single_only() -> None:
    s = build_recent_observation_summary(
        current_generated_at_utc="2026-01-01T00:00:00Z",
        current_attribution_status="not_enough_data",
        current_evidence_strength="none",
        history_tail=[],
    )
    assert s["recent_observation_count"] == 1
    assert s["recent_observation_pattern"] == "single_observation_only"


def test_history_tail_enables_repeat_pattern(tmp_path: Path) -> None:
    pid = "hist1"
    _write_product(tmp_path, pid)
    _write_next_expansion(tmp_path, pid)
    _write_prepared(tmp_path, pid)
    _write_invoke(tmp_path, pid)
    _write_reconcile(tmp_path, pid)
    o1 = build_builder_outcome_payload(tmp_path, pid)
    write_builder_outcome_artifact(tmp_path, o1)
    o2 = build_builder_outcome_payload(tmp_path, pid)
    rs = o2.get("recent_observation_summary") or {}
    assert rs.get("recent_observation_count") == 2
    assert rs.get("recent_attribution_statuses") and len(rs["recent_attribution_statuses"]) == 2


def test_outcome_operator_one_liner_includes_timing_and_recent(tmp_path: Path) -> None:
    row = outcome_summary_for_portfolio(
        {
            "product_id": "p",
            "attribution_status": "not_enough_data",
            "comparison_provenance": {
                "comparison_window_status": "unavailable",
                "comparison_basis": "none",
                "comparison_window_note": "x",
                "compared_artifact_refs": {},
            },
            "observation_timing": {
                "observation_timing_status": "unknown",
                "builder_to_signal_timing_note": "n",
            },
            "recent_observation_summary": {
                "recent_observation_pattern": "single_observation_only",
                "recent_observation_count": 1,
                "recent_attribution_statuses": ["not_enough_data"],
                "recent_evidence_strengths": ["none"],
                "recent_observation_note": None,
            },
        },
        tmp_path,
    )
    assert "timing: unknown" in str(row.get("outcome_operator_one_liner") or "")
    assert "recent: single_observation_only" in str(row.get("outcome_operator_one_liner") or "")
    assert format_outcome_operator_one_liner(
        outcome_compact_line="Outcome: x (compare: y)",
        observation_timing_status=None,
        recent_observation_pattern=None,
        recent_observation_count=None,
    ) == "Outcome: x (compare: y)"
