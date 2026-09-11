"""``argus dashboard summary`` refreshes portfolio builder_activity (reporting hook only)."""

from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path

import pytest

from argus.cli.dashboard_cmd import run_dashboard_command
from argus.portfolio.builder_activity import PORTFOLIO_BUILDER_ACTIVITY_SCHEMA


def test_dashboard_summary_writes_builder_activity(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    tmp = tmp_path
    # Patch where ``dashboard_cmd`` holds ``repo_root`` (import-bound; patching ``repo.repo_root`` is not enough).
    monkeypatch.setattr("argus.cli.dashboard_cmd.repo_root", lambda: tmp)

    args = Namespace(
        dashboard_command="summary",
        json=False,
        no_save=False,
        limit_history=5,
        products_dir=None,
    )
    assert run_dashboard_command(args) == 0
    latest = tmp / "runs" / "portfolio" / "builder_activity" / "latest.json"
    assert latest.is_file()
    raw = json.loads(latest.read_text(encoding="utf-8"))
    assert raw.get("schema") == PORTFOLIO_BUILDER_ACTIVITY_SCHEMA


def test_dashboard_summary_no_save_skips_builder_activity(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    tmp = tmp_path
    monkeypatch.setattr("argus.cli.dashboard_cmd.repo_root", lambda: tmp)

    args = Namespace(
        dashboard_command="summary",
        json=False,
        no_save=True,
        limit_history=5,
        products_dir=None,
    )
    assert run_dashboard_command(args) == 0
    assert not (tmp / "runs" / "portfolio" / "builder_activity" / "latest.json").is_file()
