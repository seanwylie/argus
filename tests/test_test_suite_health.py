"""Tests for :mod:`argus.debug.test_suite_health` and CLI."""

from __future__ import annotations

import builtins
import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from argus.cli.debug_cmd import run_debug_subcommand
from argus.debug.test_suite_health import (
    TEST_SUITE_HEALTH_SCHEMA,
    FailureRecord,
    build_health_payload,
    classify_failure,
    parse_junit_xml,
    parse_pytest_text,
    render_test_suite_health_terminal,
    write_test_suite_health_artifact,
)


def test_classify_orchestration_executor_blocking() -> None:
    c, b, _reason = classify_failure(
        "tests/test_orchestration_step_executor.py::test_x",
        "ValueError: temporal_refresh",
    )
    assert c == "orchestration_executor"
    assert b is True


def test_classify_north_star_non_blocking() -> None:
    c, b, _ = classify_failure(
        "tests/test_north_star_docs.py::test_burn_down",
        "AssertionError: 'Burn-down' not found",
    )
    assert c == "docs_word"
    assert b is False


def test_classify_dashboard_blocking() -> None:
    c, b, _ = classify_failure(
        "tests/test_operator_console_data.py::test_x",
        None,
    )
    assert c == "dashboard"
    assert b is True


def test_parse_pytest_text() -> None:
    log = """
FAILED tests/a.py::t1 - AssertionError: x
ERROR tests/b.py::t2 - RuntimeError: y
"""
    fs = parse_pytest_text(log)
    assert len(fs) == 2
    assert fs[0].test_id == "tests/a.py::t1"
    assert fs[1].kind == "error"


def test_parse_junit_xml_failure(tmp_path: Path) -> None:
    p = tmp_path / "out.xml"
    p.write_text(
        """<?xml version="1.0" encoding="utf-8"?>
<testsuites>
  <testsuite name="pytest" tests="2" failures="1" errors="0" skipped="0">
    <testcase classname="tests.test_z.TestC" name="test_ok" time="0.1" />
    <testcase classname="tests.test_z.TestC" name="test_bad" time="0.1">
      <failure message="AssertionError">assert 0</failure>
    </testcase>
  </testsuite>
</testsuites>
""",
        encoding="utf-8",
    )
    failures, counts = parse_junit_xml(p)
    assert counts.get("tests") == 2
    assert len(failures) == 1
    assert "test_bad" in failures[0].test_id


def test_build_health_payload_blocking_vs_candidates() -> None:
    fr = [
        FailureRecord(
            test_id="tests/test_orchestration_step_executor.py::test_chain",
            message="boom",
            kind="failure",
        ),
        FailureRecord(
            test_id="tests/test_north_star_docs.py::test_x",
            message="Burn-down not found",
            kind="failure",
        ),
    ]
    pl = build_health_payload(
        failures=fr,
        total_passed=10,
        total_errors=None,
        total_tests=12,
        source="junit_xml",
        pytest_cmd=None,
        junit_path="/tmp/x.xml",
        text_path=None,
    )
    assert pl["schema"] == TEST_SUITE_HEALTH_SCHEMA
    assert pl["total_failed"] == 2
    assert pl["total_passed"] == 10
    assert any(
        x.get("category") == "orchestration_executor"
        for x in pl["likely_blocking_for_stabilization"]
    )
    assert any("north_star" in x.get("test_id", "") for x in pl["known_failure_candidates"])


def test_write_artifact(tmp_path: Path) -> None:
    pl = build_health_payload(
        failures=[],
        total_passed=0,
        total_errors=None,
        total_tests=0,
        source="junit_xml",
        pytest_cmd=None,
        junit_path=None,
        text_path=None,
    )
    out = write_test_suite_health_artifact(tmp_path, pl)
    assert out.name == "latest.json"
    assert (tmp_path / "runs" / "debug" / "test_suite_health" / "latest.json").is_file()
    raw = json.loads(out.read_text(encoding="utf-8"))
    assert raw["schema"] == TEST_SUITE_HEALTH_SCHEMA


def test_render_terminal_includes_clusters() -> None:
    pl = build_health_payload(
        failures=[
            FailureRecord(
                test_id="tests/test_orchestration_step_executor.py::t1",
                message="e",
                kind="failure",
            ),
        ],
        total_passed=None,
        total_errors=None,
        total_tests=None,
        source="pytest_text",
        pytest_cmd=None,
        junit_path=None,
        text_path=None,
    )
    txt = render_test_suite_health_terminal(pl)
    assert "orchestration_executor" in txt
    assert "Suggested next targets" in txt


def test_cli_json_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    junit = tmp_path / "j.xml"
    junit.write_text(
        """<?xml version="1.0" encoding="utf-8"?>
<testsuites><testsuite tests="1" failures="1" errors="0" skipped="0">
<testcase classname="tests.foo.TestT" name="test_x" time="0">
<failure message="AssertionError">x</failure>
</testcase></testsuite></testsuites>""",
        encoding="utf-8",
    )

    class Args:
        debug_command = "test-suite-health"
        json = True
        junit_xml = str(junit)
        pytest_text = None
        pytest_args = None
        no_write_artifact = True

    captured: list[str] = []

    def _fake_print(*args: object, **kwargs: object) -> None:
        if args:
            captured.append(str(args[0]))

    with patch.object(builtins, "print", _fake_print):
        with patch("argus.cli.debug_cmd.repo_root", return_value=tmp_path):
            code = run_debug_subcommand(Args())
    assert code == 0
    out = json.loads(captured[0])
    assert out["schema"] == TEST_SUITE_HEALTH_SCHEMA
    assert out["total_failed"] == 1


def test_cli_help_registered() -> None:
    r = subprocess.run(
        [sys.executable, "-m", "argus.cli.main", "debug", "test-suite-health", "--help"],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert r.returncode == 0
    assert "test-suite-health" in r.stdout.lower() or "junit" in r.stdout.lower()
