"""Escalation inbox (autonomy boundary) — build, classify, and operator actions."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from argus.portfolio.autonomous_runner import (
    PORTFOLIO_AUTONOMOUS_RUNNER_SCHEMA,
    portfolio_autonomous_runner_dir,
)
from argus.portfolio.escalation_inbox import (
    ACK_STATE_ACKNOWLEDGED,
    ACK_STATE_RESOLVED,
    ACK_STATE_SNOOZED,
    CATEGORY_APPROVAL_NEEDED,
    CATEGORY_EXTERNAL_DEPENDENCY,
    CATEGORY_INFORMATIONAL,
    CATEGORY_UNSAFE_TO_CONTINUE,
    ESCALATION_INBOX_SCHEMA,
    build_escalation_inbox_payload,
    merge_escalation_actions,
    record_escalation_acknowledge,
    record_escalation_resolve,
    record_escalation_snooze,
    render_escalation_inbox_markdown,
    run_escalation_inbox,
)
from argus.portfolio.intervention import (
    INTERVENTION_EVIDENCE_REFRESH,
    PORTFOLIO_INTERVENTION_SCHEMA,
    portfolio_intervention_dir,
)
from argus.portfolio.operator_queue import OPERATOR_QUEUE_SCHEMA
from argus.portfolio.scheduler import PORTFOLIO_SCHEDULER_SESSION_SCHEMA


def _write_autonomous(
    root: Path,
    *,
    stop_reason: str = "max_cycles_reached",
    codes: list[str] | None = None,
    blocked: list[dict] | None = None,
    session_id: str = "sess1",
) -> None:
    d = portfolio_autonomous_runner_dir(root)
    d.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": PORTFOLIO_AUTONOMOUS_RUNNER_SCHEMA,
        "session_id": session_id,
        "finished_at_utc": "2026-01-01T00:00:00Z",
        "stop_reason": stop_reason,
        "stop_reason_codes": codes or [],
        "blocked_promotions": blocked or [],
    }
    (d / "latest.json").write_text(json.dumps(payload), encoding="utf-8")


def _write_cycle(root: Path, overall: str) -> None:
    d = root / "runs" / "portfolio" / "cycle"
    d.mkdir(parents=True, exist_ok=True)
    pl = {
        "schema": "argus.portfolio_cycle.v1",
        "run_id": "cyc1",
        "summary": {"overall_operator_recommendation": overall},
    }
    (d / "latest.json").write_text(json.dumps(pl), encoding="utf-8")


def _intervention_report(run_id: str, rows: list[dict]) -> dict:
    return {
        "schema": PORTFOLIO_INTERVENTION_SCHEMA,
        "run_id": run_id,
        "evaluated_at_utc": "2026-04-01T00:00:00Z",
        "inputs": {},
        "thresholds": {},
        "flagged_products": rows,
        "stable_benign_products": [],
    }


def _write_scheduler(root: Path, stop_reason: str, codes: list[str]) -> None:
    d = root / "runs" / "portfolio" / "scheduler"
    d.mkdir(parents=True, exist_ok=True)
    pl = {
        "schema": PORTFOLIO_SCHEDULER_SESSION_SCHEMA,
        "session_id": "sch1",
        "finished_at_utc": "2026-01-01T00:00:00Z",
        "stop_reason": stop_reason,
        "stop_reason_codes": codes,
    }
    (d / "latest.json").write_text(json.dumps(pl), encoding="utf-8")


def _write_operator_queue(
    root: Path,
    *,
    product_id: str = "llm-consensus-engine",
    orchestration_status: str = "waiting_inputs",
    next_action: str = "signals_collect",
    recommendation: str = "",
    evidence_maturity_hint: str = "",
    queue_rank: int = 1,
) -> None:
    d = root / "runs" / "portfolio" / "operator_queue"
    d.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": OPERATOR_QUEUE_SCHEMA,
        "generated_at_utc": "2026-01-01T00:00:00Z",
        "entries": [
            {
                "product_id": product_id,
                "queue_rank": queue_rank,
                "orchestration_status": orchestration_status,
                "next_action": next_action,
                "recommendation": recommendation,
                "evidence_maturity_hint": evidence_maturity_hint,
            }
        ],
    }
    (d / "latest.json").write_text(json.dumps(payload), encoding="utf-8")


def test_blocked_duplicate_is_escalation_worthy(tmp_path: Path) -> None:
    _write_autonomous(
        tmp_path,
        blocked=[
            {
                "kind": "creation_proposal_to_scaffold",
                "proposal_id": "p1",
                "reason": "refusing to overwrite existing product directory: /x",
            }
        ],
    )
    pl = build_escalation_inbox_payload(tmp_path, recent_session_limit=3)
    assert pl["schema"] == ESCALATION_INBOX_SCHEMA
    bp = [x for x in pl["open_items"] if x.get("source") == "blocked_promotion" and x.get("requires_operator_action")]
    assert bp and bp[0].get("category") == "review_needed"


def test_routine_deprecation_plan_exists_is_informational_not_active(tmp_path: Path) -> None:
    _write_autonomous(
        tmp_path,
        blocked=[
            {
                "kind": "deprecation_proposal_to_plan",
                "product_id": "foo",
                "proposal_id": "d1",
                "reason": "deprecation plan artifact already exists for this product",
            }
        ],
    )
    pl = build_escalation_inbox_payload(tmp_path)
    info = [x for x in pl["open_items"] if x.get("category") == CATEGORY_INFORMATIONAL]
    assert info
    assert not any(x.get("in_active_queue") for x in info if not x.get("requires_operator_action"))


def test_unsafe_autonomous_pipeline_stop(tmp_path: Path) -> None:
    _write_autonomous(tmp_path, stop_reason="portfolio_refresh_failed", codes=["e"])
    pl = build_escalation_inbox_payload(tmp_path)
    row = next(x for x in pl["open_items"] if x.get("source") == "autonomous_runner" and "portfolio_refresh" in str(x.get("evidence_summary")))
    assert row.get("category") == CATEGORY_UNSAFE_TO_CONTINUE
    assert row.get("severity") == "critical"


def test_external_dependency_quiescence_wait(tmp_path: Path) -> None:
    _write_autonomous(
        tmp_path,
        stop_reason="quiescence_recommendation",
        codes=["autonomous_runner.stop.quiescence.wait"],
    )
    pl = build_escalation_inbox_payload(tmp_path)
    row = next(x for x in pl["open_items"] if x.get("source") == "autonomous_runner")
    assert row.get("category") == CATEGORY_EXTERNAL_DEPENDENCY


def test_ack_resolve_snooze(tmp_path: Path) -> None:
    _write_autonomous(tmp_path, stop_reason="intervention_heavy_streak", codes=["x"])
    pl0 = build_escalation_inbox_payload(tmp_path)
    row = next(x for x in pl0["open_items"] if x.get("item_id", "").startswith("esc-auto"))
    iid = row["item_id"]

    record_escalation_acknowledge(tmp_path, iid, note="seen")
    merged = merge_escalation_actions(tmp_path)
    assert merged[iid]["ack_state"] == ACK_STATE_ACKNOWLEDGED

    record_escalation_snooze(tmp_path, iid, days=30.0)
    merged2 = merge_escalation_actions(tmp_path)
    assert merged2[iid]["ack_state"] == ACK_STATE_SNOOZED

    record_escalation_resolve(tmp_path, iid)
    merged3 = merge_escalation_actions(tmp_path)
    assert merged3[iid]["ack_state"] == ACK_STATE_RESOLVED

    pl = build_escalation_inbox_payload(tmp_path)
    r2 = next(x for x in pl["open_items"] if x.get("item_id") == iid)
    assert r2.get("reopened_after_resolve") is True


def test_recurring_blocked_promotion(tmp_path: Path) -> None:
    d = portfolio_autonomous_runner_dir(tmp_path)
    d.mkdir(parents=True, exist_ok=True)
    blocked = [
        {
            "kind": "creation_proposal_to_scaffold",
            "proposal_id": "p1",
            "reason": "refusing to overwrite existing product directory",
        }
    ]
    for name, sid in [("20260101T000001Z.json", "a"), ("20260101T000002Z.json", "b")]:
        payload = {
            "schema": PORTFOLIO_AUTONOMOUS_RUNNER_SCHEMA,
            "session_id": sid,
            "stop_reason": "max_cycles_reached",
            "stop_reason_codes": [],
            "blocked_promotions": blocked,
        }
        (d / name).write_text(json.dumps(payload), encoding="utf-8")
    payload = {
        "schema": PORTFOLIO_AUTONOMOUS_RUNNER_SCHEMA,
        "session_id": "c",
        "stop_reason": "max_cycles_reached",
        "stop_reason_codes": [],
        "blocked_promotions": blocked,
    }
    (d / "latest.json").write_text(json.dumps(payload), encoding="utf-8")

    pl = build_escalation_inbox_payload(tmp_path, recent_session_limit=10)
    br = next(x for x in pl["open_items"] if x.get("source") == "blocked_promotion" and x.get("requires_operator_action"))
    assert br.get("recurring") is True
    assert int(br.get("seen_in_run_count") or 0) >= 2


def test_portfolio_cycle_stop_item(tmp_path: Path) -> None:
    _write_cycle(tmp_path, "request_human_review")
    pl = build_escalation_inbox_payload(tmp_path)
    assert any(x.get("source") == "portfolio_cycle" for x in pl["open_items"])


def test_scheduler_stop_surface(tmp_path: Path) -> None:
    _write_scheduler(tmp_path, "portfolio_refresh_failed", ["y"])
    pl = build_escalation_inbox_payload(tmp_path)
    assert any(x.get("source") == "scheduler" for x in pl["open_items"])


def test_run_writes_artifacts(tmp_path: Path) -> None:
    _write_autonomous(tmp_path)
    run_escalation_inbox(tmp_path, write_artifacts=True)
    assert (tmp_path / "runs" / "portfolio" / "escalation_inbox" / "latest.json").is_file()
    assert (tmp_path / "runs" / "portfolio" / "escalation_inbox" / "latest.md").is_file()


def test_medium_evidence_refresh_recurring_unchanged_not_promoted_to_escalation(tmp_path: Path) -> None:
    d = portfolio_intervention_dir(tmp_path)
    d.mkdir(parents=True)
    row = {
        "product_id": "alpha",
        "intervention_category": INTERVENTION_EVIDENCE_REFRESH,
        "severity": "medium",
        "detection_reason_codes": ["intervention.repeated_same_next_action_no_readiness_gain"],
        "evidence_summary": "stale",
        "recommended_operator_action": "refresh",
        "chronicity": "emerging",
    }
    (d / "20260401T000000Z.json").write_text(json.dumps(_intervention_report("20260401T000000Z", [row])), encoding="utf-8")
    (d / "20260402T000000Z.json").write_text(json.dumps(_intervention_report("20260402T000000Z", [row])), encoding="utf-8")
    (d / "latest.json").write_text(json.dumps(_intervention_report("20260402T000000Z", [row])), encoding="utf-8")
    pl = build_escalation_inbox_payload(tmp_path, recent_session_limit=3)
    esc_inv = [x for x in pl["open_items"] if str(x.get("source") or "") == "intervention_inbox"]
    assert not esc_inv


def test_medium_evidence_refresh_detection_change_still_promoted_to_escalation(tmp_path: Path) -> None:
    d = portfolio_intervention_dir(tmp_path)
    d.mkdir(parents=True)
    base = {
        "product_id": "alpha",
        "intervention_category": INTERVENTION_EVIDENCE_REFRESH,
        "detection_reason_codes": ["intervention.repeated_same_next_action_no_readiness_gain"],
        "evidence_summary": "stale",
        "recommended_operator_action": "refresh",
        "chronicity": "emerging",
    }
    (d / "20260401T000000Z.json").write_text(
        json.dumps(_intervention_report("20260401T000000Z", [{**base, "severity": "medium"}])),
        encoding="utf-8",
    )
    (d / "20260402T000000Z.json").write_text(
        json.dumps(_intervention_report("20260402T000000Z", [{**base, "severity": "high"}])),
        encoding="utf-8",
    )
    (d / "latest.json").write_text(
        json.dumps(_intervention_report("20260402T000000Z", [{**base, "severity": "high"}])),
        encoding="utf-8",
    )
    pl = build_escalation_inbox_payload(tmp_path, recent_session_limit=3)
    esc_inv = [x for x in pl["open_items"] if str(x.get("source") or "") == "intervention_inbox"]
    assert esc_inv and esc_inv[0].get("severity") == "high"


@patch("argus.portfolio.escalation_inbox.build_intervention_inbox_payload")
def test_intervention_subset_escalates(mock_iv, tmp_path: Path) -> None:
    mock_iv.return_value = {
        "schema": "argus.intervention_inbox.v1",
        "source_intervention_run_id": "ir1",
        "open_items": [
            {
                "item_id": "inv-x",
                "product_id": "prod1",
                "in_active_queue": True,
                "severity": "high",
                "intervention_category": "stuck_loop",
                "evidence_summary": "e",
                "recommended_operator_action": "fix",
                "detection_fingerprint": "fp",
                "first_seen_run_id": "a",
                "last_seen_run_id": "b",
                "seen_in_run_count": 2,
                "recurring": True,
                "evidence_unchanged_across_last_two_intervention_runs": False,
            }
        ],
    }
    pl = build_escalation_inbox_payload(tmp_path)
    assert any(x.get("item_id", "").startswith("esc-inv-") for x in pl["open_items"])


def test_operator_escalation_enrichment_quiescence_wait(tmp_path: Path) -> None:
    _write_operator_queue(
        tmp_path,
        product_id="llm-consensus-engine",
        orchestration_status="waiting_inputs",
        next_action="signals_collect",
    )
    _write_autonomous(
        tmp_path,
        stop_reason="quiescence_recommendation",
        codes=["autonomous_runner.stop.quiescence.wait"],
    )
    pl = build_escalation_inbox_payload(tmp_path)
    assert pl["inputs"].get("operator_queue_context_loaded") is True
    row = next(x for x in pl["open_items"] if x.get("source") == "autonomous_runner")
    assert row.get("human_readable_state")
    assert row.get("root_cause_summary")
    assert row.get("recommended_operator_action")
    assert row.get("primary_product") == "llm-consensus-engine"
    assert "llm-consensus-engine" in row["recommended_operator_action"]
    assert "required operability" in row["human_readable_state"].lower()
    assert "golden" in row["recommended_operator_action"].lower()


def test_operator_escalation_enrichment_inspect_posture_review_products(tmp_path: Path) -> None:
    _write_operator_queue(tmp_path, product_id="fixture-one", orchestration_status="inspect_needed")
    _write_cycle(tmp_path, "inspect_specific_products")
    pl = build_escalation_inbox_payload(tmp_path)
    row = next(x for x in pl["open_items"] if x.get("source") == "portfolio_cycle")
    assert row.get("primary_product") == "fixture-one"
    assert "fixture-one" in row["recommended_operator_action"].lower()
    assert "inspect" in row["human_readable_state"].lower()


def test_operator_escalation_enrichment_stale_refresh_nudge(tmp_path: Path) -> None:
    _write_operator_queue(
        tmp_path,
        product_id="alpha-prod",
        orchestration_status="stale_refresh_needed",
        next_action="signals_refresh",
    )
    _write_autonomous(
        tmp_path,
        stop_reason="quiescence_recommendation",
        codes=["autonomous_runner.stop.quiescence.wait"],
    )
    pl = build_escalation_inbox_payload(tmp_path)
    row = next(x for x in pl["open_items"] if x.get("source") == "autonomous_runner")
    act = row["recommended_operator_action"].lower()
    assert "alpha-prod" in act
    assert "refresh" in act or "signals" in act


def test_operator_escalation_enrichment_approval_needed(tmp_path: Path) -> None:
    _write_operator_queue(tmp_path, product_id="queue-top")
    _write_autonomous(
        tmp_path,
        stop_reason="cycle_overall_recommendation",
        codes=["portfolio.cycle.inspect_specific_products"],
    )
    pl = build_escalation_inbox_payload(tmp_path)
    row = next(
        x
        for x in pl["open_items"]
        if x.get("source") == "autonomous_runner" and x.get("category") == CATEGORY_APPROVAL_NEEDED
    )
    assert "Approval gate" in row["recommended_operator_action"]
    assert "queue-top" in row["recommended_operator_action"]


def test_escalation_markdown_includes_state_cause_action_sections(tmp_path: Path) -> None:
    _write_operator_queue(tmp_path)
    _write_autonomous(
        tmp_path,
        stop_reason="quiescence_recommendation",
        codes=["autonomous_runner.stop.quiescence.wait"],
    )
    pl = build_escalation_inbox_payload(tmp_path)
    md = render_escalation_inbox_markdown(pl)
    assert "### Actionable detail" in md
    assert "**State:**" in md
    assert "**Cause:**" in md
    assert "**Recommended action:**" in md


def _write_builder_escalation_packet(
    tmp_path: Path,
    *,
    product_id: str = "p1",
    packet_id: str = "esc_20260416T120000Z_p1",
    risk_level: str = "high",
) -> None:
    d = tmp_path / "runs" / "escalations" / "latest"
    d.mkdir(parents=True, exist_ok=True)
    payload = {
        "packet_id": packet_id,
        "product_id": product_id,
        "risk_level": risk_level,
        "title": "Builder anomaly: p1",
        "summary": "summary",
        "why_stopped": "- [builder_x] path_scope touched argus/core",
        "created_at": "2026-04-16T12:00:00Z",
        "metadata": {"builder_escalation": True},
    }
    (d / f"{packet_id}.json").write_text(json.dumps(payload), encoding="utf-8")


def test_builder_escalation_critical_mirrored_to_inbox(tmp_path: Path) -> None:
    _write_builder_escalation_packet(tmp_path, risk_level="critical")
    pl = build_escalation_inbox_payload(tmp_path)
    assert "builder_escalation" in (pl.get("inputs") or {}).get("sources", [])
    row = next(x for x in pl["open_items"] if x.get("source") == "builder_escalation")
    assert row.get("severity") == "critical"
    assert row.get("category") == CATEGORY_UNSAFE_TO_CONTINUE
    assert row.get("item_id") == "esc-bld-esc_20260416T120000Z_p1"
    pl2 = build_escalation_inbox_payload(tmp_path)
    assert len([x for x in pl2["open_items"] if x.get("source") == "builder_escalation"]) == 1


def test_builder_escalation_medium_not_in_inbox(tmp_path: Path) -> None:
    _write_builder_escalation_packet(tmp_path, packet_id="esc_med", risk_level="medium")
    pl = build_escalation_inbox_payload(tmp_path)
    assert not any(x.get("source") == "builder_escalation" for x in pl["open_items"])
