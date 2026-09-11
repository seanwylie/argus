"""Builder invoke: Phase 1 project_permission gate for --execute."""

from __future__ import annotations

import os
from pathlib import Path

from argus.builder.invoke import run_builder_invoke
from tests.test_builder_invoke import _write_generated


def _policy(root: Path, product_id: str, *, extra_yaml: str) -> None:
    p = root / "products" / product_id / "argus.policy.yaml"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        "schema: argus.project_permission_policy.v1\n" + extra_yaml,
        encoding="utf-8",
    )


def test_invoke_review_mode_does_not_record_permission_decision(tmp_path: Path) -> None:
    _write_generated(tmp_path, "rv")
    rec = run_builder_invoke(tmp_path, "rv", execute=False, no_record=True)
    assert rec.get("project_permission_decision") is None


def test_invoke_execute_denied_when_builder_execute_no(tmp_path: Path) -> None:
    _write_generated(tmp_path, "deny")
    _policy(tmp_path, "deny", extra_yaml='builder_execute: "no"\n')
    rec = run_builder_invoke(tmp_path, "deny", execute=True, no_record=True)
    assert rec["mode"] == "execute"
    assert rec["invocation_status"] == "failed"
    ppd = rec.get("project_permission_decision")
    assert isinstance(ppd, dict)
    assert ppd.get("aggregate_decision") == "refused"
    assert ppd.get("execution_proceeds") is False
    assert "builder_execute" in (rec.get("error") or "")


def test_invoke_execute_proceeds_when_policy_allows(tmp_path: Path) -> None:
    _write_generated(tmp_path, "ok")
    _policy(tmp_path, "ok", extra_yaml='builder_execute: "yes"\n')
    script = tmp_path / "fake_cursor.sh"
    script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    os.chmod(script, 0o755)
    old = os.environ.get("ARGUS_CURSOR_CLI")
    os.environ["ARGUS_CURSOR_CLI"] = str(script)
    try:
        rec = run_builder_invoke(tmp_path, "ok", execute=True, no_record=True)
    finally:
        if old is None:
            os.environ.pop("ARGUS_CURSOR_CLI", None)
        else:
            os.environ["ARGUS_CURSOR_CLI"] = old
    ppd = rec.get("project_permission_decision")
    assert isinstance(ppd, dict)
    assert ppd.get("aggregate_decision") == "allowed"
    assert ppd.get("execution_proceeds") is True
    assert rec["invocation_status"] == "ok"


def test_invoke_default_policy_allows_without_policy_file(tmp_path: Path) -> None:
    """Missing argus.policy.yaml uses defaults; builder_execute defaults to yes."""
    _write_generated(tmp_path, "ndef")
    script = tmp_path / "fake_cursor2.sh"
    script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    os.chmod(script, 0o755)
    old = os.environ.get("ARGUS_CURSOR_CLI")
    os.environ["ARGUS_CURSOR_CLI"] = str(script)
    try:
        rec = run_builder_invoke(tmp_path, "ndef", execute=True, no_record=True)
    finally:
        if old is None:
            os.environ.pop("ARGUS_CURSOR_CLI", None)
        else:
            os.environ["ARGUS_CURSOR_CLI"] = old
    assert rec.get("project_permission_decision", {}).get("aggregate_decision") == "allowed"
