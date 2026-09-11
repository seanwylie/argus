"""Tests for ``argus.dashboard.console_data`` loaders and view helpers."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from argus.dashboard.console_data import (
    AUTONOMOUS_PER_CYCLE_TABLE_MAX_ROWS,
    OPERATOR_CONSOLE_SNAPSHOT_SCHEMA,
    PORTFOLIO_AUTONOMOUS_RUNNER_SCHEMA,
    PORTFOLIO_RUNNER_SERVICE_SCHEMA,
    RUNNER_SERVICE_STALE_ACTIVE_SECONDS,
    artifact_coherence_view,
    autonomous_session_primary_status,
    autonomous_session_view,
    build_autonomous_per_cycle_table_rows,
    build_operator_console_snapshot,
    console_artifact_specs,
    intervention_inbox_view,
    learning_view,
    lifecycle_view,
    load_json_artifact,
    load_operator_console_bundle,
    needs_you_view,
    overview_from_narrative,
    overview_from_summary,
    queue_view,
    runner_service_view,
    strategy_view,
)
from argus.portfolio.escalation_inbox import ESCALATION_INBOX_SCHEMA


def test_load_missing_file(tmp_path: Path) -> None:
    art = load_json_artifact(tmp_path, "x", "runs/nope/latest.json")
    assert art.exists is False
    assert art.data is None
    assert art.error is None


def test_load_invalid_json(tmp_path: Path) -> None:
    p = tmp_path / "bad.json"
    p.write_text("{not json", encoding="utf-8")
    art = load_json_artifact(tmp_path, "x", "bad.json")
    assert art.exists is True
    assert art.data is None
    assert art.error


def test_load_non_object_root(tmp_path: Path) -> None:
    p = tmp_path / "arr.json"
    p.write_text("[1,2]", encoding="utf-8")
    art = load_json_artifact(tmp_path, "x", "arr.json")
    assert art.exists is True
    assert "not an object" in (art.error or "")


def test_path_traversal_rejected(tmp_path: Path) -> None:
    art = load_json_artifact(tmp_path, "x", "../outside.json")
    assert art.exists is False
    assert art.error == "path escapes repo root"


def test_load_valid_and_bundle(tmp_path: Path) -> None:
    spec_dir = tmp_path / "runs/dashboard/operator_summary"
    spec_dir.mkdir(parents=True)
    payload = {"schema": "test", "headline_status": "ok", "run_id": "r1"}
    (spec_dir / "latest.json").write_text(json.dumps(payload), encoding="utf-8")

    bundle = load_operator_console_bundle(tmp_path)
    assert bundle["operator_summary"].data == payload
    assert bundle["narrative"].exists is False


def test_build_operator_console_snapshot_covers_all_tabs(tmp_path: Path) -> None:
    bundle = load_operator_console_bundle(tmp_path)
    snap = build_operator_console_snapshot(tmp_path, bundle)
    assert snap["schema"] == OPERATOR_CONSOLE_SNAPSHOT_SCHEMA
    assert snap["artifact_specs"] == console_artifact_specs()
    assert set(snap["artifacts"].keys()) == set(console_artifact_specs().keys())
    assert snap["artifacts"]["operator_summary"]["exists"] is False
    assert "generated_at_utc" in snap
    assert "repo_root" in snap
    assert "views" in snap
    assert snap["views"]["artifact_coherence"]["present"] is False
    assert snap["views"]["world_context"]["present"] is False


def test_artifact_coherence_view_degraded_invalid_and_missing() -> None:
    from argus.portfolio.artifact_coherence import ARTIFACT_COHERENCE_REPORT_SCHEMA

    assert artifact_coherence_view(None)["present"] is False
    v = artifact_coherence_view(
        {
            "schema": ARTIFACT_COHERENCE_REPORT_SCHEMA,
            "overall_status": "degraded",
            "run_id": "r1",
            "evaluated_at_utc": "t",
            "summary": "s",
            "checks": {"x": {"status": "warn"}},
        }
    )
    assert v["present"] is True
    assert v["ui_severity"] == "caution"
    assert v["checks_preview"]
    inv = artifact_coherence_view(
        {
            "schema": ARTIFACT_COHERENCE_REPORT_SCHEMA,
            "overall_status": "invalid",
            "checks": {"a": {"status": "fail"}},
        }
    )
    assert inv["ui_severity"] == "invalid"


def test_overview_from_summary() -> None:
    ov = overview_from_summary({"headline_status": "green", "run_id": "x"})
    assert ov["empty"] is False
    assert ov["headline_status"] == "green"
    ov2 = overview_from_summary(
        {
            "headline_status": "portfolio_empty",
            "zero_state": True,
            "external_context_advisory": "1 signal. Fresh: 1, stale: 0.",
            "world_context_present": True,
        }
    )
    assert ov2["external_context_advisory"] is not None
    assert ov2["world_context_present"] is True


def test_overview_from_summary_includes_situation_brief() -> None:
    ov = overview_from_summary(
        {
            "headline_status": "portfolio_empty",
            "zero_state": True,
            "external_context_situation_brief": "Short situation line.",
            "external_context_advisory": "Longer advisory narrative.",
        }
    )
    assert ov["external_context_situation_brief"] == "Short situation line."


def test_queue_view_truncates_entries() -> None:
    entries = [{"product_id": f"p{i}", "queue_rank": i} for i in range(60)]
    qv = queue_view({"entries": entries, "generated_at_utc": "t"})
    assert qv["entry_count"] == 60
    assert len(qv["entries"]) == 50


def test_lifecycle_view_reads_nested_summary() -> None:
    data = {
        "portfolio_lifecycle_summary": {"narrative": "hello"},
        "lifecycle_counts": {"a": 1},
    }
    lv = lifecycle_view(data)
    assert lv["summary_narrative"] == "hello"
    assert lv["lifecycle_counts"] == {"a": 1}


def test_learning_view_caps_lessons() -> None:
    lessons = [f"L{i}" for i in range(30)]
    lv = learning_view({"top_lessons_so_far": lessons})
    assert len(lv["top_lessons"]) == 16


def test_strategy_view() -> None:
    sv = strategy_view({"strategic_posture": "hold", "rationale": ["a"]})
    assert sv["strategic_posture"] == "hold"


def test_intervention_inbox_view_counts() -> None:
    items = [
        {"severity": "high", "in_active_queue": True},
        {"severity": "low", "in_active_queue": False},
    ]
    iv = intervention_inbox_view({"open_items": items, "built_at_utc": "t"})
    assert iv["open_items_count"] == 2
    assert iv["active_in_queue"] == 1
    assert iv["high_severity_active"] == 1


def test_overview_from_narrative_preview() -> None:
    text = "x" * 1000
    nv = overview_from_narrative({"narrative_text": text, "sections": {"overall_trajectory": "up"}})
    assert len(nv["narrative_preview"]) == 800
    assert nv["overall_trajectory"] == "up"


def test_autonomous_session_view_empty_and_invalid_schema() -> None:
    assert autonomous_session_view(None)["empty"] is True
    assert autonomous_session_view({"schema": "wrong"})["empty"] is True
    assert autonomous_session_view({}).get("note")


def _sample_cycle_row(
    idx: int,
    *,
    overall: str = "continue",
    quiescence: str = "observe",
    mat: int = 1,
    flagged: int = 0,
) -> dict:
    return {
        "cycle_index": idx,
        "portfolio_refresh": {"exit_code": 0, "ok": True, "schema": "argus.portfolio_refresh.v1"},
        "portfolio_cycle": {
            "cycle_run_id": f"rid{idx}",
            "ok": True,
            "quiescence_recommendation": quiescence,
            "overall_operator_recommendation": overall,
            "products_with_material_change_count": mat,
            "intervention_flagged_count": flagged,
        },
        "portfolio_lifecycle": {"status": "ok", "run_id": "l1", "schema": "argus.portfolio_lifecycle.v1"},
        "operator_summary": {"status": "ok", "run_id": "s1", "headline_status": "ok"},
        "operator_narrative": {"status": "ok", "run_id": "n1"},
    }


def test_build_autonomous_per_cycle_table_rows_extraction() -> None:
    payload = {
        "stop_reason": "max_cycles_reached",
        "per_cycle_outcomes": [
            _sample_cycle_row(1, overall="continue", mat=2),
            _sample_cycle_row(2, overall="pause", mat=0),
        ],
        "promotion_execution": {
            "steps": [
                {
                    "kind": "deprecation_proposal_to_plan",
                    "result_status": "success",
                    "detail": "x",
                }
            ],
        },
    }
    tab = build_autonomous_per_cycle_table_rows(payload)
    assert tab["empty"] is False
    assert tab["total"] == 2
    assert tab["truncated"] is False
    assert len(tab["rows"]) == 2
    assert tab["rows"][0]["Cycle"] == 1
    assert "pause" in tab["rows"][1]["Outcome"] or "quiescence" in tab["rows"][1]["Outcome"].lower()
    assert tab["rows"][1]["Promotions"] != "—"
    assert "stop=max_cycles_reached" in tab["rows"][1]["Notes"]


def test_build_autonomous_per_cycle_table_rows_truncates_last_n() -> None:
    n = AUTONOMOUS_PER_CYCLE_TABLE_MAX_ROWS + 4
    payload = {
        "stop_reason": "max_cycles_reached",
        "per_cycle_outcomes": [_sample_cycle_row(i) for i in range(1, n + 1)],
        "promotion_execution": {},
    }
    tab = build_autonomous_per_cycle_table_rows(payload)
    assert tab["total"] == n
    assert tab["truncated"] is True
    assert len(tab["rows"]) == AUTONOMOUS_PER_CYCLE_TABLE_MAX_ROWS
    assert tab["rows"][0]["Cycle"] == 5  # 1..14 -> show 5..14
    assert tab["rows"][-1]["Cycle"] == n


def test_build_autonomous_per_cycle_table_rows_missing_and_mismatch() -> None:
    assert build_autonomous_per_cycle_table_rows({})["empty"] is True
    assert build_autonomous_per_cycle_table_rows({"per_cycle_outcomes": None})["empty"] is True
    bad = build_autonomous_per_cycle_table_rows({"per_cycle_outcomes": "not-a-list"})
    assert bad["empty"] is True
    assert bad["schema_mismatch_items"] == 1
    mix = build_autonomous_per_cycle_table_rows(
        {"per_cycle_outcomes": [_sample_cycle_row(1), "not-dict", _sample_cycle_row(2)]}
    )
    assert mix["total"] == 2
    assert mix["schema_mismatch_items"] == 1


def test_build_autonomous_per_cycle_table_rows_truncates_long_outcome() -> None:
    row = _sample_cycle_row(1)
    row["portfolio_cycle"]["overall_operator_recommendation"] = "x" * 400
    tab = build_autonomous_per_cycle_table_rows({"stop_reason": "max_cycles_reached", "per_cycle_outcomes": [row]})
    assert len(tab["rows"][0]["Outcome"]) <= 201


def test_build_autonomous_per_cycle_table_rows_refresh_failed() -> None:
    tab = build_autonomous_per_cycle_table_rows(
        {
            "stop_reason": "portfolio_refresh_failed",
            "per_cycle_outcomes": [
                {
                    "cycle_index": 1,
                    "portfolio_refresh": {"exit_code": 1, "ok": False, "error": "bad"},
                    "portfolio_cycle": None,
                }
            ],
        }
    )
    assert tab["rows"][0]["Outcome"] == "refresh_failed"
    assert "refresh✗" in tab["rows"][0]["Actions"]


def test_autonomous_session_view_shapes_promotions() -> None:
    payload = {
        "schema": PORTFOLIO_AUTONOMOUS_RUNNER_SCHEMA,
        "session_id": "s1",
        "started_at_utc": "a",
        "finished_at_utc": "b",
        "cycles_run": 2,
        "stop_reason": "intervention_heavy_streak",
        "stop_reason_codes": ["x"],
        "session_summary": "sum",
        "lifecycle_session_influence": {
            "primary_signal": "repair_pressure",
            "session_notes": ["n1"],
            "priority_hints": ["h1"],
        },
        "promotable_actions": [
            {
                "kind": "creation_proposal_to_scaffold",
                "proposal_id": "p1",
                "derived_product_id": "slug",
                "safe_for_auto": True,
            }
        ],
        "blocked_promotions": [
            {"kind": "deprecation_proposal_to_plan", "product_id": "z", "reason": "exists"},
        ],
        "promotion_recommendations": ["do thing"],
        "promotion_execution": {
            "allow_promotion": True,
            "effective_dry_run": False,
            "steps": [
                {
                    "kind": "deprecation_proposal_to_plan",
                    "result_status": "success",
                    "detail": "ok",
                    "promotion": {"result_status": "success", "nested_results": {"deprecation_plan": {"ok": True}}},
                }
            ],
        },
        "artifacts_refreshed": ["runs/portfolio/cycle/latest.json"],
        "inputs": {"dry_run": False, "allow_promotion": True},
        "per_cycle_outcomes": [{"cycle_index": 1}],
    }
    av = autonomous_session_view(payload)
    assert av["empty"] is False
    assert av["primary_status"] == "stopped_intervention_heavy"
    assert av["promotable_actions_rows"][0]["kind"] == "creation_proposal_to_scaffold"
    assert av["blocked_promotions_rows"][0]["reason"] == "exists"
    assert av["promotion_execution_rows"]
    assert av.get("per_cycle_table_rows")


def test_autonomous_session_primary_status_mapping() -> None:
    assert autonomous_session_primary_status("max_cycles_reached") == "completed"
    assert autonomous_session_primary_status("quiescence_recommendation") == "stopped_quiescent"
    assert autonomous_session_primary_status("portfolio_refresh_failed") == "stopped_pipeline_error"


def test_bundle_includes_autonomous_runner_key(tmp_path: Path) -> None:
    bundle = load_operator_console_bundle(tmp_path)
    assert "autonomous_runner" in bundle
    assert bundle["autonomous_runner"].exists is False


def test_bundle_includes_escalation_inbox_key(tmp_path: Path) -> None:
    bundle = load_operator_console_bundle(tmp_path)
    assert "escalation_inbox" in bundle
    assert bundle["escalation_inbox"].exists is False


def test_bundle_includes_runner_service_key(tmp_path: Path) -> None:
    bundle = load_operator_console_bundle(tmp_path)
    assert "runner_service" in bundle
    assert bundle["runner_service"].exists is False


def test_bundle_includes_artifact_coherence_key(tmp_path: Path) -> None:
    bundle = load_operator_console_bundle(tmp_path)
    assert "artifact_coherence" in bundle
    assert bundle["artifact_coherence"].exists is False


def _runner_base(**kwargs: object) -> dict:
    pl = {
        "schema": PORTFOLIO_RUNNER_SERVICE_SCHEMA,
        "service_run_id": "svc1",
        "updated_at_utc": "2026-04-01T12:00:00Z",
        "current_status": "stopped",
        "service_started_at_utc": "2026-04-01T11:00:00Z",
        "service_finished_at_utc": "2026-04-01T12:00:00Z",
        "last_run_started_at_utc": "2026-04-01T11:30:00Z",
        "last_run_finished_at_utc": "2026-04-01T11:55:00Z",
        "last_session_id": "sess_a",
        "last_autonomous_stop_reason": "max_cycles_reached",
        "next_planned_run_at_utc": None,
        "loop_count": 1,
        "stop_reason": "completed",
        "stop_reason_codes": ["runner_service.stop.one_shot"],
        "last_run_summary": "done",
        "inputs": {"interval_seconds": 0.0},
    }
    pl.update(kwargs)
    return pl


def test_runner_service_view_missing_and_invalid_schema() -> None:
    assert runner_service_view(None)["empty"] is True
    assert runner_service_view({"schema": "wrong"})["empty"] is True
    assert runner_service_view({}).get("note")


def test_runner_service_view_running_fresh() -> None:
    ref = datetime(2026, 4, 1, 12, 0, 0, tzinfo=timezone.utc)
    pl = _runner_base(
        current_status="running",
        updated_at_utc="2026-04-01T12:00:00Z",
        stop_reason="",
        stop_reason_codes=[],
    )
    rv = runner_service_view(pl, reference_now=ref + timedelta(seconds=60))
    assert rv["empty"] is False
    assert rv["stale_suspected"] is False
    assert rv["current_status"] == "running"


def test_runner_service_view_stale_running() -> None:
    ref = datetime(2026, 4, 1, 12, 0, 0, tzinfo=timezone.utc)
    pl = _runner_base(
        current_status="running",
        updated_at_utc="2026-04-01T12:00:00Z",
        stop_reason="",
        stop_reason_codes=[],
    )
    rv = runner_service_view(
        pl,
        reference_now=ref + timedelta(seconds=RUNNER_SERVICE_STALE_ACTIVE_SECONDS + 120),
    )
    assert rv["stale_suspected"] is True
    assert rv["stale_note"]
    assert "heartbeat_stale" in (rv.get("status_badges") or [])


def test_runner_service_view_stopped_with_reason() -> None:
    pl = _runner_base(
        current_status="stopped",
        stop_reason="stop_sentinel",
        stop_reason_codes=["runner_service.stop.sentinel_file"],
    )
    rv = runner_service_view(pl)
    assert rv["empty"] is False
    assert rv["stop_reason"] == "stop_sentinel"
    assert any("stop:stop_sentinel" in b for b in (rv.get("status_badges") or []))


def test_runner_service_view_one_shot_complete_idle() -> None:
    pl = _runner_base(
        current_status="stopped",
        stop_reason="completed",
        loop_count=1,
        next_planned_run_at_utc=None,
        inputs={"interval_seconds": 0.0},
    )
    rv = runner_service_view(pl)
    assert rv["no_next_run_scheduled"] is True
    assert rv["stop_reason"] == "completed"


def test_runner_service_view_embedded_autonomous_flag() -> None:
    pl = _runner_base(last_autonomous_session={"schema": "argus.portfolio_autonomous_runner.v1"})
    rv = runner_service_view(pl)
    assert rv["has_embedded_autonomous_session"] is True


def test_needs_you_view_missing_and_invalid_schema() -> None:
    assert needs_you_view(None)["empty"] is True
    assert needs_you_view({"schema": "wrong"})["empty"] is True
    assert needs_you_view({}).get("note")


def test_needs_you_view_empty_inbox() -> None:
    nv = needs_you_view({"schema": ESCALATION_INBOX_SCHEMA, "open_items": [], "built_at_utc": "t"})
    assert nv["empty"] is False
    assert nv["total_items"] == 0
    assert nv["no_actionable_items"] is True


def test_needs_you_view_actionable_vs_informational() -> None:
    pl = {
        "schema": ESCALATION_INBOX_SCHEMA,
        "built_at_utc": "t",
        "open_items": [
            {
                "item_id": "a1",
                "category": "review_needed",
                "severity": "high",
                "product_id": "p1",
                "requires_operator_action": True,
                "in_active_queue": True,
                "evidence_summary": "e1",
                "requested_action": "fix",
                "source": "autonomous_runner",
                "ack_state": "open",
            },
            {
                "item_id": "a2",
                "category": "informational",
                "severity": "low",
                "requires_operator_action": False,
                "in_active_queue": False,
                "evidence_summary": "e2",
                "requested_action": "none",
                "source": "autonomous_runner",
                "ack_state": "open",
            },
            {
                "item_id": "a3",
                "category": "review_needed",
                "severity": "medium",
                "requires_operator_action": True,
                "in_active_queue": False,
                "ack_state": "resolved",
                "evidence_summary": "e3",
                "requested_action": "done",
                "source": "x",
            },
        ],
    }
    nv = needs_you_view(pl)
    assert nv["actionable_count"] == 1
    assert nv["informational_count"] >= 1
    assert nv["settled_count"] >= 1
    assert len(nv["actionable_rows"]) == 1
    assert len(nv["actionable_guidance"]) == 1
    assert nv["actionable_guidance"][0].get("blocker_label")
    assert nv["category_counts_all"].get("review_needed") == 2


def test_needs_you_view_mixed_categories_counts() -> None:
    pl = {
        "schema": ESCALATION_INBOX_SCHEMA,
        "open_items": [
            {
                "item_id": "x",
                "category": "external_dependency",
                "severity": "medium",
                "requires_operator_action": True,
                "in_active_queue": True,
                "evidence_summary": "",
                "requested_action": "",
                "source": "s",
                "ack_state": "open",
            },
            {
                "item_id": "y",
                "category": "unsafe_to_continue",
                "severity": "critical",
                "requires_operator_action": True,
                "in_active_queue": True,
                "evidence_summary": "",
                "requested_action": "",
                "source": "s",
                "ack_state": "open",
            },
        ],
    }
    nv = needs_you_view(pl)
    assert nv["actionable_count"] == 2
    assert nv["category_counts_actionable"].get("external_dependency") == 1
    assert nv["severity_counts_actionable"].get("critical") == 1
