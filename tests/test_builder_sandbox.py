"""Builder agent sandbox (bubblewrap + env stripping)."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import pytest

from argus.builder.landlock_support import should_attempt_landlock_for_fs_mode
from argus.builder.sandbox import (
    APPLIED_BWRAP,
    FS_SCOPE_ARGUS_ROOT_WORKTREE_SCOPED,
    FS_SCOPE_LEGACY_REPO_RW,
    FS_SCOPE_PRODUCT_SCOPED,
    NETWORK_MODE_ALLOW_ALL,
    NETWORK_MODE_DEFAULT,
    NETWORK_MODE_DISABLED,
    build_agent_containment,
    build_bwrap_argv,
    infer_builder_filesystem_scope,
    resolve_effective_network_mode,
    strip_builder_agent_environment,
    which_bwrap,
)


def test_strip_removes_aws_github() -> None:
    base = {
        "PATH": "/usr/bin",
        "HOME": "/home/x",
        "AWS_ACCESS_KEY_ID": "secret",
        "GITHUB_TOKEN": "g",
        "GIT_ASKPASS": "bad",
        "LANG": "C.UTF-8",
        "FOO": "bar",
    }
    out, names, n = strip_builder_agent_environment(base)
    assert "AWS_ACCESS_KEY_ID" not in out
    assert "GITHUB_TOKEN" not in out
    assert "FOO" not in out
    assert n >= 3


def test_agent_refused_without_bwrap(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setattr("argus.builder.sandbox.which_bwrap", lambda: None)
    monkeypatch.delenv("ARGUS_BUILDER_ALLOW_UNSANDBOXED", raising=False)
    inner = ["/bin/true"]
    r = build_agent_containment(
        repo_root=tmp_path,
        product_id="p",
        execution_backend="agent",
        execute=True,
        sandbox_cli=None,
        allow_unsandboxed_cli=False,
        inner_argv=inner,
    )
    assert r["error"] is not None
    assert "bubblewrap" in (r["error"] or "").lower()


def test_agent_allow_unsandboxed_when_no_bwrap(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setattr("argus.builder.sandbox.which_bwrap", lambda: None)
    inner = ["/bin/true"]
    r = build_agent_containment(
        repo_root=tmp_path,
        product_id="p",
        execution_backend="agent",
        execute=True,
        sandbox_cli="auto",
        allow_unsandboxed_cli=True,
        inner_argv=inner,
    )
    assert r["error"] is None
    assert r["builder_containment"]["trust_degraded_unsandboxed"] is True


def test_infer_explicit_outside_product_is_legacy(tmp_path) -> None:
    (tmp_path / "products" / "p").mkdir(parents=True)
    ext = tmp_path / "outside.md"
    ext.write_text("x", encoding="utf-8")
    m, reason, extra = infer_builder_filesystem_scope(
        tmp_path,
        "p",
        products_dir=None,
        artifact_source="explicit",
        explicit_prompt_path=ext,
        explicit_task_path=ext,
    )
    assert m == FS_SCOPE_LEGACY_REPO_RW
    assert reason == "explicit_paths_not_under_product_directory"
    assert extra == []


def test_resolve_effective_network_mode_cli_over_env() -> None:
    assert resolve_effective_network_mode("allow_all", env_value="disabled") == "allow_all"
    assert resolve_effective_network_mode(None, env_value="disabled") == "disabled"
    assert resolve_effective_network_mode(None, env_value=None) == NETWORK_MODE_DEFAULT


def test_build_bwrap_argv_disabled_inserts_unshare_net(tmp_path) -> None:
    env, _, _ = strip_builder_agent_environment({"PATH": "/usr/bin"})
    argv = build_bwrap_argv(
        bwrap_exe="/bin/bwrap",
        repo_root=tmp_path,
        product_id="x",
        inner_argv=["/bin/true"],
        env=env,
        scratch_root=tmp_path / "runs" / "builder" / ".sandbox",
        network_mode=NETWORK_MODE_DISABLED,
    )
    up = argv.index("--unshare-pid")
    assert argv[up + 1] == "--unshare-net"
    assert "--unshare-net" not in build_bwrap_argv(
        bwrap_exe="/bin/bwrap",
        repo_root=tmp_path,
        product_id="x",
        inner_argv=["/bin/true"],
        env=env,
        scratch_root=tmp_path / "runs" / "builder" / ".sandbox",
        network_mode=NETWORK_MODE_DEFAULT,
    )


def test_infer_runs_prepare_adds_prepare_rw(tmp_path) -> None:
    m, reason, extra = infer_builder_filesystem_scope(
        tmp_path,
        "p",
        products_dir=None,
        artifact_source="runs_prepare",
        explicit_prompt_path=None,
        explicit_task_path=None,
    )
    assert m == FS_SCOPE_PRODUCT_SCOPED
    assert reason is None
    assert extra == [(tmp_path / "runs" / "builder" / "prepare" / "p").resolve()]


def test_infer_argus_root_is_legacy_not_product_scoped(tmp_path) -> None:
    (tmp_path / "products" / "p").mkdir(parents=True)
    m, reason, extra = infer_builder_filesystem_scope(
        tmp_path,
        "p",
        products_dir=None,
        artifact_source="product_generated",
        explicit_prompt_path=None,
        explicit_task_path=None,
        git_workspace_kind="argus_root",
    )
    assert m == FS_SCOPE_LEGACY_REPO_RW
    assert reason == "argus_root_workspace_not_product_scoped_filesystem"
    assert extra == []


def test_infer_argus_root_worktree_scoped_when_worktree_under_repo(tmp_path) -> None:
    (tmp_path / "products" / "p").mkdir(parents=True)
    wt = tmp_path / "runs" / "builder" / "worktrees" / "p" / "wt1"
    wt.mkdir(parents=True)
    m, reason, extra = infer_builder_filesystem_scope(
        tmp_path,
        "p",
        products_dir=None,
        artifact_source="runs_prepare",
        explicit_prompt_path=None,
        explicit_task_path=None,
        git_workspace_kind="argus_root_worktree",
        worktree_host_path=wt,
    )
    assert m == FS_SCOPE_ARGUS_ROOT_WORKTREE_SCOPED
    assert reason is None
    assert extra == []


def test_infer_argus_root_worktree_legacy_when_path_missing(tmp_path) -> None:
    (tmp_path / "products" / "p").mkdir(parents=True)
    m, reason, extra = infer_builder_filesystem_scope(
        tmp_path,
        "p",
        products_dir=None,
        artifact_source="product_generated",
        explicit_prompt_path=None,
        explicit_task_path=None,
        git_workspace_kind="argus_root_worktree",
        worktree_host_path=None,
    )
    assert m == FS_SCOPE_LEGACY_REPO_RW
    assert reason == "argus_root_worktree_missing_worktree_path"


def test_infer_argus_root_worktree_legacy_when_path_outside_repo(tmp_path) -> None:
    (tmp_path / "products" / "p").mkdir(parents=True)
    outside = Path("/tmp") / f"argus_infer_outside_{os.getpid()}"
    m, reason, extra = infer_builder_filesystem_scope(
        tmp_path,
        "p",
        products_dir=None,
        artifact_source="product_generated",
        explicit_prompt_path=None,
        explicit_task_path=None,
        git_workspace_kind="argus_root_worktree",
        worktree_host_path=outside,
    )
    assert m == FS_SCOPE_LEGACY_REPO_RW
    assert reason == "argus_root_worktree_path_not_under_repo_root"


def test_build_bwrap_argv_product_scoped_has_ro_bind_and_rw_overlays(tmp_path) -> None:
    env, _, _ = strip_builder_agent_environment({"PATH": "/usr/bin"})
    srr = str(tmp_path.resolve())
    argv = build_bwrap_argv(
        bwrap_exe="/bin/bwrap",
        repo_root=tmp_path,
        product_id="x",
        inner_argv=["/bin/true"],
        env=env,
        scratch_root=tmp_path / "runs" / "builder" / ".sandbox",
        filesystem_scope_mode=FS_SCOPE_PRODUCT_SCOPED,
        extra_rw_paths=[tmp_path / "runs" / "builder" / "prepare" / "x"],
        git_workspace_kind="nested_product",
    )
    assert any(
        argv[i] == "--ro-bind" and argv[i + 1] == srr and argv[i + 2] == srr
        for i in range(len(argv) - 2)
    )
    prod = str((tmp_path / "products" / "x").resolve())
    assert prod in argv
    prep = str((tmp_path / "runs" / "builder" / "prepare" / "x").resolve())
    assert prep in argv
    assert argv.count("--bind") >= 3


def test_build_bwrap_argv_argus_root_worktree_scoped_ro_repo_rw_worktree_and_git(tmp_path) -> None:
    env, _, _ = strip_builder_agent_environment({"PATH": "/usr/bin"})
    (tmp_path / ".git").mkdir()
    wt = tmp_path / "runs" / "builder" / "worktrees" / "x" / "wt"
    wt.mkdir(parents=True)
    srr = str(tmp_path.resolve())
    argv = build_bwrap_argv(
        bwrap_exe="/bin/bwrap",
        repo_root=tmp_path,
        product_id="x",
        inner_argv=["/bin/true"],
        env=env,
        scratch_root=tmp_path / "runs" / "builder" / ".sandbox",
        filesystem_scope_mode=FS_SCOPE_ARGUS_ROOT_WORKTREE_SCOPED,
        worktree_root=wt,
    )
    assert any(
        argv[i] == "--ro-bind" and argv[i + 1] == srr and argv[i + 2] == srr
        for i in range(len(argv) - 2)
    )
    assert str(wt.resolve()) in argv
    assert str((tmp_path / ".git").resolve()) in argv
    assert argv[argv.index("--chdir") + 1] == str(wt.resolve())


def test_build_bwrap_argv_legacy_single_repo_bind(tmp_path) -> None:
    env, _, _ = strip_builder_agent_environment({"PATH": "/usr/bin"})
    srr = str(tmp_path.resolve())
    argv = build_bwrap_argv(
        bwrap_exe="/bin/bwrap",
        repo_root=tmp_path,
        product_id="x",
        inner_argv=["/bin/true"],
        env=env,
        scratch_root=tmp_path / "runs" / "builder" / ".sandbox",
        filesystem_scope_mode=FS_SCOPE_LEGACY_REPO_RW,
    )
    assert any(
        argv[i] == "--bind" and argv[i + 1] == srr and argv[i + 2] == srr
        for i in range(len(argv) - 2)
    )
    assert not any(
        argv[i] == "--ro-bind" and argv[i + 1] == srr and argv[i + 2] == srr
        for i in range(len(argv) - 2)
    )


@pytest.mark.skipif(not which_bwrap(), reason="bubblewrap not installed")
def test_allow_all_network_sets_trust_degraded_network_open(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    inner = ["/bin/true"]
    r = build_agent_containment(
        repo_root=tmp_path,
        product_id="p",
        execution_backend="agent",
        execute=True,
        sandbox_cli="bwrap",
        allow_unsandboxed_cli=False,
        inner_argv=inner,
        network_mode_cli=NETWORK_MODE_ALLOW_ALL,
    )
    assert r["error"] is None
    assert r["builder_containment"]["network_mode"] == NETWORK_MODE_ALLOW_ALL
    assert r["builder_containment"]["trust_degraded_network_open"] is True


@pytest.mark.skipif(not which_bwrap(), reason="bubblewrap not installed")
def test_default_network_no_trust_degraded_network_open(tmp_path) -> None:
    inner = ["/bin/true"]
    r = build_agent_containment(
        repo_root=tmp_path,
        product_id="p",
        execution_backend="agent",
        execute=True,
        sandbox_cli="bwrap",
        allow_unsandboxed_cli=False,
        inner_argv=inner,
        network_mode_cli=NETWORK_MODE_DEFAULT,
    )
    assert r["error"] is None
    assert r["builder_containment"]["network_mode"] == NETWORK_MODE_DEFAULT
    assert r["builder_containment"]["trust_degraded_network_open"] is False


@pytest.mark.skipif(not which_bwrap(), reason="bubblewrap not installed")
def test_agent_containment_argus_root_worktree_scoped_records_paths(tmp_path) -> None:
    (tmp_path / ".git").mkdir()
    wt = tmp_path / "runs" / "builder" / "worktrees" / "p" / "wt"
    wt.mkdir(parents=True)
    inner = ["/bin/true"]
    r = build_agent_containment(
        repo_root=tmp_path,
        product_id="p",
        execution_backend="agent",
        execute=True,
        sandbox_cli="bwrap",
        allow_unsandboxed_cli=False,
        inner_argv=inner,
        filesystem_scope_mode=FS_SCOPE_ARGUS_ROOT_WORKTREE_SCOPED,
        worktree_host_path=wt,
    )
    assert r["error"] is None
    bc = r["builder_containment"]
    assert bc["filesystem_scope_mode"] == FS_SCOPE_ARGUS_ROOT_WORKTREE_SCOPED
    assert bc["repo_root_mount_mode"] == "read_only"
    assert bc["product_mount_mode"] == "read_write_within_worktree_checkout"
    assert bc["worktree_rw_path"] == str(wt.resolve())
    assert bc["git_dir_mount_mode"] == "read_write"


@pytest.mark.skipif(not which_bwrap(), reason="bubblewrap not installed")
def test_bwrap_wraps_argv(tmp_path) -> None:
    inner = ["/bin/true"]
    r = build_agent_containment(
        repo_root=tmp_path,
        product_id="p",
        execution_backend="agent",
        execute=True,
        sandbox_cli="bwrap",
        allow_unsandboxed_cli=False,
        inner_argv=inner,
    )
    assert r["error"] is None
    assert r["builder_containment"]["containment_applied"] == APPLIED_BWRAP
    assert r["builder_containment"]["filesystem_scope_mode"] == FS_SCOPE_PRODUCT_SCOPED
    assert r["builder_containment"]["repo_root_mount_mode"] == "read_only"
    assert r["builder_containment"].get("network_mode") == NETWORK_MODE_DEFAULT
    assert r["builder_containment"].get("network_applied") is True
    assert "--ro-bind" in r["argv"]
    bw = which_bwrap()
    if sys.platform.startswith("linux") and shutil.which("setpriv"):
        assert r["argv"][0] == shutil.which("setpriv")
        assert r["argv"][1] == "--no-new-privs"
        assert bw in r["argv"]
        assert r["builder_containment"]["no_new_privs_applied"] is True
    else:
        assert r["argv"][0] == bw


@pytest.mark.skipif(not which_bwrap(), reason="bubblewrap not installed")
@pytest.mark.skipif(not which_bwrap(), reason="bubblewrap not installed")
@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="landlock wrapper is linux-only")
def test_linux_landlock_inserts_inner_launcher_when_enabled(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.delenv("ARGUS_BUILDER_LANDLOCK", raising=False)
    (tmp_path / "products" / "p").mkdir(parents=True)
    inner = ["/bin/true"]
    r = build_agent_containment(
        repo_root=tmp_path,
        product_id="p",
        execution_backend="agent",
        execute=True,
        sandbox_cli="bwrap",
        allow_unsandboxed_cli=False,
        inner_argv=inner,
        filesystem_scope_mode=FS_SCOPE_PRODUCT_SCOPED,
        git_workspace_kind="nested_product",
    )
    assert r["error"] is None
    assert should_attempt_landlock_for_fs_mode(FS_SCOPE_PRODUCT_SCOPED) is True
    bc = r["builder_containment"]
    assert bc.get("landlock_requested") is True
    assert r.get("landlock_status_file") is not None
    flat = " ".join(r["argv"])
    assert "argus.builder.landlock_launcher" in flat


@pytest.mark.skipif(not which_bwrap(), reason="bubblewrap not installed")
@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="landlock wrapper is linux-only")
def test_linux_landlock_skipped_for_legacy_repo_rw(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.delenv("ARGUS_BUILDER_LANDLOCK", raising=False)
    (tmp_path / "products" / "p").mkdir(parents=True)
    inner = ["/bin/true"]
    r = build_agent_containment(
        repo_root=tmp_path,
        product_id="p",
        execution_backend="agent",
        execute=True,
        sandbox_cli="bwrap",
        allow_unsandboxed_cli=False,
        inner_argv=inner,
        filesystem_scope_mode=FS_SCOPE_LEGACY_REPO_RW,
        git_workspace_kind="argus_root",
    )
    assert r["error"] is None
    flat = " ".join(r["argv"])
    assert "argus.builder.landlock_launcher" not in flat
    assert r["builder_containment"].get("landlock_skipped_reason") == "legacy_repo_rw_scope_bypasses_landlock"


@pytest.mark.skipif(not which_bwrap(), reason="bubblewrap not installed")
def test_linux_no_setpriv_degrades_trust(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setattr("argus.builder.sandbox._is_linux", lambda: True)
    monkeypatch.setattr("argus.builder.sandbox.which_setpriv", lambda: None)
    inner = ["/bin/true"]
    r = build_agent_containment(
        repo_root=tmp_path,
        product_id="p",
        execution_backend="agent",
        execute=True,
        sandbox_cli="bwrap",
        allow_unsandboxed_cli=False,
        inner_argv=inner,
    )
    assert r["error"] is None
    bc = r["builder_containment"]
    assert bc["no_new_privs_requested"] is True
    assert bc["no_new_privs_applied"] is False
    assert bc["trust_degraded_missing_no_new_privs"] is True
    assert r["argv"][0] == which_bwrap()


def test_non_linux_skips_no_new_privs(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setattr("argus.builder.sandbox._is_linux", lambda: False)
    monkeypatch.setattr("argus.builder.sandbox.which_bwrap", lambda: None)
    inner = ["/bin/true"]
    r = build_agent_containment(
        repo_root=tmp_path,
        product_id="p",
        execution_backend="agent",
        execute=True,
        sandbox_cli="none",
        allow_unsandboxed_cli=False,
        inner_argv=inner,
    )
    assert r["error"] is None
    assert r["builder_containment"]["no_new_privs_reason"] == "non_linux_skip"


def test_skip_env_disables_no_new_privs_request(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setattr("argus.builder.sandbox._is_linux", lambda: True)
    monkeypatch.setenv("ARGUS_BUILDER_SKIP_NO_NEW_PRIVS", "1")
    monkeypatch.setattr("argus.builder.sandbox.which_bwrap", lambda: None)
    inner = ["/bin/true"]
    r = build_agent_containment(
        repo_root=tmp_path,
        product_id="p",
        execution_backend="agent",
        execute=True,
        sandbox_cli="none",
        allow_unsandboxed_cli=False,
        inner_argv=inner,
    )
    assert r["error"] is None
    bc = r["builder_containment"]
    assert bc["no_new_privs_requested"] is False
    assert "ARGUS_BUILDER_SKIP_NO_NEW_PRIVS" in (bc.get("no_new_privs_reason") or "")


def test_status_fields_present(tmp_path) -> None:
    from argus.builder.status import compute_builder_status

    pid = "sb"
    inv = tmp_path / "runs" / "builder" / "invoke" / pid
    inv.mkdir(parents=True)
    import json

    (inv / "latest.json").write_text(
        json.dumps(
            {
                "schema": "argus.builder_invoke_record.v1",
                "resolved_target": {"id": "x", "target_type": "content_slot", "group_id": "b"},
                "execution_backend": "agent",
                "builder_containment": {
                    "containment_applied": "bwrap",
                    "containment_requested": "auto",
                    "trust_degraded_unsandboxed": False,
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
            }
        ),
        encoding="utf-8",
    )
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
    assert lv.get("containment_applied") == "bwrap"
    assert lv.get("no_new_privs_requested") is None
    assert lv.get("no_new_privs_applied") is None
