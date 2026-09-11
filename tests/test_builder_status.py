"""Builder status: read-only alignment across next_expansion, prepare, invoke, reconcile."""

from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path

import pytest

from argus.builder.status import compute_builder_status, format_builder_status_human
from argus.cli.builder_cmd import run_builder_subcommand


def _write_next_expansion(
    root: Path,
    product_id: str,
    *,
    target_id: str = "group_01_slot_04",
    group_id: str = "group_01_beginnings",
) -> None:
    pr = root / "products" / product_id / "content"
    pr.mkdir(parents=True)
    payload = {
        "schema": "argus.next_expansion.v1",
        "as_of_utc": "2026-04-16T22:02:58Z",
        "primary_target": {
            "target_type": "content_slot",
            "id": target_id,
            "group_id": group_id,
            "rationale": "r",
            "basis": [],
            "confidence": "high",
        },
    }
    (pr / "next_expansion.json").write_text(json.dumps(payload), encoding="utf-8")


def _write_prepared(
    root: Path,
    product_id: str,
    *,
    target_id: str = "group_01_slot_04",
    group_id: str = "group_01_beginnings",
) -> None:
    d = root / "products" / product_id / "generated"
    d.mkdir(parents=True)
    (d / "builder_next_prompt.md").write_text("# p\n", encoding="utf-8")
    task = {
        "schema": "argus.builder_prepare_task.v1",
        "generated_at_utc": "2026-04-16T21:48:24Z",
        "prompt_template_id": "builder_v0.content_slot",
        "resolved_target": {
            "id": target_id,
            "target_type": "content_slot",
            "group_id": group_id,
        },
        "source_next_expansion_path": f"products/{product_id}/content/next_expansion.json",
    }
    (d / "builder_task.json").write_text(json.dumps(task), encoding="utf-8")


def _write_invoke(
    root: Path,
    product_id: str,
    *,
    target_id: str = "group_01_slot_04",
    group_id: str = "group_01_beginnings",
) -> None:
    p = root / "runs" / "builder" / "invoke" / product_id
    p.mkdir(parents=True)
    rec = {
        "schema": "argus.builder_invoke_record.v1",
        "product_id": product_id,
        "mode": "review",
        "invocation_status": "not_executed",
        "invoked_at_utc": "2026-04-16T22:00:00Z",
        "resolved_target": {"id": target_id, "target_type": "content_slot", "group_id": group_id},
    }
    (p / "latest.json").write_text(json.dumps(rec), encoding="utf-8")


def _write_reconcile(
    root: Path,
    product_id: str,
    *,
    target_id: str = "group_01_slot_04",
    group_id: str = "group_01_beginnings",
) -> None:
    p = root / "runs" / "builder" / "reconcile" / product_id
    p.mkdir(parents=True)
    rec = {
        "schema": "argus.builder_reconcile_record.v1",
        "product_id": product_id,
        "reconciled_at_utc": "2026-04-16T22:05:00Z",
        "target_transition_status": "unchanged",
        "current_target": {"id": target_id, "target_type": "content_slot", "group_id": group_id},
        "generate_next_expansion": {"status": "skipped", "reason": "flag_not_set"},
        "prepare_next": {"status": "skipped", "reason": "flag_not_set"},
    }
    (p / "latest.json").write_text(json.dumps(rec), encoding="utf-8")


def test_status_surfaces_builder_scope_check_v2(tmp_path: Path) -> None:
    pid = "scv2"
    _write_next_expansion(tmp_path, pid)
    _write_prepared(tmp_path, pid)
    _write_invoke(tmp_path, pid)
    p = tmp_path / "runs" / "builder" / "reconcile" / pid
    p.mkdir(parents=True)
    rec = {
        "schema": "argus.builder_reconcile_record.v1",
        "product_id": pid,
        "reconciled_at_utc": "2026-04-16T22:05:00Z",
        "target_transition_status": "changed",
        "current_target": {
            "id": "group_01_slot_04",
            "target_type": "content_slot",
            "group_id": "group_01_beginnings",
        },
        "generate_next_expansion": {"status": "skipped", "reason": "flag_not_set"},
        "prepare_next": {
            "status": "skipped",
            "reason": "scope_breach_blocks_prepare_next",
        },
        "builder_scope_check": {
            "schema": "argus.builder_scope_check.v2",
            "status": "breach",
            "scope_breach": True,
            "path_scope_breach": False,
            "semantic_scope_breach": True,
            "breach_reasons": ["semantic_scope: example"],
        },
        "execution_outcome": {
            "schema": "argus.builder.execution_outcome.v1",
            "outcome": "breached",
            "reasons": ["scope_breach"],
        },
    }
    (p / "latest.json").write_text(json.dumps(rec), encoding="utf-8")
    out = compute_builder_status(tmp_path, pid)
    lr = out["latest_reconcile"]
    assert lr["builder_scope_check_schema"] == "argus.builder_scope_check.v2"
    assert lr["scope_check_status"] == "breach"
    assert lr["scope_breach"] is True
    assert lr["path_scope_breach"] is False
    assert lr["semantic_scope_breach"] is True
    assert lr["prepare_next_reason"] == "scope_breach_blocks_prepare_next"
    assert lr["execution_outcome"] == "breached"
    text = format_builder_status_human(out)
    assert "scope_check:" in text
    assert "semantic=True" in text
    assert "execution_outcome: breached" in text


def test_status_surfaces_argus_core_breach(tmp_path: Path) -> None:
    pid = "argus_br"
    _write_next_expansion(tmp_path, pid)
    _write_prepared(tmp_path, pid)
    _write_invoke(tmp_path, pid)
    p = tmp_path / "runs" / "builder" / "reconcile" / pid
    p.mkdir(parents=True)
    rec = {
        "schema": "argus.builder_reconcile_record.v1",
        "product_id": pid,
        "reconciled_at_utc": "2026-04-16T22:05:00Z",
        "target_transition_status": "unchanged",
        "current_target": {
            "id": "group_01_slot_04",
            "target_type": "content_slot",
            "group_id": "group_01_beginnings",
        },
        "generate_next_expansion": {"status": "skipped", "reason": "flag_not_set"},
        "prepare_next": {
            "status": "skipped",
            "reason": "scope_breach_blocks_prepare_next",
        },
        "builder_scope_check": {
            "schema": "argus.builder_scope_check.v2",
            "status": "breach",
            "scope_breach": True,
            "path_scope_breach": True,
            "semantic_scope_breach": False,
            "breach_reasons": ["path_scope:modified_argus_core (1 path(s) under argus/)"],
            "path_scope": {
                "schema": "argus.builder_scope_check.v1",
                "argus_core_breach": True,
                "non_product_root_breach": False,
                "modified_argus_paths": ["argus/core.py"],
            },
        },
    }
    (p / "latest.json").write_text(json.dumps(rec), encoding="utf-8")
    out = compute_builder_status(tmp_path, pid)
    lr = out["latest_reconcile"]
    assert lr["path_scope_argus_core_breach"] is True
    assert lr["path_scope_non_product_root_breach"] is False
    text = format_builder_status_human(out)
    assert "Argus core (argus/) modified" in text


def test_status_shows_argus_root_workspace_and_degraded_trust(tmp_path: Path) -> None:
    pid = "aroot"
    _write_next_expansion(tmp_path, pid)
    _write_prepared(tmp_path, pid)
    inv_dir = tmp_path / "runs" / "builder" / "invoke" / pid
    inv_dir.mkdir(parents=True)
    inv_rec = {
        "schema": "argus.builder_invoke_record.v1",
        "product_id": pid,
        "mode": "execute",
        "execution_backend": "agent",
        "invocation_status": "ok",
        "invoked_at_utc": "2026-04-16T22:00:00Z",
        "resolved_target": {
            "id": "group_01_slot_04",
            "target_type": "content_slot",
            "group_id": "group_01_beginnings",
        },
        "git_baseline": {
            "schema": "argus.builder.git_baseline.v1",
            "git_workspace_kind": "argus_root",
            "git_cwd": str(tmp_path.resolve()),
            "baseline_commit": "abc",
        },
        "git_branch_isolation": {
            "branch_isolation_status": "skipped_not_nested_product_repo",
        },
        "builder_containment": {
            "schema": "argus.builder.containment.v1",
            "trust_degraded_workspace_scope": True,
            "filesystem_scope_mode": "legacy_repo_rw",
        },
    }
    (inv_dir / "latest.json").write_text(json.dumps(inv_rec), encoding="utf-8")
    _write_reconcile(tmp_path, pid)
    out = compute_builder_status(tmp_path, pid)
    lv = out["latest_invoke"]
    assert lv["builder_workspace_kind"] == "argus_root"
    assert lv["trust_degraded_workspace_scope"] is True
    text = format_builder_status_human(out)
    assert "builder_workspace_kind: argus_root" in text
    assert "DEGRADED — Argus-root workspace" in text
    lr = out["latest_reconcile"]
    assert lr["review_git_workspace_kind"] == "argus_root"
    assert lr["review_trust_degraded_workspace_scope"] is True


def test_status_aligned(tmp_path: Path) -> None:
    pid = "wk"
    _write_next_expansion(tmp_path, pid)
    _write_prepared(tmp_path, pid)
    _write_invoke(tmp_path, pid)
    _write_reconcile(tmp_path, pid)
    out = compute_builder_status(tmp_path, pid)
    assert out["alignment_summary"] == "aligned"
    assert out["schema"] == "argus.builder_status.v1"
    assert out["declared_next_target"]["target_id"] == "group_01_slot_04"
    assert out["prepared_task"]["resolved_target_id"] == "group_01_slot_04"
    assert out["latest_invoke"]["invocation_status"] == "not_executed"
    assert out["latest_reconcile"]["target_transition_status"] == "unchanged"
    assert out["latest_reconcile"]["review_status"] == "blocked"
    text = format_builder_status_human(out)
    assert "Summary: aligned" in text
    assert "Merge readiness: blocked" in text
    assert "group_01_slot_04" in text


def test_status_missing_prepared(tmp_path: Path) -> None:
    pid = "mp"
    _write_next_expansion(tmp_path, pid)
    out = compute_builder_status(tmp_path, pid)
    assert out["alignment_summary"] == "missing_prepared"


def test_status_prepared_stale(tmp_path: Path) -> None:
    pid = "ps"
    _write_next_expansion(tmp_path, pid, target_id="new_slot")
    _write_prepared(tmp_path, pid, target_id="old_slot")
    _write_invoke(tmp_path, pid, target_id="old_slot")
    _write_reconcile(tmp_path, pid, target_id="new_slot")
    out = compute_builder_status(tmp_path, pid)
    assert out["alignment_summary"] == "prepared_stale"


def test_status_records_missing_invoke(tmp_path: Path) -> None:
    pid = "rmi"
    _write_next_expansion(tmp_path, pid)
    _write_prepared(tmp_path, pid)
    _write_reconcile(tmp_path, pid)
    out = compute_builder_status(tmp_path, pid)
    assert out["alignment_summary"] == "records_missing"


def test_status_records_missing_reconcile(tmp_path: Path) -> None:
    pid = "rmr"
    _write_next_expansion(tmp_path, pid)
    _write_prepared(tmp_path, pid)
    _write_invoke(tmp_path, pid)
    out = compute_builder_status(tmp_path, pid)
    assert out["alignment_summary"] == "records_missing"


def test_status_missing_declared(tmp_path: Path) -> None:
    out = compute_builder_status(tmp_path, "nope")
    assert out["alignment_summary"] == "missing_declared_target"
    assert out["declared_next_target"]["present"] is False


def test_status_drift_invoke(tmp_path: Path) -> None:
    pid = "dr"
    _write_next_expansion(tmp_path, pid, target_id="t_declared")
    _write_prepared(tmp_path, pid, target_id="t_declared")
    _write_invoke(tmp_path, pid, target_id="t_old")
    _write_reconcile(tmp_path, pid, target_id="t_declared")
    out = compute_builder_status(tmp_path, pid)
    assert out["alignment_summary"] == "drift_detected"
    assert any("invoke" in n.lower() for n in out["notes"])


def test_status_unknown_corrupt_invoke_json(tmp_path: Path) -> None:
    pid = "bad"
    _write_next_expansion(tmp_path, pid)
    _write_prepared(tmp_path, pid)
    inv = tmp_path / "runs" / "builder" / "invoke" / pid
    inv.mkdir(parents=True)
    (inv / "latest.json").write_text("{not json", encoding="utf-8")
    _write_reconcile(tmp_path, pid)
    out = compute_builder_status(tmp_path, pid)
    assert out["alignment_summary"] == "unknown"
    assert out["latest_invoke"]["parse_error"]


def test_status_json_output_cli(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    pid = "cli"
    _write_next_expansion(tmp_path, pid)
    _write_prepared(tmp_path, pid)
    _write_invoke(tmp_path, pid)
    _write_reconcile(tmp_path, pid)
    monkeypatch.setattr("argus.cli.builder_cmd.repo_root", lambda: tmp_path)
    args = Namespace(
        builder_command="status",
        product_id=pid,
        json=True,
        brief=False,
        products_dir=None,
    )
    assert run_builder_subcommand(args) == 0


def test_status_human_cli(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    pid = "hum"
    _write_next_expansion(tmp_path, pid)
    monkeypatch.setattr("argus.cli.builder_cmd.repo_root", lambda: tmp_path)
    args = Namespace(
        builder_command="status",
        product_id=pid,
        json=False,
        brief=False,
        products_dir=None,
    )
    assert run_builder_subcommand(args) == 0
    captured = capsys.readouterr()
    assert "missing_prepared" in captured.out or "Summary: missing_prepared" in captured.out


def test_status_includes_last_builder_escalation(tmp_path: Path) -> None:
    pid = "escp"
    _write_next_expansion(tmp_path, pid)
    _write_prepared(tmp_path, pid)
    _write_invoke(tmp_path, pid)
    _write_reconcile(tmp_path, pid)
    d = tmp_path / "runs" / "escalations" / "latest"
    d.mkdir(parents=True, exist_ok=True)
    pkt = {
        "packet_id": "esc_20260416T120000Z_escp",
        "product_id": pid,
        "risk_level": "high",
        "title": "Builder anomaly",
        "why_stopped": "- [r] first reason line",
        "summary": "s",
        "created_at": "2026-04-16T12:00:00Z",
        "metadata": {"builder_escalation": True},
    }
    (d / "esc_20260416T120000Z_escp.json").write_text(json.dumps(pkt), encoding="utf-8")
    out = compute_builder_status(tmp_path, pid)
    le = out.get("last_escalation") or {}
    assert le.get("present") is True
    assert le.get("severity") == "high"
    assert "first reason" in (le.get("short_reason") or "")
    assert le.get("operator_visible") is True
    human = format_builder_status_human(out)
    assert "Last Builder escalation: high" in human
    assert "runs/escalations/latest/" in human


def test_status_products_dir_override(tmp_path: Path) -> None:
    custom = tmp_path / "vendor_products"
    pid = "vp"
    pr = custom / pid / "content"
    pr.mkdir(parents=True)
    payload = {
        "schema": "argus.next_expansion.v1",
        "primary_target": {
            "target_type": "content_slot",
            "id": "s1",
            "group_id": "b1",
            "rationale": "r",
            "basis": [],
            "confidence": "low",
        },
    }
    (pr / "next_expansion.json").write_text(json.dumps(payload), encoding="utf-8")
    out = compute_builder_status(tmp_path, pid, products_dir=Path("vendor_products"))
    assert out["alignment_summary"] == "missing_prepared"
    assert "vendor_products" in (out["declared_next_target"]["path"] or "")
