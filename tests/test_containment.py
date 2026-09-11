"""Credential containment v0 — policy, sanitization, capability map."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from argus.containment.policy import (
    ENV_CONTAINMENT,
    build_capability_map,
    refuse_unless_capability,
    sanitized_subprocess_environment,
    subprocess_env_for_repo,
)
from argus.containment.report import render_escalation_report


def test_sanitized_subprocess_environment_strips_aws_and_tokens() -> None:
    base = {
        "PATH": "/usr/bin",
        "AWS_ACCESS_KEY_ID": "AKIA",
        "AWS_SECRET_ACCESS_KEY": "x",
        "GITHUB_TOKEN": "ghp_x",
        "SSH_AUTH_SOCK": "/tmp/agent",
        "HOME": "/home/u",
    }
    out = sanitized_subprocess_environment(base, policy=None)
    assert out["PATH"] == "/usr/bin"
    assert out["HOME"] == "/home/u"
    assert "AWS_ACCESS_KEY_ID" not in out
    assert "GITHUB_TOKEN" not in out
    assert "SSH_AUTH_SOCK" not in out


def test_subprocess_env_unchanged_when_not_enforced(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ENV_CONTAINMENT, raising=False)
    os.environ.pop(ENV_CONTAINMENT, None)
    (tmp_path / "config").mkdir()
    # no policy file
    e = subprocess_env_for_repo(tmp_path)
    assert e.get("PATH") == os.environ.get("PATH")


def test_subprocess_env_strips_when_flag_set(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_CONTAINMENT, "1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "should_strip")
    e = subprocess_env_for_repo(tmp_path)
    assert "AWS_ACCESS_KEY_ID" not in e
    monkeypatch.delenv(ENV_CONTAINMENT, raising=False)


def test_capability_map_declines_git_push_by_default(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname=x\nversion=0\n", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / ".git").mkdir()
    m = build_capability_map(tmp_path)
    assert m["capabilities"]["git_push"]["status"] == "declined"


def test_refuse_unless_capability_raises_for_git_push(tmp_path: Path) -> None:
    from argus.containment.policy import ContainmentError

    (tmp_path / "pyproject.toml").write_text("[project]\nname=x\nversion=0\n", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / ".git").mkdir()
    with pytest.raises(ContainmentError, match="git_push"):
        refuse_unless_capability(tmp_path, "git_push")


def test_escalation_report_renders(tmp_path: Path) -> None:
    text = render_escalation_report(tmp_path)
    assert "GitHub" in text
    assert "AWS" in text
    assert "least-privilege" in text.lower() or "least-privilege" in text.replace("-", "")
