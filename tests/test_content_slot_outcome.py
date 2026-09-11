"""Story-slot execution outcome classification (hermetic)."""

from __future__ import annotations

import json
from pathlib import Path

from argus.builder.content_slot_outcome import (
    OUTCOME_BLOCKED,
    OUTCOME_BREACHED,
    OUTCOME_COMPLETED,
    OUTCOME_PARTIAL,
    OUTCOME_UNKNOWN,
    assess_content_slot_increment_files,
    derive_content_slot_execution_outcome,
    outcome_blocks_prepare_next,
)


def _scope_ok() -> dict:
    return {"schema": "argus.builder_scope_check.v2", "scope_breach": False}


def _scope_breach() -> dict:
    return {"schema": "argus.builder_scope_check.v2", "scope_breach": True}


def _task_content_slot(tid: str) -> dict:
    return {
        "resolved_target": {
            "id": tid,
            "target_type": "content_slot",
            "group_id": "group_01_beginnings",
        },
    }


def _write_story_artifacts(root: Path, product_id: str, tid: str) -> None:
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
    html.write_text("<!doctype html><html><body>" + "y" * 100 + "</body></html>", encoding="utf-8")


def test_assess_files_missing(tmp_path: Path) -> None:
    r = assess_content_slot_increment_files(tmp_path, "wk", None, "group_01_slot_03")
    assert r["slot_json_present"] is False
    assert r["slot_json_valid_non_empty"] is False


def test_breached_overrides_files(tmp_path: Path) -> None:
    _write_story_artifacts(tmp_path, "wk", "group_01_slot_03")
    out = derive_content_slot_execution_outcome(
        tmp_path,
        "wk",
        products_dir=None,
        scope_check=_scope_breach(),
        invoke_data={"invocation_status": "ok", "mode": "execute"},
        prior_resolved_target=_task_content_slot("group_01_slot_03")["resolved_target"],
        task_data=_task_content_slot("group_01_slot_03"),
    )
    assert out["outcome"] == OUTCOME_BREACHED
    assert outcome_blocks_prepare_next(out)


def test_blocked_on_invoke_failed(tmp_path: Path) -> None:
    out = derive_content_slot_execution_outcome(
        tmp_path,
        "wk",
        products_dir=None,
        scope_check=_scope_ok(),
        invoke_data={"invocation_status": "failed", "error": "agent died"},
        prior_resolved_target=_task_content_slot("group_01_slot_03")["resolved_target"],
        task_data=_task_content_slot("group_01_slot_03"),
    )
    assert out["outcome"] == OUTCOME_BLOCKED
    assert outcome_blocks_prepare_next(out)


def test_completed_invoke_ok_and_artifacts(tmp_path: Path) -> None:
    tid = "group_01_slot_03"
    _write_story_artifacts(tmp_path, "wk", tid)
    out = derive_content_slot_execution_outcome(
        tmp_path,
        "wk",
        products_dir=None,
        scope_check=_scope_ok(),
        invoke_data={"invocation_status": "ok", "mode": "execute", "exit_code": 0},
        prior_resolved_target=_task_content_slot(tid)["resolved_target"],
        task_data=_task_content_slot(tid),
    )
    assert out["outcome"] == OUTCOME_COMPLETED
    assert not outcome_blocks_prepare_next(out)


def test_partial_invoke_ok_missing_html(tmp_path: Path) -> None:
    tid = "group_01_slot_03"
    base = tmp_path / "products" / "wk"
    sj = base / "content" / "slots" / f"{tid}.json"
    sj.parent.mkdir(parents=True, exist_ok=True)
    sj.write_text(json.dumps({"schema": "x", "title": "t", "body": "z" * 50}), encoding="utf-8")
    out = derive_content_slot_execution_outcome(
        tmp_path,
        "wk",
        products_dir=None,
        scope_check=_scope_ok(),
        invoke_data={"invocation_status": "ok", "mode": "execute", "exit_code": 0},
        prior_resolved_target=_task_content_slot(tid)["resolved_target"],
        task_data=_task_content_slot(tid),
    )
    assert out["outcome"] == OUTCOME_PARTIAL
    assert outcome_blocks_prepare_next(out)


def test_partial_files_but_no_successful_invoke(tmp_path: Path) -> None:
    tid = "group_01_slot_03"
    _write_story_artifacts(tmp_path, "wk", tid)
    out = derive_content_slot_execution_outcome(
        tmp_path,
        "wk",
        products_dir=None,
        scope_check=_scope_ok(),
        invoke_data={"invocation_status": "not_executed", "mode": "review"},
        prior_resolved_target=_task_content_slot(tid)["resolved_target"],
        task_data=_task_content_slot(tid),
    )
    assert out["outcome"] == OUTCOME_PARTIAL
    assert outcome_blocks_prepare_next(out)


def test_unknown_no_invoke_no_files(tmp_path: Path) -> None:
    tid = "group_01_slot_03"
    out = derive_content_slot_execution_outcome(
        tmp_path,
        "wk",
        products_dir=None,
        scope_check=_scope_ok(),
        invoke_data=None,
        prior_resolved_target=_task_content_slot(tid)["resolved_target"],
        task_data=_task_content_slot(tid),
    )
    assert out["outcome"] == OUTCOME_UNKNOWN
    assert not outcome_blocks_prepare_next(out)


def test_non_content_slot_not_classified(tmp_path: Path) -> None:
    out = derive_content_slot_execution_outcome(
        tmp_path,
        "x",
        products_dir=None,
        scope_check=_scope_ok(),
        invoke_data=None,
        prior_resolved_target={"id": "a", "target_type": "other"},
        task_data={"resolved_target": {"id": "a", "target_type": "other"}},
    )
    assert out["outcome"] == OUTCOME_UNKNOWN
    assert "not classified" in " ".join(out.get("reasons") or []).lower()
