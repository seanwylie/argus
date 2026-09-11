"""Phase 2B: compact operator-facing Builder summary (CLI / JSON / dashboard)."""

from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path

import pytest

from argus.builder.status import (
    build_builder_operator_summary,
    compute_builder_status,
    format_builder_operator_summary_human,
)
from argus.cli.builder_cmd import run_builder_subcommand
from tests.test_builder_status import (
    _write_invoke,
    _write_next_expansion,
    _write_prepared,
    _write_reconcile,
)


def test_operator_summary_in_status_payload_aligned(tmp_path: Path) -> None:
    pid = "op1"
    _write_next_expansion(tmp_path, pid)
    _write_prepared(tmp_path, pid)
    _write_invoke(tmp_path, pid)
    _write_reconcile(tmp_path, pid)
    out = compute_builder_status(tmp_path, pid)
    osum = out.get("operator_summary") or {}
    assert osum.get("schema") == "argus.builder_operator_summary.v1"
    assert osum.get("has_builder_activity") is True
    assert osum.get("alignment_summary") == "aligned"
    assert "Declared target" in (osum.get("alignment_headline") or "")
    assert osum.get("merge_readiness") == "blocked"
    assert osum.get("trust_posture") == "blocked"
    assert osum.get("recommended_next_action") == "inspect_diff"
    assert (osum.get("trust_operator_view") or {}).get("schema") == "argus.builder_trust_operator_view.v1"
    human = format_builder_operator_summary_human(out)
    assert "=== Builder — op1 ===" in human
    assert "Merge readiness: blocked" in human
    assert "### Trust & next" in human
    assert "Trust — Blocked" in human
    assert "Next — Inspect diff" in human or "Inspect diff" in human
    assert "`trust_posture=blocked`" in human
    assert "`recommended_next_action=inspect_diff`" in human
    assert "Review-only" in human or "review" in human.lower()


def test_operator_summary_no_records_graceful(tmp_path: Path) -> None:
    out = compute_builder_status(tmp_path, "ghost")
    osum = out.get("operator_summary") or {}
    assert osum.get("has_builder_activity") is False
    assert "missing_declared_target" in (osum.get("alignment_summary") or "")
    assert osum.get("trust_posture") == "unknown_incomplete"
    assert osum.get("recommended_next_action") == "fix_declaration"
    human = format_builder_operator_summary_human(out)
    assert "no invoke/reconcile records" in human.lower()


def test_operator_summary_escalation_visible(tmp_path: Path) -> None:
    pid = "esc_op"
    _write_next_expansion(tmp_path, pid)
    _write_prepared(tmp_path, pid)
    _write_invoke(tmp_path, pid)
    _write_reconcile(tmp_path, pid)
    d = tmp_path / "runs" / "escalations" / "latest"
    d.mkdir(parents=True, exist_ok=True)
    pkt = {
        "packet_id": "esc_20260416T120000Z_esc_op",
        "product_id": pid,
        "risk_level": "high",
        "title": "Builder anomaly",
        "why_stopped": "scope problem",
        "summary": "s",
        "created_at": "2026-04-16T12:00:00Z",
        "metadata": {"builder_escalation": True},
    }
    (d / "esc_20260416T120000Z_esc_op.json").write_text(json.dumps(pkt), encoding="utf-8")
    out = compute_builder_status(tmp_path, pid)
    osum = out.get("operator_summary") or {}
    assert "high" in (osum.get("escalation_summary") or "").lower()
    assert osum.get("recommended_next_action") == "review_escalation"
    assert "runs/escalations" in (osum.get("suggested_next") or "").lower() or "escalation" in (
        osum.get("suggested_next") or ""
    ).lower()
    human = format_builder_operator_summary_human(out)
    assert "high:" in human or "Escalation:" in human
    assert "`recommended_next_action=review_escalation`" in human
    assert "Next — Review escalation" in human or "escalation" in human.lower()


def test_operator_summary_merge_candidate_suggested_merge(tmp_path: Path) -> None:
    pid = "merge_ok"
    target_id = "group_01_slot_04"
    group_id = "group_01_beginnings"
    _write_next_expansion(tmp_path, pid, target_id=target_id, group_id=group_id)
    _write_prepared(tmp_path, pid, target_id=target_id, group_id=group_id)
    inv_dir = tmp_path / "runs" / "builder" / "invoke" / pid
    inv_dir.mkdir(parents=True)
    inv_rec = {
        "schema": "argus.builder_invoke_record.v1",
        "product_id": pid,
        "mode": "execute",
        "execution_backend": "agent",
        "invocation_status": "ok",
        "invoked_at_utc": "2026-04-16T22:00:00Z",
        "resolved_target": {"id": target_id, "target_type": "content_slot", "group_id": group_id},
        "git_branch_isolation": {
            "schema": "argus.builder.git_branch_isolation.v1",
            "branch_isolation_status": "ok",
            "git_builder_branch": "builder/s1-abc1234567",
            "git_branch_before": "main",
            "git_branch_created": True,
            "trust_degraded_dirty_tree": False,
        },
        "git_baseline": {
            "schema": "argus.builder.git_baseline.v1",
            "baseline_commit": "deadbeef",
            "git_cwd": str(tmp_path),
        },
        "builder_containment": {
            "schema": "argus.builder.containment.v1",
            "containment_requested": "auto",
            "containment_applied": "bwrap",
            "containment_fallback_used": False,
            "trust_degraded_unsandboxed": False,
            "network_mode": "default",
            "network_applied": True,
        },
    }
    (inv_dir / "latest.json").write_text(json.dumps(inv_rec), encoding="utf-8")
    rec_dir = tmp_path / "runs" / "builder" / "reconcile" / pid
    rec_dir.mkdir(parents=True)
    rec = {
        "schema": "argus.builder_reconcile_record.v1",
        "product_id": pid,
        "reconciled_at_utc": "2026-04-16T22:05:00Z",
        "target_transition_status": "unchanged",
        "current_target": {"id": target_id, "target_type": "content_slot", "group_id": group_id},
        "generate_next_expansion": {"status": "skipped", "reason": "flag_not_set"},
        "prepare_next": {"status": "skipped", "reason": "flag_not_set"},
        "builder_scope_check": {
            "schema": "argus.builder_scope_check.v2",
            "status": "ok",
            "scope_breach": False,
            "path_scope_breach": False,
            "semantic_scope_breach": False,
            "path_scope": {},
        },
        "execution_outcome": {
            "schema": "argus.builder.execution_outcome.v1",
            "outcome": "completed",
            "reasons": [],
        },
        "builder_diff_summary": {
            "schema": "argus.builder.git_diff_summary.v1",
            "changed_file_count": 2,
            "fallback_used": False,
            "source": "invoke_baseline",
        },
    }
    (rec_dir / "latest.json").write_text(json.dumps(rec), encoding="utf-8")
    out = compute_builder_status(tmp_path, pid)
    assert out["latest_reconcile"]["review_status"] == "merge_candidate"
    osum = out.get("operator_summary") or {}
    assert osum.get("merge_readiness") == "merge_candidate"
    assert osum.get("trust_posture") == "ready_to_review"
    assert osum.get("recommended_next_action") == "review_and_merge"
    assert "builder merge merge_ok" in (osum.get("suggested_next") or "")
    assert "Sandboxed (bubblewrap)" in (osum.get("sandbox_summary") or "")


def test_build_from_payload_without_embedded_operator_summary(tmp_path: Path) -> None:
    pid = "embed"
    _write_next_expansion(tmp_path, pid)
    _write_prepared(tmp_path, pid)
    _write_invoke(tmp_path, pid)
    _write_reconcile(tmp_path, pid)
    full = compute_builder_status(tmp_path, pid)
    slim = {k: v for k, v in full.items() if k != "operator_summary"}
    built = build_builder_operator_summary(slim)
    assert built.get("schema") == "argus.builder_operator_summary.v1"
    assert built.get("alignment_summary") == "aligned"


def test_status_brief_cli_only_operator_block(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    pid = "brief1"
    _write_next_expansion(tmp_path, pid)
    _write_prepared(tmp_path, pid)
    _write_invoke(tmp_path, pid)
    _write_reconcile(tmp_path, pid)
    monkeypatch.setattr("argus.cli.builder_cmd.repo_root", lambda: tmp_path)
    args = Namespace(
        builder_command="status",
        product_id=pid,
        json=False,
        brief=True,
        products_dir=None,
    )
    assert run_builder_subcommand(args) == 0
    captured = capsys.readouterr()
    assert "=== Builder — brief1 ===" in captured.out
    assert "Latest invoke:" not in captured.out
    assert "Declared target:" not in captured.out
