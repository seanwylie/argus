"""Builder host readiness (static PATH/env checks)."""

from __future__ import annotations

import json
from argparse import Namespace

import pytest

from argus.builder.host_readiness import (
    STATUS_DEGRADED,
    STATUS_NOT_READY,
    STATUS_OK,
    compute_builder_host_readiness,
    exit_code_for_readiness,
    format_builder_host_readiness_human,
)
from argus.builder.sandbox import (
    ENV_ALLOW_UNSANDBOXED,
    ENV_BUILDER_SANDBOX,
    ENV_SKIP_NO_NEW_PRIVS,
)


def test_json_shape_and_schema() -> None:
    p = compute_builder_host_readiness()
    assert p["schema"] == "argus.builder.host_readiness.v1"
    assert p["overall_status"] in (STATUS_OK, STATUS_DEGRADED, STATUS_NOT_READY)
    assert isinstance(p["checks"], list)
    assert "env_effective" in p
    assert "notes" in p
    ids = {c["id"] for c in p["checks"]}
    assert "platform" in ids
    assert "bubblewrap" in ids
    assert "no_new_privs" in ids
    assert "git" in ids
    assert "builder_network_mode" in ids


def test_not_ready_without_bwrap_when_auto_and_no_allow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(ENV_ALLOW_UNSANDBOXED, raising=False)
    monkeypatch.delenv(ENV_BUILDER_SANDBOX, raising=False)
    monkeypatch.setattr("argus.builder.host_readiness.which_bwrap", lambda: None)
    p = compute_builder_host_readiness()
    assert p["overall_status"] == STATUS_NOT_READY
    assert exit_code_for_readiness(p, strict=False) == 1


def test_degraded_when_bwrap_missing_but_allow_unsandboxed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(ENV_ALLOW_UNSANDBOXED, "1")
    monkeypatch.delenv(ENV_BUILDER_SANDBOX, raising=False)
    monkeypatch.setattr("argus.builder.host_readiness.which_bwrap", lambda: None)
    monkeypatch.setattr("argus.builder.host_readiness.which_setpriv", lambda: "/usr/bin/setpriv")
    monkeypatch.setattr("argus.builder.host_readiness.shutil.which", lambda name: "/bin/git" if name == "git" else None)
    p = compute_builder_host_readiness()
    assert p["overall_status"] == STATUS_DEGRADED
    assert exit_code_for_readiness(p, strict=False) == 0
    assert exit_code_for_readiness(p, strict=True) == 1


def test_linux_missing_setpriv_is_degraded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("argus.builder.host_readiness.sys.platform", "linux")
    monkeypatch.delenv(ENV_SKIP_NO_NEW_PRIVS, raising=False)
    monkeypatch.delenv(ENV_ALLOW_UNSANDBOXED, raising=False)
    monkeypatch.delenv(ENV_BUILDER_SANDBOX, raising=False)
    monkeypatch.setattr("argus.builder.host_readiness.which_bwrap", lambda: "/usr/bin/bwrap")
    monkeypatch.setattr("argus.builder.host_readiness.which_setpriv", lambda: None)
    monkeypatch.setattr("argus.builder.host_readiness.shutil.which", lambda name: "/bin/git" if name == "git" else None)
    p = compute_builder_host_readiness()
    assert p["overall_status"] == STATUS_DEGRADED
    assert any(c["id"] == "no_new_privs" and c["status"] == "warn" for c in p["checks"])


def test_ok_when_tools_and_env_clean(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("argus.builder.host_readiness.sys.platform", "linux")
    monkeypatch.delenv(ENV_SKIP_NO_NEW_PRIVS, raising=False)
    monkeypatch.delenv(ENV_ALLOW_UNSANDBOXED, raising=False)
    monkeypatch.delenv(ENV_BUILDER_SANDBOX, raising=False)
    monkeypatch.setattr("argus.builder.host_readiness.which_bwrap", lambda: "/usr/bin/bwrap")
    monkeypatch.setattr("argus.builder.host_readiness.which_setpriv", lambda: "/usr/bin/setpriv")
    monkeypatch.setattr("argus.builder.host_readiness.shutil.which", lambda name: "/bin/git" if name == "git" else None)
    p = compute_builder_host_readiness()
    assert p["overall_status"] == STATUS_OK


def test_strict_exit_on_degraded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("argus.builder.host_readiness.sys.platform", "linux")
    monkeypatch.delenv(ENV_SKIP_NO_NEW_PRIVS, raising=False)
    monkeypatch.delenv(ENV_ALLOW_UNSANDBOXED, raising=False)
    monkeypatch.delenv(ENV_BUILDER_SANDBOX, raising=False)
    monkeypatch.setattr("argus.builder.host_readiness.which_bwrap", lambda: "/usr/bin/bwrap")
    monkeypatch.setattr("argus.builder.host_readiness.which_setpriv", lambda: None)
    monkeypatch.setattr("argus.builder.host_readiness.shutil.which", lambda name: "/bin/git" if name == "git" else None)
    p = compute_builder_host_readiness()
    assert exit_code_for_readiness(p, strict=True) == 1


def test_human_output_contains_overall() -> None:
    p = compute_builder_host_readiness()
    text = format_builder_host_readiness_human(p)
    assert "overall:" in text
    assert p["overall_status"] in text


def test_cli_doctor_json(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    from argus.cli.builder_cmd import run_builder_subcommand

    monkeypatch.setattr("argus.cli.builder_cmd.repo_root", lambda: __import__("pathlib").Path("."))
    args = Namespace(
        builder_command="doctor",
        json=True,
        strict=False,
        products_dir=None,
    )
    assert run_builder_subcommand(args) in (0, 1)
    out = capsys.readouterr().out
    data = json.loads(out)
    assert data["schema"] == "argus.builder.host_readiness.v1"
