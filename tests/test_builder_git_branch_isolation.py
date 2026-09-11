"""Builder per-run git branch isolation (nested product repo)."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from argus.builder.git_branch_isolation import prepare_builder_isolation_branch
from argus.builder.invoke import run_builder_invoke
from argus.builder.status import compute_builder_status, format_builder_status_human


def _write_product_for_invoke(root: Path, product_id: str) -> None:
    pr = root / "products" / product_id
    (pr / "content").mkdir(parents=True, exist_ok=True)
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
    (pr / "content" / "next_expansion.json").write_text(
        json.dumps(payload),
        encoding="utf-8",
    )
    d = pr / "generated"
    d.mkdir(parents=True)
    (d / "builder_next_prompt.md").write_text("# p\n", encoding="utf-8")
    (d / "builder_task.json").write_text(
        json.dumps(
            {
                "schema": "argus.builder_prepare_task.v1",
                "resolved_target": {
                    "id": "s1",
                    "target_type": "content_slot",
                    "group_id": "b1",
                },
                "source_next_expansion_path": f"products/{product_id}/content/next_expansion.json",
            }
        ),
        encoding="utf-8",
    )


def test_invoke_review_skips_branch_isolation(tmp_path: Path) -> None:
    _write_product_for_invoke(tmp_path, "br1")
    rec = run_builder_invoke(tmp_path, "br1", no_record=True)
    gbi = rec.get("git_branch_isolation") or {}
    assert gbi.get("branch_isolation_status") == "skipped_not_executing"
    assert gbi.get("git_builder_branch") is None


def test_execute_nested_git_creates_builder_branch(tmp_path: Path) -> None:
    if not subprocess.run(["git", "--version"], capture_output=True).returncode == 0:
        pytest.skip("git not available")
    pid = "br2"
    _write_product_for_invoke(tmp_path, pid)
    script = tmp_path / "fake_cursor.sh"
    script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    os.chmod(script, 0o755)
    old = os.environ.get("ARGUS_CURSOR_CLI")
    os.environ["ARGUS_CURSOR_CLI"] = str(script)
    try:
        rec = run_builder_invoke(
            tmp_path, pid, execute=True, no_record=True, allow_unsandboxed=True
        )
    finally:
        if old is None:
            os.environ.pop("ARGUS_CURSOR_CLI", None)
        else:
            os.environ["ARGUS_CURSOR_CLI"] = old

    gbi = rec.get("git_branch_isolation") or {}
    assert gbi.get("branch_isolation_status") == "ok", gbi
    bb = gbi.get("git_builder_branch") or ""
    assert bb.startswith("builder/s1-"), bb
    assert gbi.get("git_branch_created") is True
    assert gbi.get("trust_degraded_dirty_tree") is False
    gb = rec.get("git_baseline") or {}
    assert gb.get("git_workspace_kind") == "nested_product"
    assert gb.get("git_branch_at_baseline") == bb

    pd = tmp_path / "products" / pid
    p = subprocess.run(
        ["git", "-C", str(pd), "rev-parse", "--abbrev-ref", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert p.returncode == 0
    assert (p.stdout or "").strip() == bb


def test_execute_dirty_tree_degraded_not_blocked(tmp_path: Path) -> None:
    if not subprocess.run(["git", "--version"], capture_output=True).returncode == 0:
        pytest.skip("git not available")
    pid = "br3"
    _write_product_for_invoke(tmp_path, pid)
    # First invoke: creates nested git + commit, switches to builder branch.
    script = tmp_path / "fake_cursor.sh"
    script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    os.chmod(script, 0o755)
    old_cli = os.environ.get("ARGUS_CURSOR_CLI")
    os.environ["ARGUS_CURSOR_CLI"] = str(script)
    try:
        run_builder_invoke(tmp_path, pid, execute=True, no_record=True)

        dirty_path = tmp_path / "products" / pid / "generated" / "dirty.txt"
        dirty_path.write_text("uncommitted\n", encoding="utf-8")

        rec = run_builder_invoke(
            tmp_path, pid, execute=True, no_record=True, allow_unsandboxed=True
        )
    finally:
        if old_cli is None:
            os.environ.pop("ARGUS_CURSOR_CLI", None)
        else:
            os.environ["ARGUS_CURSOR_CLI"] = old_cli

    gbi = rec.get("git_branch_isolation") or {}
    assert gbi.get("branch_isolation_status") == "degraded_dirty_tree"
    assert gbi.get("trust_degraded_dirty_tree") is True
    assert rec.get("invocation_status") == "ok"


def test_prepare_builder_isolation_branch_idempotent_name_shape(tmp_path: Path) -> None:
    if not subprocess.run(["git", "--version"], capture_output=True).returncode == 0:
        pytest.skip("git not available")
    pr = tmp_path / "g"
    pr.mkdir()
    subprocess.run(["git", "init"], cwd=pr, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "t@e"],
        cwd=pr,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "t"],
        cwd=pr,
        check=True,
        capture_output=True,
    )
    (pr / "a.txt").write_text("a\n")
    subprocess.run(["git", "add", "a.txt"], cwd=pr, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "i"],
        cwd=pr,
        check=True,
        capture_output=True,
    )
    r1 = prepare_builder_isolation_branch(pr, increment_id="slot-1", product_id="p")
    assert r1.get("branch_isolation_status") == "ok"
    b1 = r1.get("git_builder_branch")
    assert b1 and b1.startswith("builder/slot-1-")

    r2 = prepare_builder_isolation_branch(pr, increment_id="slot-1", product_id="p")
    assert r2.get("git_builder_branch") != b1
    assert r2.get("branch_isolation_status") == "ok"


def test_status_surfaces_branch_isolation(tmp_path: Path) -> None:
    pid = "stbr"
    inv = tmp_path / "runs" / "builder" / "invoke" / pid
    inv.mkdir(parents=True)
    (inv / "latest.json").write_text(
        json.dumps(
            {
                "schema": "argus.builder_invoke_record.v1",
                "resolved_target": {"id": "x", "target_type": "content_slot", "group_id": "b"},
                "git_branch_isolation": {
                    "schema": "argus.builder.git_branch_isolation.v1",
                    "branch_isolation_status": "ok",
                    "git_branch_before": "main",
                    "git_builder_branch": "builder/s1-abc1234567",
                    "git_branch_created": True,
                    "trust_degraded_dirty_tree": False,
                    "branch_isolation_error": None,
                },
            }
        ),
        encoding="utf-8",
    )
    rec = tmp_path / "runs" / "builder" / "reconcile" / pid
    rec.mkdir(parents=True)
    (rec / "latest.json").write_text(
        json.dumps(
            {
                "schema": "argus.builder_reconcile_record.v1",
                "current_target": {"id": "x", "target_type": "content_slot", "group_id": "b"},
                "git_branch_context": {
                    "git_branch_at_reconcile": "builder/s1-abc1234567",
                    "read_error": None,
                },
            }
        ),
        encoding="utf-8",
    )
    # Minimal declared + prepared for alignment (aligned path)
    pr = tmp_path / "products" / pid / "content"
    pr.mkdir(parents=True)
    pr.joinpath("next_expansion.json").write_text(
        json.dumps(
            {
                "schema": "argus.next_expansion.v1",
                "primary_target": {
                    "target_type": "content_slot",
                    "id": "x",
                    "group_id": "b",
                    "rationale": "r",
                    "basis": [],
                    "confidence": "low",
                },
            }
        ),
        encoding="utf-8",
    )
    gen = tmp_path / "products" / pid / "generated"
    gen.mkdir(parents=True)
    gen.joinpath("builder_next_prompt.md").write_text("#\n", encoding="utf-8")
    gen.joinpath("builder_task.json").write_text(
        json.dumps(
            {
                "schema": "argus.builder_prepare_task.v1",
                "resolved_target": {"id": "x", "target_type": "content_slot", "group_id": "b"},
            }
        ),
        encoding="utf-8",
    )

    st = compute_builder_status(tmp_path, pid)
    lv = st.get("latest_invoke") or {}
    assert lv.get("branch_isolation_status") == "ok"
    assert lv.get("git_builder_branch") == "builder/s1-abc1234567"
    assert lv.get("git_branch_before") == "main"
    lr = st.get("latest_reconcile") or {}
    assert lr.get("git_branch_at_reconcile") == "builder/s1-abc1234567"

    human = format_builder_status_human(st)
    assert "branch_isolation: ok" in human
    assert "builder_branch: builder/s1-abc1234567" in human
    assert "git_branch_at_reconcile:" in human
