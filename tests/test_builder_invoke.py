"""Builder invoke: resolve artifacts, optional Cursor subprocess, invocation record."""

from __future__ import annotations

import json
import os
import subprocess
from argparse import Namespace
from pathlib import Path

import pytest

from argus.builder.invoke import (
    BuilderInvokeError,
    assess_prepared_contract_vs_declared,
    build_agent_argv,
    resolve_prepared_artifacts,
    run_builder_invoke,
)
from argus.builder.sandbox import which_bwrap
from argus.cli.builder_cmd import run_builder_subcommand


def _minimal_next_expansion(root: Path, product_id: str) -> None:
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


def _write_generated(root: Path, product_id: str, *, in_runs: bool = False) -> None:
    _minimal_next_expansion(root, product_id)
    if in_runs:
        d = root / "runs" / "builder" / "prepare" / product_id
    else:
        d = root / "products" / product_id / "generated"
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


def test_resolve_prefers_product_generated(tmp_path: Path) -> None:
    _write_generated(tmp_path, "p", in_runs=False)
    _write_generated(tmp_path, "p", in_runs=True)
    r = resolve_prepared_artifacts(tmp_path, "p")
    assert r.source == "product_generated"
    assert "products" in str(r.prompt_md)


def test_resolve_falls_back_to_runs_prepare(tmp_path: Path) -> None:
    _write_generated(tmp_path, "p", in_runs=True)
    r = resolve_prepared_artifacts(tmp_path, "p")
    assert r.source == "runs_prepare"


def test_resolve_missing_raises(tmp_path: Path) -> None:
    with pytest.raises(BuilderInvokeError, match="No prepared Builder artifacts"):
        resolve_prepared_artifacts(tmp_path, "missing")


def test_invoke_review_writes_record(tmp_path: Path) -> None:
    _write_generated(tmp_path, "inv1")
    rec = run_builder_invoke(tmp_path, "inv1")
    assert rec.get("execution_backend") == "cursor"
    assert rec["mode"] == "review"
    assert rec["invocation_status"] == "not_executed"
    latest = tmp_path / "runs" / "builder" / "invoke" / "inv1" / "latest.json"
    assert latest.is_file()
    loaded = json.loads(latest.read_text(encoding="utf-8"))
    assert loaded["schema"] == "argus.builder_invoke_record.v1"
    assert loaded["resolved_target"]["id"] == "s1"
    assert loaded.get("git_baseline", {}).get("schema") == "argus.builder.git_baseline.v1"
    gbi = loaded.get("git_branch_isolation") or {}
    assert gbi.get("branch_isolation_status") == "skipped_not_executing"
    bc = loaded.get("builder_containment") or {}
    assert bc.get("schema") == "argus.builder.containment.v1"
    assert bc.get("containment_applied") == "not_applicable"


def test_invoke_dry_run_flag_mode(tmp_path: Path) -> None:
    _write_generated(tmp_path, "inv2")
    rec = run_builder_invoke(tmp_path, "inv2", explicit_dry_run=True)
    assert rec["mode"] == "dry_run"


def test_invoke_no_record(tmp_path: Path) -> None:
    _write_generated(tmp_path, "inv3")
    run_builder_invoke(tmp_path, "inv3", no_record=True)
    assert not (tmp_path / "runs" / "builder" / "invoke" / "inv3").exists()


def test_invoke_execute_uses_fake_cli(tmp_path: Path) -> None:
    _write_generated(tmp_path, "inv4")
    script = tmp_path / "fake_cursor.sh"
    script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    os.chmod(script, 0o755)
    old = os.environ.get("ARGUS_CURSOR_CLI")
    os.environ["ARGUS_CURSOR_CLI"] = str(script)
    try:
        rec = run_builder_invoke(tmp_path, "inv4", execute=True, no_record=True)
    finally:
        if old is None:
            os.environ.pop("ARGUS_CURSOR_CLI", None)
        else:
            os.environ["ARGUS_CURSOR_CLI"] = old
    assert rec["mode"] == "execute"
    assert rec["invocation_status"] == "ok"
    assert rec["exit_code"] == 0


def test_invoke_execute_missing_cli_fails(tmp_path: Path) -> None:
    _write_generated(tmp_path, "inv5")
    old = os.environ.get("ARGUS_CURSOR_CLI")
    os.environ["ARGUS_CURSOR_CLI"] = "/nonexistent/argus_builder_invoke_test_cursor"
    try:
        rec = run_builder_invoke(tmp_path, "inv5", execute=True, no_record=True)
    finally:
        if old is None:
            os.environ.pop("ARGUS_CURSOR_CLI", None)
        else:
            os.environ["ARGUS_CURSOR_CLI"] = old
    assert rec["invocation_status"] == "failed"
    assert "not found" in (rec.get("error") or "").lower() or rec.get("error")


def test_cli_invoke(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _write_generated(tmp_path, "cli")
    monkeypatch.setattr("argus.cli.builder_cmd.repo_root", lambda: tmp_path)
    args = Namespace(
        builder_command="invoke",
        product_id="cli",
        prepare_first=False,
        prepare_output="product",
        prompt_path=None,
        task_path=None,
        execute=False,
        dry_run=False,
        no_record=False,
        json=False,
        products_dir=None,
        backend="cursor",
    )
    assert run_builder_subcommand(args) == 0
    assert (tmp_path / "runs" / "builder" / "invoke" / "cli" / "latest.json").is_file()


def test_cli_execute_and_dry_run_mutually_exclusive(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _write_generated(tmp_path, "ex")
    monkeypatch.setattr("argus.cli.builder_cmd.repo_root", lambda: tmp_path)
    args = Namespace(
        builder_command="invoke",
        product_id="ex",
        prepare_first=False,
        prepare_output="product",
        prompt_path=None,
        task_path=None,
        execute=True,
        dry_run=True,
        no_record=False,
        json=False,
        products_dir=None,
        backend="cursor",
    )
    assert run_builder_subcommand(args) == 2


def test_prepare_first_generates_then_invokes(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    pid = "pf"
    _minimal_next_expansion(tmp_path, pid)
    monkeypatch.setattr("argus.cli.builder_cmd.repo_root", lambda: tmp_path)
    args = Namespace(
        builder_command="invoke",
        product_id=pid,
        prepare_first=True,
        prepare_output="product",
        prompt_path=None,
        task_path=None,
        execute=False,
        dry_run=False,
        no_record=False,
        json=True,
        products_dir=None,
        backend="cursor",
    )
    assert run_builder_subcommand(args) == 0
    assert (tmp_path / "products" / pid / "generated" / "builder_next_prompt.md").is_file()


def test_invoke_record_includes_contract_status_aligned(tmp_path: Path) -> None:
    _write_generated(tmp_path, "cs")
    rec = run_builder_invoke(tmp_path, "cs", no_record=True)
    assert rec["prepared_contract_status"] == "aligned"
    assert rec["declared_target"]["id"] == "s1"


def test_assess_stale_vs_declared(tmp_path: Path) -> None:
    _minimal_next_expansion(tmp_path, "st")
    task = {
        "resolved_target": {"id": "not_s1", "target_type": "content_slot", "group_id": "b1"},
    }
    status, dec, prep, rel = assess_prepared_contract_vs_declared(
        tmp_path,
        "st",
        task=task,
        products_dir=None,
        explicit_paths=False,
    )
    assert status == "stale"
    assert dec and dec.get("id") == "s1"
    assert prep and prep.get("id") == "not_s1"
    assert rel and "next_expansion.json" in rel


def test_invoke_stale_review_warns(tmp_path: Path) -> None:
    _minimal_next_expansion(tmp_path, "sw")
    d = tmp_path / "products" / "sw" / "generated"
    d.mkdir(parents=True)
    (d / "builder_next_prompt.md").write_text("# p\n", encoding="utf-8")
    (d / "builder_task.json").write_text(
        json.dumps(
            {
                "schema": "argus.builder_prepare_task.v1",
                "resolved_target": {"id": "old_only", "target_type": "content_slot", "group_id": "b1"},
            }
        ),
        encoding="utf-8",
    )
    rec = run_builder_invoke(tmp_path, "sw", no_record=True)
    assert rec["prepared_contract_status"] == "stale"
    assert rec["invocation_status"] == "not_executed"
    assert "stale" in rec["note"].lower()


def test_invoke_stale_execute_blocked_without_prepare_first(tmp_path: Path) -> None:
    _minimal_next_expansion(tmp_path, "sb")
    d = tmp_path / "products" / "sb" / "generated"
    d.mkdir(parents=True)
    (d / "builder_next_prompt.md").write_text("# p\n", encoding="utf-8")
    (d / "builder_task.json").write_text(
        json.dumps(
            {
                "schema": "argus.builder_prepare_task.v1",
                "resolved_target": {"id": "stale_id", "target_type": "content_slot"},
            }
        ),
        encoding="utf-8",
    )
    script = tmp_path / "fake_cursor.sh"
    script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    os.chmod(script, 0o755)
    old = os.environ.get("ARGUS_CURSOR_CLI")
    os.environ["ARGUS_CURSOR_CLI"] = str(script)
    try:
        rec = run_builder_invoke(tmp_path, "sb", execute=True, no_record=True)
    finally:
        if old is None:
            os.environ.pop("ARGUS_CURSOR_CLI", None)
        else:
            os.environ["ARGUS_CURSOR_CLI"] = old
    assert rec["invocation_status"] == "failed"
    assert rec["prepared_contract_status"] == "stale"
    assert "stale" in (rec.get("error") or "").lower()


def test_invoke_prepare_first_then_execute_aligned(tmp_path: Path) -> None:
    pid = "pfix"
    _minimal_next_expansion(tmp_path, pid)
    d = tmp_path / "products" / pid / "generated"
    d.mkdir(parents=True)
    (d / "builder_next_prompt.md").write_text("# old\n", encoding="utf-8")
    (d / "builder_task.json").write_text(
        json.dumps(
            {
                "schema": "argus.builder_prepare_task.v1",
                "resolved_target": {"id": "wrong", "target_type": "content_slot", "group_id": "b1"},
            }
        ),
        encoding="utf-8",
    )
    script = tmp_path / "fake_cursor.sh"
    script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    os.chmod(script, 0o755)
    old = os.environ.get("ARGUS_CURSOR_CLI")
    os.environ["ARGUS_CURSOR_CLI"] = str(script)
    try:
        rec = run_builder_invoke(
            tmp_path, pid, prepare_first=True, execute=True, no_record=True
        )
    finally:
        if old is None:
            os.environ.pop("ARGUS_CURSOR_CLI", None)
        else:
            os.environ["ARGUS_CURSOR_CLI"] = old
    assert rec["prepared_contract_status"] == "aligned"
    assert rec["invocation_status"] == "ok"
    assert rec["prepare_first"] is True


def test_cli_stale_execute_returns_nonzero(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _minimal_next_expansion(tmp_path, "cx")
    d = tmp_path / "products" / "cx" / "generated"
    d.mkdir(parents=True)
    (d / "builder_next_prompt.md").write_text("# p\n", encoding="utf-8")
    (d / "builder_task.json").write_text(
        json.dumps(
            {
                "schema": "argus.builder_prepare_task.v1",
                "resolved_target": {"id": "x", "target_type": "content_slot"},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("argus.cli.builder_cmd.repo_root", lambda: tmp_path)
    args = Namespace(
        builder_command="invoke",
        product_id="cx",
        prepare_first=False,
        prepare_output="product",
        prompt_path=None,
        task_path=None,
        execute=True,
        dry_run=False,
        no_record=True,
        json=False,
        products_dir=None,
        backend="cursor",
    )
    assert run_builder_subcommand(args) == 1


def test_invoke_default_backend_cursor(tmp_path: Path) -> None:
    _write_generated(tmp_path, "db")
    rec = run_builder_invoke(tmp_path, "db", no_record=True)
    assert rec.get("execution_backend") == "cursor"


def test_build_agent_argv_execute_shape_no_plan_mode(tmp_path: Path) -> None:
    argv = build_agent_argv(
        agent_cli="agent",
        prompt_text="PROMPT",
        extra_args=["--trust"],
        repo_root=tmp_path,
    )
    joined = " ".join(argv)
    assert "--mode=plan" not in joined
    assert "--mode=ask" not in joined
    assert argv[0] == "agent"
    assert "-p" in argv
    assert "--output-format" in argv
    assert argv[argv.index("--output-format") + 1] == "json"
    assert "--workspace" in argv
    assert argv[-1] == "PROMPT"
    assert str(tmp_path.resolve()) in argv


def test_invoke_agent_review_no_subprocess(tmp_path: Path) -> None:
    _write_generated(tmp_path, "ar")
    rec = run_builder_invoke(tmp_path, "ar", execution_backend="agent", no_record=True)
    assert rec["execution_backend"] == "agent"
    assert rec["invocation_method"] == "agent_cli_execute_preview"
    assert rec["invocation_status"] == "not_executed"
    assert rec.get("agent_cli_plan_mode") is False
    assert rec.get("agent_headless_execute_argv") is True
    cmd = " ".join(rec["command"])
    assert "--mode=plan" not in cmd
    assert "-p" in rec["command"]
    assert "--output-format" in rec["command"]
    assert "--workspace" in rec["command"]


@pytest.mark.skipif(not which_bwrap(), reason="bubblewrap not installed")
def test_invoke_agent_execute_fake_cli(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_generated(tmp_path, "ag")
    script = tmp_path / "fake_agent.sh"
    script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    os.chmod(script, 0o755)
    old = os.environ.get("ARGUS_AGENT_CLI")
    os.environ["ARGUS_AGENT_CLI"] = str(script)
    monkeypatch.setattr(
        "argus.builder.invoke.subprocess.run",
        lambda *a, **k: subprocess.CompletedProcess(a[0], 0, "", ""),
    )
    try:
        rec = run_builder_invoke(
            tmp_path,
            "ag",
            execute=True,
            execution_backend="agent",
            no_record=True,
            allow_unsandboxed=True,
        )
    finally:
        if old is None:
            os.environ.pop("ARGUS_AGENT_CLI", None)
        else:
            os.environ["ARGUS_AGENT_CLI"] = old
    assert rec["invocation_method"] == "agent_cli_execute_mode"
    assert rec.get("agent_cli_plan_mode") is False
    assert rec["invocation_status"] == "ok"
    assert rec["exit_code"] == 0
    assert rec.get("agent_stdout_summary") == "" or rec.get("agent_stdout_summary") is not None
    assert "--mode=plan" not in " ".join(rec["command"])
    bc = rec.get("builder_containment", {})
    assert bc.get("network_mode") == "default"
    assert bc.get("network_applied") is True
    assert bc.get("trust_degraded_network_open") is False
    fsm = bc.get("filesystem_scope_mode")
    assert fsm in ("product_scoped", "legacy_repo_rw")
    if fsm == "product_scoped":
        assert bc.get("repo_root_mount_mode") == "read_only"
    else:
        assert bc.get("repo_root_mount_mode") == "read_write"


def test_invoke_argus_root_agent_legacy_fs_and_workspace_trust_degraded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_generated(tmp_path, "aroot")
    monkeypatch.setattr(
        "argus.builder.invoke.resolve_git_workspace",
        lambda repo_root, product_id, products_dir=None: {
            "kind": "argus_root",
            "git_cwd": repo_root,
            "argus_path_prefix": "",
        },
    )
    script = tmp_path / "fake_agent.sh"
    script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    os.chmod(script, 0o755)
    old = os.environ.get("ARGUS_AGENT_CLI")
    os.environ["ARGUS_AGENT_CLI"] = str(script)
    monkeypatch.setattr(
        "argus.builder.invoke.subprocess.run",
        lambda *a, **k: subprocess.CompletedProcess(a[0], 0, "", ""),
    )
    try:
        rec = run_builder_invoke(
            tmp_path,
            "aroot",
            execute=True,
            execution_backend="agent",
            no_record=True,
            allow_unsandboxed=True,
        )
    finally:
        if old is None:
            os.environ.pop("ARGUS_AGENT_CLI", None)
        else:
            os.environ["ARGUS_AGENT_CLI"] = old
    assert rec.get("git_baseline", {}).get("git_workspace_kind") == "argus_root"
    bc = rec.get("builder_containment", {})
    assert bc.get("filesystem_scope_mode") == "legacy_repo_rw"
    assert bc.get("trust_degraded_workspace_scope") is True
    assert bc.get("filesystem_scope_reason") == "argus_root_workspace_not_product_scoped_filesystem"


def test_invoke_agent_missing_cli_fails(tmp_path: Path) -> None:
    _write_generated(tmp_path, "nm")
    old = os.environ.get("ARGUS_AGENT_CLI")
    os.environ["ARGUS_AGENT_CLI"] = "/nonexistent/argus_fake_agent_missing"
    try:
        rec = run_builder_invoke(
            tmp_path,
            "nm",
            execute=True,
            execution_backend="agent",
            no_record=True,
            allow_unsandboxed=True,
        )
    finally:
        if old is None:
            os.environ.pop("ARGUS_AGENT_CLI", None)
        else:
            os.environ["ARGUS_AGENT_CLI"] = old
    assert rec["invocation_status"] == "failed"
    assert "not found" in (rec.get("error") or "").lower()


def test_stale_blocks_agent_execute_before_subprocess(tmp_path: Path) -> None:
    _minimal_next_expansion(tmp_path, "sbag")
    d = tmp_path / "products" / "sbag" / "generated"
    d.mkdir(parents=True)
    (d / "builder_next_prompt.md").write_text("# p\n", encoding="utf-8")
    (d / "builder_task.json").write_text(
        json.dumps(
            {
                "schema": "argus.builder_prepare_task.v1",
                "resolved_target": {"id": "bad", "target_type": "content_slot", "group_id": "b1"},
            }
        ),
        encoding="utf-8",
    )
    script = tmp_path / "fake_agent.sh"
    script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    os.chmod(script, 0o755)
    old = os.environ.get("ARGUS_AGENT_CLI")
    os.environ["ARGUS_AGENT_CLI"] = str(script)
    try:
        rec = run_builder_invoke(
            tmp_path,
            "sbag",
            execute=True,
            execution_backend="agent",
            no_record=True,
            allow_unsandboxed=True,
        )
    finally:
        if old is None:
            os.environ.pop("ARGUS_AGENT_CLI", None)
        else:
            os.environ["ARGUS_AGENT_CLI"] = old
    assert rec["prepared_contract_status"] == "stale"
    assert rec["invocation_status"] == "failed"
    assert "agent_stdout_summary" not in rec


def test_bad_agent_extra_args_env_raises(tmp_path: Path) -> None:
    _write_generated(tmp_path, "badenv")
    old = os.environ.get("ARGUS_AGENT_EXTRA_ARGS")
    os.environ["ARGUS_AGENT_EXTRA_ARGS"] = 'unclosed"quote'
    try:
        with pytest.raises(BuilderInvokeError):
            run_builder_invoke(tmp_path, "badenv", execution_backend="agent", no_record=True)
    finally:
        if old is None:
            os.environ.pop("ARGUS_AGENT_EXTRA_ARGS", None)
        else:
            os.environ["ARGUS_AGENT_EXTRA_ARGS"] = old


def test_cli_invoke_backend_agent_dry_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_generated(tmp_path, "bag")
    monkeypatch.setattr("argus.cli.builder_cmd.repo_root", lambda: tmp_path)
    args = Namespace(
        builder_command="invoke",
        product_id="bag",
        prepare_first=False,
        prepare_output="product",
        prompt_path=None,
        task_path=None,
        execute=False,
        dry_run=True,
        no_record=True,
        json=False,
        products_dir=None,
        backend="agent",
    )
    assert run_builder_subcommand(args) == 0
    err = capsys.readouterr().err
    assert "Backend: agent" in err
