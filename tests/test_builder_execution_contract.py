"""Builder execution contract and scope evaluation (hermetic)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from argus.builder.execution_contract import (
    build_builder_scope_check,
    build_content_slot_execution_contract,
    evaluate_path_scope,
    evaluate_semantic_primary_target_scope,
    path_allowed_by_contract,
    slot_file_glob_for_same_group,
)


def test_story_file_glob_same_book() -> None:
    assert slot_file_glob_for_same_group("group_01_slot_05") == "content/slots/group_01_slot_*.json"
    assert slot_file_glob_for_same_group("x") is None


def test_path_allowed_by_contract() -> None:
    ec = {
        "allowed_paths_exact": ["content/foo.json"],
        "allowed_path_patterns": ["app/site/slot/*.html"],
    }
    assert path_allowed_by_contract("content/foo.json", ec)
    assert path_allowed_by_contract("app/site/slot/a.html", ec)
    assert not path_allowed_by_contract("argus/core.py", ec)


def test_build_content_slot_execution_contract_paths(tmp_path: Path) -> None:
    (tmp_path / "products" / "wk" / "content").mkdir(parents=True)
    raw = {
        "schema": "argus.next_expansion.v1",
        "primary_target": {
            "id": "group_01_slot_03",
            "group_id": "group_01_beginnings",
            "target_type": "content_slot",
            "rationale": "r",
            "basis": [],
            "confidence": "high",
        },
    }
    pt = raw["primary_target"]
    ec = build_content_slot_execution_contract(
        repo_root=tmp_path,
        product_id="wk",
        products_dir=None,
        raw=raw,
        pt=pt,
    )
    assert ec["schema"] == "argus.builder.execution_contract.v1"
    assert ec["increment_target_id"] == "group_01_slot_03"
    assert "content/slots/group_01_slot_03.json" in ec["allowed_paths_exact"]
    assert ec["next_expansion_policy"] == "do_not_change_primary_target"
    assert "app/site/group/group_01.html" in ec["allowed_paths_exact"]


@pytest.mark.skipif(
    subprocess.run(["git", "--version"], capture_output=True).returncode != 0,
    reason="git not installed",
)
def test_evaluate_path_scope_detects_breach_outside_product(tmp_path: Path) -> None:
    subprocess.run(["git", "-C", str(tmp_path), "init", "-b", "main"], check=True, capture_output=True)
    (tmp_path / "products" / "wk" / "content").mkdir(parents=True, exist_ok=True)
    (tmp_path / "products" / "wk" / "content" / "content_catalog.json").write_text("{}", encoding="utf-8")
    (tmp_path / "evil.txt").write_text("x", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "-A"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "commit", "-m", "init"],
        check=True,
        capture_output=True,
    )
    (tmp_path / "evil2.txt").write_text("y", encoding="utf-8")

    ec = build_content_slot_execution_contract(
        repo_root=tmp_path,
        product_id="wk",
        products_dir=None,
        raw={
            "primary_target": {
                "id": "group_01_slot_03",
                "group_id": "group_01_beginnings",
                "target_type": "content_slot",
                "rationale": "",
                "basis": [],
                "confidence": "high",
            },
        },
        pt={
            "id": "group_01_slot_03",
            "group_id": "group_01_beginnings",
            "target_type": "content_slot",
        },
    )
    r = evaluate_path_scope(tmp_path, product_id="wk", products_dir=None, execution_contract=ec)
    assert r["status"] == "ok"
    assert r["scope_breach"] is True
    assert any("evil" in p for p in r["paths_outside_product"])
    assert r["argus_core_breach"] is False
    assert r["non_product_root_breach"] is True
    reasons = " ".join(r.get("breach_reasons") or [])
    assert "path_scope:modified_non_product_root" in reasons


@pytest.mark.skipif(
    subprocess.run(["git", "--version"], capture_output=True).returncode != 0,
    reason="git not installed",
)
def test_evaluate_path_scope_argus_modification_is_hard_breach(tmp_path: Path) -> None:
    subprocess.run(["git", "-C", str(tmp_path), "init", "-b", "main"], check=True, capture_output=True)
    (tmp_path / "products" / "wk" / "content").mkdir(parents=True, exist_ok=True)
    (tmp_path / "products" / "wk" / "content" / "content_catalog.json").write_text("{}", encoding="utf-8")
    (tmp_path / "argus").mkdir(parents=True)
    (tmp_path / "argus" / "touched.py").write_text("# x", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "-A"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "commit", "-m", "init"],
        check=True,
        capture_output=True,
    )
    (tmp_path / "argus" / "touched.py").write_text("# y", encoding="utf-8")

    ec = build_content_slot_execution_contract(
        repo_root=tmp_path,
        product_id="wk",
        products_dir=None,
        raw={
            "primary_target": {
                "id": "group_01_slot_03",
                "group_id": "group_01_beginnings",
                "target_type": "content_slot",
                "rationale": "",
                "basis": [],
                "confidence": "high",
            },
        },
        pt={
            "id": "group_01_slot_03",
            "group_id": "group_01_beginnings",
            "target_type": "content_slot",
        },
    )
    r = evaluate_path_scope(tmp_path, product_id="wk", products_dir=None, execution_contract=ec)
    assert r["scope_breach"] is True
    assert r["argus_core_breach"] is True
    assert "argus/touched.py" in r["modified_argus_paths"]
    reasons = " ".join(r.get("breach_reasons") or [])
    assert "path_scope:modified_argus_core" in reasons


@pytest.mark.skipif(
    subprocess.run(["git", "--version"], capture_output=True).returncode != 0,
    reason="git not installed",
)
def test_evaluate_path_scope_in_scope_product_edit_no_breach(tmp_path: Path) -> None:
    subprocess.run(["git", "-C", str(tmp_path), "init", "-b", "main"], check=True, capture_output=True)
    story = (
        tmp_path / "products" / "wk" / "content" / "slots" / "group_01_slot_03.json"
    )
    story.parent.mkdir(parents=True, exist_ok=True)
    story.write_text('{"schema": "x", "title": "t"}', encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "-A"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "commit", "-m", "init"],
        check=True,
        capture_output=True,
    )
    story.write_text('{"schema": "x", "title": "t2"}', encoding="utf-8")

    ec = build_content_slot_execution_contract(
        repo_root=tmp_path,
        product_id="wk",
        products_dir=None,
        raw={
            "primary_target": {
                "id": "group_01_slot_03",
                "group_id": "group_01_beginnings",
                "target_type": "content_slot",
                "rationale": "",
                "basis": [],
                "confidence": "high",
            },
        },
        pt={
            "id": "group_01_slot_03",
            "group_id": "group_01_beginnings",
            "target_type": "content_slot",
        },
    )
    r = evaluate_path_scope(tmp_path, product_id="wk", products_dir=None, execution_contract=ec)
    assert r["scope_breach"] is False
    assert r["argus_core_breach"] is False
    assert r["non_product_root_breach"] is False


def test_evaluate_path_scope_no_contract(tmp_path: Path) -> None:
    r = evaluate_path_scope(
        tmp_path, product_id="wk", products_dir=None, execution_contract=None
    )
    assert r["status"] == "no_contract_in_task"


def _minimal_contract() -> dict:
    return {
        "schema": "argus.builder.execution_contract.v1",
        "next_expansion_policy": "do_not_change_primary_target",
    }


def test_evaluate_semantic_primary_target_ok(tmp_path: Path) -> None:
    (tmp_path / "products" / "wk" / "content").mkdir(parents=True)
    tid = "group_01_slot_03"
    ne = {
        "schema": "argus.next_expansion.v1",
        "primary_target": {
            "id": tid,
            "target_type": "content_slot",
            "group_id": "group_01_beginnings",
        },
    }
    (tmp_path / "products" / "wk" / "content" / "next_expansion.json").write_text(
        json.dumps(ne), encoding="utf-8"
    )
    task = {
        "resolved_target": {
            "id": tid,
            "target_type": "content_slot",
            "group_id": "group_01_beginnings",
        },
        "execution_contract": _minimal_contract(),
    }
    ec = task["execution_contract"]
    r = evaluate_semantic_primary_target_scope(
        tmp_path,
        product_id="wk",
        products_dir=None,
        task_data=task,
        execution_contract=ec,
    )
    assert r["status"] == "ok"
    assert r["semantic_scope_breach"] is False


def test_evaluate_semantic_primary_target_mismatch(tmp_path: Path) -> None:
    (tmp_path / "products" / "wk" / "content").mkdir(parents=True)
    ne = {
        "schema": "argus.next_expansion.v1",
        "primary_target": {
            "id": "group_01_slot_03",
            "target_type": "content_slot",
            "group_id": "group_01_beginnings",
        },
    }
    (tmp_path / "products" / "wk" / "content" / "next_expansion.json").write_text(
        json.dumps(ne), encoding="utf-8"
    )
    task = {
        "resolved_target": {
            "id": "group_01_slot_04",
            "target_type": "content_slot",
            "group_id": "group_01_beginnings",
        },
        "execution_contract": _minimal_contract(),
    }
    ec = task["execution_contract"]
    r = evaluate_semantic_primary_target_scope(
        tmp_path,
        product_id="wk",
        products_dir=None,
        task_data=task,
        execution_contract=ec,
    )
    assert r["semantic_scope_breach"] is True
    assert r["status"] == "primary_target_mismatch"
    assert any("primary_target" in x for x in r["semantic_breach_reasons"])


def test_build_builder_scope_check_merges_path_and_semantic(tmp_path: Path) -> None:
    (tmp_path / "products" / "wk" / "content").mkdir(parents=True)
    ne = {
        "primary_target": {
            "id": "group_01_slot_03",
            "target_type": "content_slot",
            "group_id": "group_01_beginnings",
        },
    }
    (tmp_path / "products" / "wk" / "content" / "next_expansion.json").write_text(
        json.dumps(ne), encoding="utf-8"
    )
    task = {
        "resolved_target": {
            "id": "group_01_slot_04",
            "target_type": "content_slot",
            "group_id": "group_01_beginnings",
        },
        "execution_contract": _minimal_contract(),
    }
    r = build_builder_scope_check(
        tmp_path, product_id="wk", products_dir=None, task_data=task
    )
    assert r["schema"] == "argus.builder_scope_check.v2"
    assert r["semantic_scope_breach"] is True
    assert r["scope_breach"] is True
    assert r["path_scope_breach"] is False
    assert r["status"] == "breach"
