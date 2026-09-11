"""Builder v0: next_expansion.json → prompt + builder_task.json (hermetic)."""

from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path

import pytest

from argus.builder.next_expansion_prepare import (
    NextExpansionPrepareError,
    build_prepare_result,
    write_prepare_artifacts,
)
from argus.cli.builder_cmd import run_builder_subcommand


def _write_minimal_next_expansion(root: Path, product_id: str, *, target_type: str = "content_slot") -> None:
    pr = root / "products" / product_id
    (pr / "content").mkdir(parents=True)
    payload = {
        "schema": "argus.next_expansion.v1",
        "primary_target": {
            "target_type": target_type,
            "id": "group_01_slot_03",
            "group_id": "group_01_beginnings",
            "rationale": "Next slot in sequence.",
            "basis": ["catalog order"],
            "confidence": "high",
        },
    }
    (pr / "content" / "next_expansion.json").write_text(
        json.dumps(payload, indent=2),
        encoding="utf-8",
    )


def test_build_prepare_content_slot_contains_target_and_template(tmp_path: Path) -> None:
    _write_minimal_next_expansion(tmp_path, "wk")
    res = build_prepare_result(tmp_path, "wk")
    assert res.task["schema"] == "argus.builder_prepare_task.v1"
    assert res.task["prompt_template_id"] == "builder_v0.content_slot"
    assert res.task["resolved_target"]["id"] == "group_01_slot_03"
    assert res.task["resolved_target"]["target_type"] == "content_slot"
    assert res.task["review_required"] is True
    assert res.task.get("execution_contract", {}).get("schema") == "argus.builder.execution_contract.v1"
    assert res.task["execution_contract"]["increment_target_id"] == "group_01_slot_03"
    assert "group_01_slot_03" in res.markdown
    assert "bounded execution" in res.markdown.lower()
    assert "do not change `primary_target`" in res.markdown.lower() or "primary_target" in res.markdown


def test_unsupported_target_type_raises(tmp_path: Path) -> None:
    _write_minimal_next_expansion(tmp_path, "wk", target_type="book_hub")
    with pytest.raises(NextExpansionPrepareError, match="Unsupported target_type"):
        build_prepare_result(tmp_path, "wk")


def test_write_artifacts_creates_files(tmp_path: Path) -> None:
    _write_minimal_next_expansion(tmp_path, "wk")
    res = write_prepare_artifacts(tmp_path, "wk", under="runs")
    assert res.paths.prompt_md.is_file() and res.paths.task_json.is_file()
    task = json.loads(res.paths.task_json.read_text(encoding="utf-8"))
    assert task["resolved_target"]["id"] == "group_01_slot_03"
    assert "runs" in str(res.paths.prompt_md)


def test_cli_prepare_writes_product_generated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_minimal_next_expansion(tmp_path, "cli_prep")
    monkeypatch.setattr("argus.cli.builder_cmd.repo_root", lambda: tmp_path)
    args = Namespace(
        builder_command="prepare",
        product_id="cli_prep",
        output="product",
        json=False,
        no_save=False,
        products_dir=None,
    )
    assert run_builder_subcommand(args) == 0
    gen = tmp_path / "products" / "cli_prep" / "generated"
    assert (gen / "builder_next_prompt.md").is_file()
    assert (gen / "builder_task.json").is_file()


def test_cli_prepare_no_save_does_not_write(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_minimal_next_expansion(tmp_path, "ns")
    monkeypatch.setattr("argus.cli.builder_cmd.repo_root", lambda: tmp_path)
    args = Namespace(
        builder_command="prepare",
        product_id="ns",
        output="product",
        json=False,
        no_save=True,
        products_dir=None,
    )
    assert run_builder_subcommand(args) == 0
    assert not (tmp_path / "products" / "ns" / "generated").exists()
