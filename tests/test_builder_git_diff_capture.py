"""Git baseline / diff capture for Builder (hermetic where possible)."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from argus.builder.execution_contract import (
    build_content_slot_execution_contract,
    evaluate_path_scope,
)
from argus.builder.git_diff_capture import (
    GIT_DIFF_SUMMARY_SCHEMA,
    build_reconcile_diff_summary,
    collect_diff_since_baseline,
    ensure_product_git_workspace,
    resolve_git_workspace,
)


@pytest.mark.skipif(
    subprocess.run(["git", "--version"], capture_output=True).returncode != 0,
    reason="git not installed",
)
def test_clean_nested_repo_diff_after_change(tmp_path: Path) -> None:
    """Baseline then edit → changed_files includes the edit (nested product git)."""
    pd = tmp_path / "products" / "wk"
    pd.mkdir(parents=True)
    (pd / "content").mkdir(parents=True, exist_ok=True)
    (pd / "content" / "x.txt").write_text("a", encoding="utf-8")
    ensure_product_git_workspace(tmp_path, "wk", products_dir=None)
    ws = resolve_git_workspace(tmp_path, "wk", products_dir=None)
    assert ws["kind"] == "nested_product"
    init = subprocess.run(
        ["git", "-C", str(ws["git_cwd"]), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
    )
    assert init.returncode == 0
    baseline = init.stdout.strip()
    (pd / "content" / "x.txt").write_text("ab", encoding="utf-8")
    sub = collect_diff_since_baseline(ws["git_cwd"], baseline, argus_path_prefix="products/wk/")
    assert sub["schema"] == GIT_DIFF_SUMMARY_SCHEMA
    assert "products/wk/content/x.txt" in (sub.get("changed_files_argus_relative") or [])


@pytest.mark.skipif(
    subprocess.run(["git", "--version"], capture_output=True).returncode != 0,
    reason="git not installed",
)
def test_dirty_tree_flagged_in_baseline(tmp_path: Path) -> None:
    from argus.builder.git_diff_capture import capture_baseline_commit

    pd = tmp_path / "products" / "wk"
    pd.mkdir(parents=True)
    subprocess.run(["git", "-C", str(pd), "init", "-b", "main"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(pd), "config", "user.email", "t@t"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(pd), "config", "user.name", "T"],
        check=True,
        capture_output=True,
    )
    (pd / "a.txt").write_text("x", encoding="utf-8")
    subprocess.run(["git", "-C", str(pd), "add", "-A"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(pd), "commit", "-m", "c"],
        check=True,
        capture_output=True,
    )
    (pd / "dirty.txt").write_text("y", encoding="utf-8")
    bl = capture_baseline_commit(pd)
    assert bl.get("working_tree_dirty_before") is True


def test_argus_path_in_changed_files_triggers_scope_breach(tmp_path: Path) -> None:
    ec = build_content_slot_execution_contract(
        repo_root=tmp_path,
        product_id="wk",
        products_dir=None,
        raw={"primary_target": {"id": "group_01_slot_03", "group_id": "b", "target_type": "content_slot"}},
        pt={"id": "group_01_slot_03", "group_id": "b", "target_type": "content_slot"},
    )
    paths = ["products/wk/content/slots/group_01_slot_03.json", "argus/core/foo.py"]
    r = evaluate_path_scope(
        tmp_path,
        product_id="wk",
        products_dir=None,
        execution_contract=ec,
        changed_paths_repo_relative=paths,
    )
    assert r["argus_core_breach"] is True
    assert r["path_scope_source"] == "builder_diff_summary"


def test_no_changes_empty_diff_nested(tmp_path: Path) -> None:
    pytest.importorskip("pytest")
    if subprocess.run(["git", "--version"], capture_output=True).returncode != 0:
        pytest.skip("git not installed")
    pd = tmp_path / "products" / "wk"
    pd.mkdir(parents=True)
    subprocess.run(["git", "-C", str(pd), "init", "-b", "main"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(pd), "config", "user.email", "t@t"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(pd), "config", "user.name", "T"],
        check=True,
        capture_output=True,
    )
    (pd / "f.txt").write_text("z", encoding="utf-8")
    subprocess.run(["git", "-C", str(pd), "add", "-A"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(pd), "commit", "-m", "c"],
        check=True,
        capture_output=True,
    )
    hp = subprocess.run(
        ["git", "-C", str(pd), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    )
    baseline = hp.stdout.strip()
    sub = collect_diff_since_baseline(pd, baseline, argus_path_prefix="products/wk/")
    assert sub.get("changed_file_count") == 0


@pytest.mark.skipif(
    subprocess.run(["git", "--version"], capture_output=True).returncode != 0,
    reason="git not installed",
)
def test_truncation_marks_diff(tmp_path: Path) -> None:
    import argus.builder.git_diff_capture as gdc

    pd = tmp_path / "products" / "wk"
    pd.mkdir(parents=True)
    ensure_product_git_workspace(tmp_path, "wk", products_dir=None)
    ws = resolve_git_workspace(tmp_path, "wk", products_dir=None)
    (pd / "big.txt").write_text("x", encoding="utf-8")
    subprocess.run(["git", "-C", str(ws["git_cwd"]), "add", "-A"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(ws["git_cwd"]), "commit", "-m", "init"],
        check=True,
        capture_output=True,
    )
    hp = subprocess.run(
        ["git", "-C", str(ws["git_cwd"]), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    )
    baseline = hp.stdout.strip()
    (pd / "big.txt").write_text("y" * 5000, encoding="utf-8")
    orig_c, orig_l = gdc.MAX_DIFF_CHARS, gdc.MAX_DIFF_LINES
    gdc.MAX_DIFF_CHARS = 80
    gdc.MAX_DIFF_LINES = 20
    try:
        out = collect_diff_since_baseline(ws["git_cwd"], baseline, argus_path_prefix="products/wk/")
        assert out.get("diff_truncated") is True
    finally:
        gdc.MAX_DIFF_CHARS = orig_c
        gdc.MAX_DIFF_LINES = orig_l


@patch("argus.builder.execution_contract._git_changed_paths")
def test_reconcile_diff_summary_fallback(mock_paths: object, tmp_path: Path) -> None:
    mock_paths.return_value = (["docs/a.md"], None)
    out = build_reconcile_diff_summary(
        tmp_path,
        "wk",
        products_dir=None,
        invoke_data=None,
    )
    assert out["source"] == "argus_working_tree_fallback"
    assert "docs/a.md" in out["changed_files_argus_relative"]
