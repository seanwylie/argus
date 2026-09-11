"""Argus-root git worktree isolation for Builder (Phase 2C)."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from argus.builder.git_worktree_isolation import (
    argus_root_worktree_enabled,
    prepare_argus_root_worktree_isolation,
    remove_argus_root_worktree,
    sync_builder_artifacts_into_worktree,
)


def test_argus_root_worktree_env_default_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ARGUS_BUILDER_ARGUS_ROOT_WORKTREE", raising=False)
    assert argus_root_worktree_enabled() is True
    monkeypatch.setenv("ARGUS_BUILDER_ARGUS_ROOT_WORKTREE", "0")
    assert argus_root_worktree_enabled() is False


def test_prepare_worktree_creates_branch_and_path(tmp_path: Path) -> None:
    if subprocess.run(["git", "--version"], capture_output=True).returncode != 0:
        pytest.skip("git not available")
    subprocess.run(["git", "init"], cwd=tmp_path, check=True)
    (tmp_path / "README.md").write_text("x\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, check=True)

    res = prepare_argus_root_worktree_isolation(tmp_path, "p1", increment_id="s1")
    assert res.get("branch_isolation_status") == "ok", res
    assert res.get("git_builder_branch", "").startswith("builder/s1-")
    wtp = res.get("git_worktree_path")
    assert wtp and Path(wtp).is_dir()
    assert (Path(wtp) / "README.md").is_file()

    ok, err = remove_argus_root_worktree(tmp_path, Path(wtp))
    assert ok is True and err is None
    assert not Path(wtp).exists()


def test_sync_copies_product_subtree(tmp_path: Path) -> None:
    main = tmp_path / "main"
    wt = tmp_path / "wt"
    main.mkdir()
    wt.mkdir()
    pd = main / "products" / "x" / "generated"
    pd.mkdir(parents=True)
    (pd / "builder_next_prompt.md").write_text("p\n", encoding="utf-8")

    sync_builder_artifacts_into_worktree(
        main,
        wt,
        "x",
        products_dir=None,
        artifact_source="product_generated",
    )
    assert (wt / "products" / "x" / "generated" / "builder_next_prompt.md").read_text() == "p\n"


def test_invoke_argus_root_uses_worktree_when_git(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if subprocess.run(["git", "--version"], capture_output=True).returncode != 0:
        pytest.skip("git not available")
    from tests.test_builder_invoke import _write_generated

    def _skip_nested_product_git(*_a: object, **_k: object) -> dict:
        return {}

    monkeypatch.setattr(
        "argus.builder.invoke.ensure_product_git_workspace",
        _skip_nested_product_git,
    )

    subprocess.run(["git", "init"], cwd=tmp_path, check=True)
    _write_generated(tmp_path, "wt1", in_runs=False)
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, check=True)

    agent = tmp_path / "fake_agent.sh"
    agent.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    os.chmod(agent, 0o755)
    monkeypatch.setenv("ARGUS_AGENT_CLI", str(agent))
    monkeypatch.setenv("ARGUS_BUILDER_AGENT_SANDBOX", "none")
    monkeypatch.setenv("ARGUS_BUILDER_ALLOW_UNSANDBOXED", "1")

    from argus.builder.invoke import run_builder_invoke

    rec = run_builder_invoke(
        tmp_path,
        "wt1",
        execute=True,
        no_record=True,
        execution_backend="agent",
        allow_unsandboxed=True,
    )
    gbi = rec.get("git_branch_isolation") or {}
    assert gbi.get("isolation_mode") == "argus_root_worktree"
    assert gbi.get("branch_isolation_status") in ("ok", "degraded_dirty_tree")
    assert gbi.get("worktree_removed_after_invoke") is True
    gb = rec.get("git_baseline") or {}
    assert gb.get("git_workspace_kind") == "argus_root_worktree"
