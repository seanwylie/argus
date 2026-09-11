"""argus.builder_outcome_validation.v1 — aggregate calibration over on-disk outcomes."""

from __future__ import annotations

from pathlib import Path

from argus.builder.outcome import (
    BUILDER_OUTCOME_SCHEMA,
    build_builder_outcome_payload,
    write_builder_outcome_artifact,
)
from argus.dashboard.operator_summary import (
    evaluate_operator_summary,
    render_operator_summary_markdown,
)
from argus.portfolio.builder_activity import write_portfolio_builder_activity_artifacts
from argus.portfolio.builder_outcome_validation import (
    BUILDER_OUTCOME_VALIDATION_SCHEMA,
    build_builder_outcome_validation_report,
    derive_validation_interpretation,
    load_builder_outcome_validation_latest,
    write_builder_outcome_validation_artifact,
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


def test_evaluate_operator_summary_includes_validation_snapshot(tmp_path: Path) -> None:
    pl = evaluate_operator_summary(tmp_path, limit_history=5)
    bov = pl.get("builder_outcome_validation") or {}
    assert bov.get("schema") == BUILDER_OUTCOME_VALIDATION_SCHEMA
    assert "validation_headline" in bov
    assert "validation_attention_level" in bov


def test_validation_empty_repo(tmp_path: Path) -> None:
    rep = build_builder_outcome_validation_report(tmp_path)
    assert rep["schema"] == BUILDER_OUTCOME_VALIDATION_SCHEMA
    assert rep["products_with_outcome_artifact"] == 0
    assert rep["counts_by_attribution_status"] == {}
    assert rep.get("validation_attention_level") == "none"
    assert "nothing to calibrate" in (rep.get("validation_headline") or "").lower()


def test_interpretation_sparse_single_product() -> None:
    r = derive_validation_interpretation(
        {
            "products_with_outcome_artifact": 1,
            "counts_by_comparison_evidence_strength": {"none": 1},
            "counts_by_observation_timing_status": {"unknown": 1},
            "counts_by_attribution_status": {"not_enough_data": 1},
            "counts_by_recent_observation_pattern": {"single_observation_only": 1},
            "products_repeated_negative_observations": [],
            "products_weak_current_evidence": ["a"],
            "products_weak_majority_recent_window": [],
        }
    )
    assert r["validation_attention_level"] == "ordinary"
    assert any("one outcome snapshot" in x.lower() for x in (r.get("validation_interpretation_lines") or []))


def test_interpretation_mixed_attention_suggested() -> None:
    r = derive_validation_interpretation(
        {
            "products_with_outcome_artifact": 4,
            "counts_by_comparison_evidence_strength": {"none": 3, "weak": 1},
            "counts_by_observation_timing_status": {"unknown": 4},
            "counts_by_attribution_status": {"not_enough_data": 4},
            "counts_by_recent_observation_pattern": {"single_observation_only": 4},
            "products_repeated_negative_observations": [],
            "products_weak_current_evidence": ["a", "b", "c"],
            "products_weak_majority_recent_window": ["x", "y", "z"],
        }
    )
    assert r["validation_attention_level"] == "attention_suggested"
    assert "weak or unavailable" in (r.get("validation_headline") or "").lower() or "uncertain" in (
        r.get("validation_headline") or ""
    ).lower()


def test_validation_one_product(tmp_path: Path) -> None:
    pid = "v1"
    _write_product(tmp_path, pid)
    _write_next_expansion(tmp_path, pid)
    _write_prepared(tmp_path, pid)
    _write_invoke(tmp_path, pid)
    _write_reconcile(tmp_path, pid)
    oc = build_builder_outcome_payload(tmp_path, pid)
    write_builder_outcome_artifact(tmp_path, oc)
    rep = build_builder_outcome_validation_report(tmp_path)
    assert rep["products_with_outcome_artifact"] == 1
    assert "not_enough_data" in (rep.get("counts_by_attribution_status") or {})
    assert rep["counts_by_comparison_evidence_strength"].get("none", 0) >= 1
    assert rep.get("validation_headline")
    assert rep.get("validation_interpretation_lines") is not None


def test_validation_mixed_two_products(tmp_path: Path) -> None:
    for pid in ("a1", "b2"):
        _write_product(tmp_path, pid)
        _write_next_expansion(tmp_path, pid)
        _write_prepared(tmp_path, pid)
        _write_invoke(tmp_path, pid)
        _write_reconcile(tmp_path, pid)
        oc = build_builder_outcome_payload(tmp_path, pid)
        write_builder_outcome_artifact(tmp_path, oc)
    rep = build_builder_outcome_validation_report(tmp_path)
    assert rep["products_with_outcome_artifact"] == 2
    assert sum((rep.get("counts_by_attribution_status") or {}).values()) == 2


def test_write_and_load_artifact(tmp_path: Path) -> None:
    pid = "w1"
    _write_product(tmp_path, pid)
    _write_next_expansion(tmp_path, pid)
    _write_prepared(tmp_path, pid)
    _write_invoke(tmp_path, pid)
    _write_reconcile(tmp_path, pid)
    write_builder_outcome_artifact(tmp_path, build_builder_outcome_payload(tmp_path, pid))
    p = write_builder_outcome_validation_artifact(tmp_path)
    assert p.name == "latest.json"
    raw = load_builder_outcome_validation_latest(tmp_path)
    assert raw and raw.get("schema") == BUILDER_OUTCOME_VALIDATION_SCHEMA


def test_portfolio_write_emits_validation_artifact(tmp_path: Path) -> None:
    pid = "pa"
    _write_product(tmp_path, pid)
    _write_next_expansion(tmp_path, pid)
    _write_prepared(tmp_path, pid)
    _write_invoke(tmp_path, pid)
    _write_reconcile(tmp_path, pid)
    write_portfolio_builder_activity_artifacts(tmp_path)
    assert (tmp_path / "runs" / "portfolio" / "builder_outcome_validation" / "latest.json").is_file()
    raw = load_builder_outcome_validation_latest(tmp_path)
    assert raw and raw.get("products_with_outcome_artifact", 0) >= 1


def test_operator_summary_markdown_includes_validation_block() -> None:
    md = render_operator_summary_markdown(
        {
            "headline_status": "healthy",
            "confidence_level": "high",
            "recommended_next_step": "—",
            "lifecycle_snapshot": {},
            "learning_snapshot": {},
            "outcome_summary": {},
            "intervention_inbox_summary": {},
            "builder_activity_snapshot": {},
            "builder_outcome_validation": {
                "schema": BUILDER_OUTCOME_VALIDATION_SCHEMA,
                "disclaimer": "Observational calibration only.",
                "source_glob": "runs/builder/outcome/*/latest.json",
                "products_with_outcome_artifact": 2,
                "counts_by_attribution_status": {"not_enough_data": 2},
                "counts_by_comparison_evidence_strength": {"none": 2},
                "counts_by_observation_timing_status": {"unknown": 2},
                "counts_by_recent_observation_pattern": {"single_observation_only": 2},
                "products_repeated_negative_observations": [],
                "products_weak_current_evidence": ["a", "b"],
                "products_weak_majority_recent_window": [],
                "validation_headline": "Test headline for markdown.",
                "validation_interpretation_lines": ["Line one.", "Line two."],
                "validation_attention_level": "ordinary",
                "validation_interpretation_disclaimer": "Interp disclaimer.",
            },
        }
    )
    assert "Builder outcome validation" in md
    assert "Observational calibration only." in md
    assert "**Summary:** Test headline for markdown." in md
    assert "  - Line one." in md
    assert "Coverage hint:" in md
    assert "`ordinary`" in md
    assert "Interp disclaimer." in md
    assert "not_enough_data" in md
    assert "builder_outcome_validation/latest.json" in md


def test_operator_summary_markdown_fills_interpretation_when_missing() -> None:
    md = render_operator_summary_markdown(
        {
            "headline_status": "healthy",
            "confidence_level": "high",
            "recommended_next_step": "—",
            "lifecycle_snapshot": {},
            "learning_snapshot": {},
            "outcome_summary": {},
            "intervention_inbox_summary": {},
            "builder_activity_snapshot": {},
            "builder_outcome_validation": {
                "schema": BUILDER_OUTCOME_VALIDATION_SCHEMA,
                "disclaimer": "Observational calibration only.",
                "products_with_outcome_artifact": 0,
                "counts_by_attribution_status": {},
                "counts_by_comparison_evidence_strength": {},
                "counts_by_observation_timing_status": {},
                "counts_by_recent_observation_pattern": {},
                "products_repeated_negative_observations": [],
                "products_weak_current_evidence": [],
                "products_weak_majority_recent_window": [],
            },
        }
    )
    assert "**Summary:**" in md
    assert "`none`" in md


def test_synthetic_repeated_negative_listed(tmp_path: Path) -> None:
    """Force pattern label via minimal outcome-shaped payload."""
    d = tmp_path / "runs" / "builder" / "outcome" / "xneg"
    d.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": BUILDER_OUTCOME_SCHEMA,
        "product_id": "xneg",
        "generated_at_utc": "2026-01-01T00:00:00Z",
        "attribution_status": "possible_negative_signal_change",
        "comparison_provenance": {"comparison_window_status": "unavailable"},
        "observation_timing": {"observation_timing_status": "unknown"},
        "recent_observation_summary": {
            "recent_observation_pattern": "repeated_negative_observations",
            "recent_observation_count": 2,
            "recent_attribution_statuses": [
                "possible_negative_signal_change",
                "possible_negative_signal_change",
            ],
            "recent_evidence_strengths": ["none", "none"],
        },
        "delta_summary": "x",
        "disclaimer": "d",
    }
    write_builder_outcome_artifact(tmp_path, payload)
    rep = build_builder_outcome_validation_report(tmp_path)
    assert "xneg" in (rep.get("products_repeated_negative_observations") or [])
