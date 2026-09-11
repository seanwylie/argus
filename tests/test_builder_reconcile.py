"""Builder reconcile v0.6–v0.7: signals/findings refresh + target comparison + optional prepare-next."""

from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

import pytest

from argus.builder.next_expansion_prepare import NextExpansionPrepareError
from argus.builder.reconcile import BUILDER_RECONCILE_RECORD_SCHEMA, run_builder_reconcile
from argus.cli.builder_cmd import run_builder_subcommand


def _minimal_execution_contract() -> dict:
    return {
        "schema": "argus.builder.execution_contract.v1",
        "next_expansion_policy": "do_not_change_primary_target",
    }


def _write_generated(root: Path, product_id: str, *, target_id: str = "group_01_slot_03") -> None:
    d = root / "products" / product_id / "generated"
    d.mkdir(parents=True)
    (d / "builder_next_prompt.md").write_text("# p\n", encoding="utf-8")
    (d / "builder_task.json").write_text(
        json.dumps(
            {
                "schema": "argus.builder_prepare_task.v1",
                "resolved_target": {
                    "id": target_id,
                    "target_type": "content_slot",
                    "group_id": "group_01_beginnings",
                },
            }
        ),
        encoding="utf-8",
    )


def _write_generated_with_do_not_change_contract(
    root: Path, product_id: str, *, target_id: str = "group_01_slot_03"
) -> None:
    d = root / "products" / product_id / "generated"
    d.mkdir(parents=True)
    (d / "builder_next_prompt.md").write_text("# p\n", encoding="utf-8")
    (d / "builder_task.json").write_text(
        json.dumps(
            {
                "schema": "argus.builder_prepare_task.v1",
                "resolved_target": {
                    "id": target_id,
                    "target_type": "content_slot",
                    "group_id": "group_01_beginnings",
                },
                "execution_contract": _minimal_execution_contract(),
            }
        ),
        encoding="utf-8",
    )


def _write_next_expansion(root: Path, product_id: str, *, target_id: str) -> None:
    pr = root / "products" / product_id / "content"
    pr.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": "argus.next_expansion.v1",
        "primary_target": {
            "target_type": "content_slot",
            "id": target_id,
            "group_id": "group_01_beginnings",
            "rationale": "r",
            "basis": [],
            "confidence": "low",
        },
    }
    (pr / "next_expansion.json").write_text(json.dumps(payload), encoding="utf-8")


def _write_invoke(root: Path, product_id: str) -> None:
    inv = root / "runs" / "builder" / "invoke" / product_id
    inv.mkdir(parents=True)
    (inv / "latest.json").write_text(
        json.dumps(
            {
                "schema": "argus.builder_invoke_record.v1",
                "resolved_target": {
                    "id": "group_01_slot_03",
                    "target_type": "content_slot",
                    "group_id": "group_01_beginnings",
                },
            }
        ),
        encoding="utf-8",
    )


def _write_invoke_ok(
    root: Path, product_id: str, *, target_id: str = "group_01_slot_03"
) -> None:
    inv = root / "runs" / "builder" / "invoke" / product_id
    inv.mkdir(parents=True)
    (inv / "latest.json").write_text(
        json.dumps(
            {
                "schema": "argus.builder_invoke_record.v1",
                "invocation_status": "ok",
                "mode": "execute",
                "exit_code": 0,
                "resolved_target": {
                    "id": target_id,
                    "target_type": "content_slot",
                    "group_id": "group_01_beginnings",
                },
            }
        ),
        encoding="utf-8",
    )


def _write_content_slot_artifacts(root: Path, product_id: str, tid: str) -> None:
    base = root / "products" / product_id
    sj = base / "content" / "slots" / f"{tid}.json"
    sj.parent.mkdir(parents=True, exist_ok=True)
    sj.write_text(
        json.dumps(
            {
                "schema": "argus.content_item.v1",
                "id": tid,
                "title": "T",
                "body": "x" * 50,
            }
        ),
        encoding="utf-8",
    )
    html = base / "app" / "site" / "slot" / f"{tid}.html"
    html.parent.mkdir(parents=True, exist_ok=True)
    html.write_text(
        "<!doctype html><html><body>" + "y" * 100 + "</body></html>",
        encoding="utf-8",
    )


@patch("argus.builder.reconcile.cmd_findings_generate", return_value=0)
@patch("argus.builder.reconcile.cmd_signals_collect", return_value=0)
def test_reconcile_unchanged(
    _mock_sig: object, _mock_fin: object, tmp_path: Path
) -> None:
    pid = "r1"
    _write_generated(tmp_path, pid)
    _write_next_expansion(tmp_path, pid, target_id="group_01_slot_03")
    _write_invoke(tmp_path, pid)
    rec, code = run_builder_reconcile(tmp_path, pid, no_record=True)
    assert code == 0
    assert rec["schema"] == BUILDER_RECONCILE_RECORD_SCHEMA
    assert rec["target_transition_status"] == "unchanged"
    assert rec["signals_collect"]["status"] == "ok"
    assert rec["findings_generate"]["status"] == "ok"
    assert rec["prepare_next"]["reason"] == "flag_not_set"
    assert rec["generate_next_expansion"]["reason"] == "flag_not_set"
    assert "builder_diff_summary" in rec
    assert "execution_outcome" in rec
    assert rec["execution_outcome"].get("schema") == "argus.builder.execution_outcome.v1"


@patch("argus.builder.reconcile.cmd_findings_generate", return_value=0)
@patch("argus.builder.reconcile.cmd_signals_collect", return_value=0)
def test_reconcile_changed(
    _mock_sig: object, _mock_fin: object, tmp_path: Path
) -> None:
    pid = "r2"
    _write_generated(tmp_path, pid, target_id="group_01_slot_03")
    _write_next_expansion(tmp_path, pid, target_id="group_01_slot_04")
    rec, code = run_builder_reconcile(tmp_path, pid, no_record=True)
    assert code == 0
    assert rec["target_transition_status"] == "changed"


@patch("argus.builder.reconcile.cmd_findings_generate", return_value=0)
@patch("argus.builder.reconcile.cmd_signals_collect", return_value=0)
def test_reconcile_unknown_prior_no_task_no_invoke(
    _mock_sig: object, _mock_fin: object, tmp_path: Path
) -> None:
    pid = "r_unknown"
    _write_next_expansion(tmp_path, pid, target_id="group_01_slot_99")
    rec, code = run_builder_reconcile(tmp_path, pid, no_record=True)
    assert code == 0
    assert rec["prior_resolved_target"] is None
    assert rec["current_target"]["id"] == "group_01_slot_99"
    assert rec["target_transition_status"] == "unknown"


@patch("argus.builder.reconcile.cmd_findings_generate", return_value=0)
@patch("argus.builder.reconcile.cmd_signals_collect", return_value=0)
def test_reconcile_missing_invoke_record(
    _mock_sig: object, _mock_fin: object, tmp_path: Path
) -> None:
    pid = "r3"
    _write_generated(tmp_path, pid)
    _write_next_expansion(tmp_path, pid, target_id="group_01_slot_03")
    rec, code = run_builder_reconcile(tmp_path, pid, no_record=True)
    assert code == 0
    assert any("No invoke record" in o for o in rec["observations"])
    assert rec["prior_resolved_target"]["id"] == "group_01_slot_03"


@patch("argus.builder.reconcile.cmd_findings_generate", return_value=0)
@patch("argus.builder.reconcile.cmd_signals_collect", return_value=0)
def test_reconcile_no_task_uses_invoke_prior_only(
    _mock_sig: object, _mock_fin: object, tmp_path: Path
) -> None:
    pid = "r4"
    _write_next_expansion(tmp_path, pid, target_id="group_01_slot_03")
    _write_invoke(tmp_path, pid)
    rec, code = run_builder_reconcile(tmp_path, pid, no_record=True)
    assert code == 0
    assert rec["source_task_path"] is None
    assert rec["prior_resolved_target"]["id"] == "group_01_slot_03"
    assert rec["target_transition_status"] == "unchanged"


@patch("argus.builder.reconcile.cmd_findings_generate", return_value=0)
@patch("argus.builder.reconcile.cmd_signals_collect", return_value=0)
def test_reconcile_skips(
    _mock_sig: object, _mock_fin: object, tmp_path: Path
) -> None:
    pid = "r5"
    _write_generated(tmp_path, pid)
    _write_next_expansion(tmp_path, pid, target_id="group_01_slot_03")
    rec, code = run_builder_reconcile(
        tmp_path, pid, skip_signals=True, skip_findings=True, no_record=True
    )
    assert code == 0
    assert rec["signals_collect"]["status"] == "skipped"
    assert rec["findings_generate"]["status"] == "skipped"


@patch("argus.builder.reconcile.cmd_findings_generate", return_value=1)
@patch("argus.builder.reconcile.cmd_signals_collect", return_value=0)
def test_reconcile_findings_failure_exit(
    _mock_sig: object, _mock_fin: object, tmp_path: Path
) -> None:
    pid = "r6b"
    _write_generated(tmp_path, pid)
    _write_next_expansion(tmp_path, pid, target_id="group_01_slot_03")
    rec, code = run_builder_reconcile(tmp_path, pid, no_record=True)
    assert rec["findings_generate"]["status"] == "failed"
    assert code == 1


@patch("argus.builder.reconcile.cmd_findings_generate", return_value=0)
@patch("argus.builder.reconcile.cmd_signals_collect", return_value=1)
def test_reconcile_signals_failure_exit(
    _mock_sig: object, _mock_fin: object, tmp_path: Path
) -> None:
    pid = "r6"
    _write_generated(tmp_path, pid)
    _write_next_expansion(tmp_path, pid, target_id="group_01_slot_03")
    rec, code = run_builder_reconcile(tmp_path, pid, no_record=True)
    assert rec["signals_collect"]["status"] == "failed"
    assert code == 1


@patch("argus.builder.reconcile.cmd_findings_generate", return_value=0)
@patch("argus.builder.reconcile.cmd_signals_collect", return_value=0)
def test_reconcile_writes_record(
    _mock_sig: object, _mock_fin: object, tmp_path: Path
) -> None:
    pid = "r7"
    _write_generated(tmp_path, pid)
    _write_next_expansion(tmp_path, pid, target_id="group_01_slot_03")
    _, code = run_builder_reconcile(tmp_path, pid)
    assert code == 0
    latest = tmp_path / "runs" / "builder" / "reconcile" / pid / "latest.json"
    assert latest.is_file()
    raw = json.loads(latest.read_text(encoding="utf-8"))
    assert raw["schema"] == BUILDER_RECONCILE_RECORD_SCHEMA
    br = raw.get("builder_branch_review") or {}
    assert br.get("schema") == "argus.builder.branch_review.v1"
    assert br.get("review_status") in ("blocked", "unsafe", "review_required", "merge_candidate")


@patch("argus.builder.reconcile.cmd_findings_generate", return_value=0)
@patch("argus.builder.reconcile.cmd_signals_collect", return_value=0)
def test_cli_reconcile(
    _mock_sig: object, _mock_fin: object, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    pid = "cli_r"
    _write_generated(tmp_path, pid)
    _write_next_expansion(tmp_path, pid, target_id="group_01_slot_03")
    monkeypatch.setattr("argus.cli.builder_cmd.repo_root", lambda: tmp_path)
    args = Namespace(
        builder_command="reconcile",
        product_id=pid,
        products_dir=None,
        prompt_path=None,
        task_path=None,
        invoke_record_path=None,
        skip_signals=False,
        skip_findings=False,
        no_record=False,
        json=False,
        generate_next_expansion=False,
        prepare_next=False,
        prepare_output="product",
    )
    assert run_builder_subcommand(args) == 0


@patch("argus.builder.reconcile.cmd_findings_generate", return_value=0)
@patch("argus.builder.reconcile.cmd_signals_collect", return_value=0)
def test_prepare_next_unchanged_skipped(
    _mock_sig: object, _mock_fin: object, tmp_path: Path
) -> None:
    pid = "pn1"
    _write_generated(tmp_path, pid)
    _write_next_expansion(tmp_path, pid, target_id="group_01_slot_03")
    rec, code = run_builder_reconcile(
        tmp_path, pid, no_record=True, prepare_next=True
    )
    assert code == 0
    assert rec["target_transition_status"] == "unchanged"
    pn = rec["prepare_next"]
    assert pn["status"] == "skipped"
    assert pn["reason"] == "target_unchanged"
    assert pn["attempted"] is False


@patch("argus.builder.reconcile.cmd_findings_generate", return_value=0)
@patch("argus.builder.reconcile.cmd_signals_collect", return_value=0)
def test_reconcile_semantic_breach_when_primary_target_advances_under_contract(
    _mock_sig: object, _mock_fin: object, tmp_path: Path
) -> None:
    """Disk next_expansion advanced vs builder_task resolved_target while policy forbids it."""
    pid = "sem1"
    _write_generated_with_do_not_change_contract(tmp_path, pid, target_id="group_01_slot_03")
    _write_next_expansion(tmp_path, pid, target_id="group_01_slot_04")
    rec, code = run_builder_reconcile(tmp_path, pid, no_record=True)
    assert code == 0
    sc = rec["builder_scope_check"]
    assert sc.get("schema") == "argus.builder_scope_check.v2"
    assert sc["semantic_scope_breach"] is True
    assert sc["scope_breach"] is True
    reasons_text = " ".join(sc.get("breach_reasons") or [])
    assert "semantic_scope" in reasons_text


@patch("argus.builder.reconcile.cmd_findings_generate", return_value=0)
@patch("argus.builder.reconcile.cmd_signals_collect", return_value=0)
def test_prepare_next_blocked_on_semantic_breach(
    _mock_sig: object, _mock_fin: object, tmp_path: Path
) -> None:
    pid = "sem2"
    _write_generated_with_do_not_change_contract(tmp_path, pid, target_id="group_01_slot_03")
    _write_next_expansion(tmp_path, pid, target_id="group_01_slot_04")
    rec, code = run_builder_reconcile(tmp_path, pid, no_record=True, prepare_next=True)
    assert code == 0
    assert rec["target_transition_status"] == "changed"
    pn = rec["prepare_next"]
    assert pn["reason"] == "scope_breach_blocks_prepare_next"
    assert pn["attempted"] is False


@patch("argus.builder.reconcile.cmd_findings_generate", return_value=0)
@patch("argus.builder.reconcile.cmd_signals_collect", return_value=0)
def test_prepare_next_blocked_partial_when_invoke_ok_but_no_artifacts(
    _mock_sig: object, _mock_fin: object, tmp_path: Path
) -> None:
    pid = "po_partial"
    _write_generated(tmp_path, pid, target_id="group_01_slot_03")
    _write_next_expansion(tmp_path, pid, target_id="group_01_slot_04")
    _write_invoke_ok(tmp_path, pid, target_id="group_01_slot_03")
    rec, code = run_builder_reconcile(
        tmp_path, pid, no_record=True, prepare_next=True
    )
    assert code == 0
    assert rec["target_transition_status"] == "changed"
    assert rec["execution_outcome"]["outcome"] == "partial"
    assert rec["prepare_next"]["reason"] == "execution_outcome_blocks_prepare_next"


@patch("argus.builder.reconcile.cmd_findings_generate", return_value=0)
@patch("argus.builder.reconcile.cmd_signals_collect", return_value=0)
def test_prepare_next_blocked_when_invoke_failed_even_with_artifacts(
    _mock_sig: object, _mock_fin: object, tmp_path: Path
) -> None:
    pid = "po_fail"
    tid = "group_01_slot_03"
    _write_generated(tmp_path, pid, target_id=tid)
    _write_next_expansion(tmp_path, pid, target_id="group_01_slot_04")
    _write_content_slot_artifacts(tmp_path, pid, tid)
    inv = tmp_path / "runs" / "builder" / "invoke" / pid
    inv.mkdir(parents=True)
    (inv / "latest.json").write_text(
        json.dumps(
            {
                "schema": "argus.builder_invoke_record.v1",
                "invocation_status": "failed",
                "error": "timeout",
                "resolved_target": {
                    "id": tid,
                    "target_type": "content_slot",
                    "group_id": "group_01_beginnings",
                },
            }
        ),
        encoding="utf-8",
    )
    rec, code = run_builder_reconcile(
        tmp_path, pid, no_record=True, prepare_next=True
    )
    assert rec["execution_outcome"]["outcome"] == "blocked"
    assert rec["prepare_next"]["reason"] == "execution_outcome_blocks_prepare_next"


@patch("argus.builder.reconcile.cmd_findings_generate", return_value=0)
@patch("argus.builder.reconcile.cmd_signals_collect", return_value=0)
def test_prepare_next_ok_when_completed_evidence(
    _mock_sig: object, _mock_fin: object, tmp_path: Path
) -> None:
    pid = "po_done"
    tid = "group_01_slot_03"
    _write_generated(tmp_path, pid, target_id=tid)
    _write_next_expansion(tmp_path, pid, target_id="group_01_slot_04")
    _write_invoke_ok(tmp_path, pid, target_id=tid)
    _write_content_slot_artifacts(tmp_path, pid, tid)
    rec, code = run_builder_reconcile(
        tmp_path, pid, no_record=True, prepare_next=True
    )
    assert code == 0
    assert rec["execution_outcome"]["outcome"] == "completed"
    pn = rec["prepare_next"]
    assert pn["status"] == "ok"
    assert pn["reason"] == "target_changed_regenerated_contract"


@patch("argus.builder.reconcile.cmd_findings_generate", return_value=0)
@patch("argus.builder.reconcile.cmd_signals_collect", return_value=0)
def test_prepare_next_changed_regenerates(
    _mock_sig: object, _mock_fin: object, tmp_path: Path
) -> None:
    pid = "pn2"
    _write_generated(tmp_path, pid, target_id="group_01_slot_03")
    _write_next_expansion(tmp_path, pid, target_id="group_01_slot_04")
    rec, code = run_builder_reconcile(
        tmp_path, pid, no_record=True, prepare_next=True
    )
    assert code == 0
    assert rec["target_transition_status"] == "changed"
    pn = rec["prepare_next"]
    assert pn["attempted"] is True
    assert pn["status"] == "ok"
    assert pn["reason"] == "target_changed_regenerated_contract"
    assert pn["artifacts"]["prompt_path"].endswith("builder_next_prompt.md")
    assert pn["artifacts"]["task_path"].endswith("builder_task.json")
    gen = tmp_path / "products" / pid / "generated"
    assert (gen / "builder_task.json").is_file()


@patch("argus.builder.reconcile.cmd_findings_generate", return_value=0)
@patch("argus.builder.reconcile.cmd_signals_collect", return_value=0)
def test_prepare_next_no_current_target_skipped(
    _mock_sig: object, _mock_fin: object, tmp_path: Path
) -> None:
    pid = "pn3"
    _write_generated(tmp_path, pid)
    # no next_expansion.json
    rec, code = run_builder_reconcile(
        tmp_path, pid, no_record=True, prepare_next=True
    )
    assert code == 0
    pn = rec["prepare_next"]
    assert pn["status"] == "skipped"
    assert pn["reason"] == "no_current_target"


@patch("argus.builder.reconcile.cmd_findings_generate", return_value=0)
@patch("argus.builder.reconcile.cmd_signals_collect", return_value=0)
def test_prepare_next_unknown_transition_skipped(
    _mock_sig: object, _mock_fin: object, tmp_path: Path
) -> None:
    pid = "pn4"
    _write_next_expansion(tmp_path, pid, target_id="group_01_slot_99")
    rec, code = run_builder_reconcile(
        tmp_path, pid, no_record=True, prepare_next=True
    )
    assert rec["target_transition_status"] == "unknown"
    pn = rec["prepare_next"]
    assert pn["status"] == "skipped"
    assert pn["reason"] == "target_transition_unknown"


@patch("argus.builder.reconcile.write_prepare_artifacts")
@patch("argus.builder.reconcile.cmd_findings_generate", return_value=0)
@patch("argus.builder.reconcile.cmd_signals_collect", return_value=0)
def test_prepare_next_prepare_failure_recorded(
    _mock_sig: object,
    _mock_fin: object,
    mock_prep: object,
    tmp_path: Path,
) -> None:
    mock_prep.side_effect = NextExpansionPrepareError("test failure")
    pid = "pn5"
    _write_generated(tmp_path, pid, target_id="group_01_slot_03")
    _write_next_expansion(tmp_path, pid, target_id="group_01_slot_04")
    rec, code = run_builder_reconcile(
        tmp_path, pid, no_record=True, prepare_next=True
    )
    assert rec["prepare_next"]["status"] == "failed"
    assert rec["prepare_next"]["attempted"] is True
    assert "test failure" in (rec["prepare_next"].get("error") or "")
    assert code == 1


@patch("argus.builder.reconcile.cmd_findings_generate", return_value=0)
@patch("argus.builder.reconcile.cmd_signals_collect", return_value=0)
def test_json_record_includes_prepare_next(
    _mock_sig: object, _mock_fin: object, tmp_path: Path
) -> None:
    pid = "pn6"
    _write_generated(tmp_path, pid)
    _write_next_expansion(tmp_path, pid, target_id="group_01_slot_03")
    rec, _ = run_builder_reconcile(
        tmp_path, pid, no_record=True, prepare_next=True, quiet=True
    )
    assert "prepare_next" in rec
    assert "status" in rec["prepare_next"]
    assert "generate_next_expansion" in rec


def _write_content_catalog(tmp_path: Path) -> None:
    pr = tmp_path / "products" / "demo-product" / "content"
    pr.mkdir(parents=True)
    cat = {
        "schema": "argus.content_catalog.v1",
        "groups": [
            {
                "id": "group_01_beginnings",
                "title": "B1",
                "ordinal": 1,
                "slots": [
                    {"id": "group_01_slot_01", "ordinal": 1, "status": "seeded"},
                    {"id": "group_01_slot_02", "ordinal": 2, "status": "planned"},
                ],
            }
        ],
    }
    (pr / "content_catalog.json").write_text(json.dumps(cat), encoding="utf-8")


@patch("argus.builder.reconcile.cmd_findings_generate", return_value=0)
@patch("argus.builder.reconcile.cmd_signals_collect", return_value=0)
def test_generate_next_expansion_without_catalog_fails(
    _mock_sig: object, _mock_fin: object, tmp_path: Path
) -> None:
    _write_generated(tmp_path, "x")
    _write_next_expansion(tmp_path, "x", target_id="group_01_slot_03")
    rec, code = run_builder_reconcile(
        tmp_path,
        "x",
        skip_signals=True,
        skip_findings=True,
        no_record=True,
        generate_next_expansion=True,
    )
    assert rec["generate_next_expansion"]["status"] == "failed"
    assert rec["generate_next_expansion"]["reason"] == "generation_failed"
    assert "Missing content catalog" in rec["generate_next_expansion"]["error"]
    assert code == 1


@patch("argus.builder.reconcile.cmd_findings_generate", return_value=0)
@patch("argus.builder.reconcile.cmd_signals_collect", return_value=0)
def test_generate_next_expansion_ok(
    _mock_sig: object, _mock_fin: object, tmp_path: Path
) -> None:
    _write_content_catalog(tmp_path)
    _write_generated(tmp_path, "demo-product", target_id="group_01_slot_01")
    _write_next_expansion(tmp_path, "demo-product", target_id="group_01_slot_99")
    rec, code = run_builder_reconcile(
        tmp_path,
        "demo-product",
        skip_signals=True,
        skip_findings=True,
        no_record=True,
        generate_next_expansion=True,
    )
    assert code == 0
    assert rec["generate_next_expansion"]["status"] == "ok"
    assert rec["generate_next_expansion"]["reason"] == "generation_succeeded"
    assert rec["current_target"]["id"] == "group_01_slot_02"
    ne = tmp_path / "products" / "demo-product" / "content" / "next_expansion.json"
    assert ne.is_file()


@patch("argus.builder.reconcile.cmd_findings_generate", return_value=0)
@patch("argus.builder.reconcile.cmd_signals_collect", return_value=0)
def test_generate_and_prepare_next_changed(
    _mock_sig: object, _mock_fin: object, tmp_path: Path
) -> None:
    _write_content_catalog(tmp_path)
    _write_generated(tmp_path, "demo-product", target_id="group_01_slot_01")
    _write_next_expansion(tmp_path, "demo-product", target_id="group_01_slot_01")
    rec, code = run_builder_reconcile(
        tmp_path,
        "demo-product",
        skip_signals=True,
        skip_findings=True,
        no_record=True,
        generate_next_expansion=True,
        prepare_next=True,
    )
    assert code == 0
    assert rec["target_transition_status"] == "changed"
    assert rec["prepare_next"]["status"] == "ok"


@patch("argus.builder.reconcile.cmd_findings_generate", return_value=0)
@patch("argus.builder.reconcile.cmd_signals_collect", return_value=0)
def test_generate_and_prepare_next_unchanged_skipped(
    _mock_sig: object, _mock_fin: object, tmp_path: Path
) -> None:
    _write_content_catalog(tmp_path)
    _write_generated(tmp_path, "demo-product", target_id="group_01_slot_02")
    _write_next_expansion(tmp_path, "demo-product", target_id="group_01_slot_01")
    rec, code = run_builder_reconcile(
        tmp_path,
        "demo-product",
        skip_signals=True,
        skip_findings=True,
        no_record=True,
        generate_next_expansion=True,
        prepare_next=True,
    )
    assert rec["generate_next_expansion"]["status"] == "ok"
    assert rec["target_transition_status"] == "unchanged"
    assert rec["prepare_next"]["reason"] == "target_unchanged"


@patch("argus.builder.reconcile.cmd_findings_generate", return_value=0)
@patch("argus.builder.reconcile.cmd_signals_collect", return_value=0)
def test_cli_reconcile_generate_flags(
    _mock_sig: object, _mock_fin: object, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _write_content_catalog(tmp_path)
    _write_generated(tmp_path, "demo-product", target_id="group_01_slot_01")
    _write_next_expansion(tmp_path, "demo-product", target_id="group_01_slot_01")
    monkeypatch.setattr("argus.cli.builder_cmd.repo_root", lambda: tmp_path)
    args = Namespace(
        builder_command="reconcile",
        product_id="demo-product",
        products_dir=None,
        prompt_path=None,
        task_path=None,
        invoke_record_path=None,
        skip_signals=True,
        skip_findings=True,
        no_record=True,
        json=True,
        generate_next_expansion=True,
        prepare_next=False,
        prepare_output="product",
    )
    assert run_builder_subcommand(args) == 0
