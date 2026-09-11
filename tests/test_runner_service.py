"""Tests for :mod:`argus.portfolio.runner_service`."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from argus.portfolio.runner_service import (
    PORTFOLIO_RUNNER_SERVICE_SCHEMA,
    STOP_COMPLETED,
    STOP_MAX_RUNS,
    STOP_SENTINEL,
    portfolio_runner_service_dir,
    render_runner_service_markdown,
    run_portfolio_runner_service,
    runner_service_stop_sentinel_path,
)


def _minimal_autonomous_payload(*, sid: str = "sess1", stop_reason: str = "max_cycles_reached") -> dict:
    return {
        "schema": "argus.portfolio_autonomous_runner.v1",
        "session_id": sid,
        "stop_reason": stop_reason,
        "stop_reason_codes": ["x"],
        "session_summary": "test summary line",
        "cycles_run": 1,
    }


def _run_files(repo: Path) -> int:
    r = repo / "runs"
    if not r.is_dir():
        return 0
    return sum(1 for p in r.rglob("*") if p.is_file())


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    (tmp_path / "runs").mkdir()
    return tmp_path


@patch("argus.portfolio.runner_service.run_portfolio_autonomous_session")
def test_one_shot(mock_auto: object, repo: Path) -> None:
    mock_auto.return_value = _minimal_autonomous_payload()

    out = run_portfolio_runner_service(
        repo,
        interval_seconds=0.0,
        max_runs=99,
        write_service_artifacts=False,
        write_session_artifacts=False,
        write_stage_artifacts=False,
    )

    assert out["schema"] == PORTFOLIO_RUNNER_SERVICE_SCHEMA
    assert out["loop_count"] == 1
    assert out["stop_reason"] == STOP_COMPLETED
    assert "runner_service.stop.one_shot" in out["stop_reason_codes"]
    mock_auto.assert_called_once()


@patch("argus.portfolio.runner_service.run_portfolio_autonomous_session")
def test_repeated_loop_stops_after_max_runs(mock_auto: object, repo: Path) -> None:
    mock_auto.return_value = _minimal_autonomous_payload()

    sleeps: list[float] = []

    def _sleep(d: float) -> None:
        sleeps.append(d)

    out = run_portfolio_runner_service(
        repo,
        interval_seconds=0.05,
        max_runs=3,
        write_service_artifacts=False,
        write_session_artifacts=False,
        write_stage_artifacts=False,
        sleep_fn=_sleep,
    )

    assert mock_auto.call_count == 3
    assert len(sleeps) == 2
    assert out["loop_count"] == 3
    assert out["stop_reason"] == STOP_MAX_RUNS
    assert out["current_status"] == "stopped"


@patch("argus.portfolio.runner_service.run_portfolio_autonomous_session")
def test_stop_sentinel_before_second_session(mock_auto: object, repo: Path) -> None:
    mock_auto.return_value = _minimal_autonomous_payload(sid="a")
    sp = runner_service_stop_sentinel_path(repo)

    def _sleep(_d: float) -> None:
        sp.parent.mkdir(parents=True, exist_ok=True)
        sp.write_text("", encoding="utf-8")

    out = run_portfolio_runner_service(
        repo,
        interval_seconds=0.01,
        max_runs=5,
        write_service_artifacts=False,
        write_session_artifacts=False,
        write_stage_artifacts=False,
        sleep_fn=_sleep,
    )

    assert mock_auto.call_count == 1
    assert out["stop_reason"] == STOP_SENTINEL
    assert any("sentinel" in c for c in out["stop_reason_codes"])


@patch("argus.portfolio.runner_service.run_portfolio_autonomous_session")
def test_stop_sentinel_before_any_session(mock_auto: object, repo: Path) -> None:
    sp = runner_service_stop_sentinel_path(repo)
    sp.parent.mkdir(parents=True, exist_ok=True)
    sp.write_text("", encoding="utf-8")

    out = run_portfolio_runner_service(
        repo,
        write_service_artifacts=False,
        write_session_artifacts=False,
        write_stage_artifacts=False,
    )

    mock_auto.assert_not_called()
    assert out["loop_count"] == 0
    assert out["stop_reason"] == STOP_SENTINEL


@patch("argus.portfolio.runner_service.run_portfolio_autonomous_session")
def test_dry_run_passed_through(mock_auto: object, repo: Path) -> None:
    mock_auto.return_value = _minimal_autonomous_payload()

    run_portfolio_runner_service(
        repo,
        dry_run=True,
        write_service_artifacts=False,
        write_session_artifacts=False,
        write_stage_artifacts=False,
    )

    assert mock_auto.call_args.kwargs.get("dry_run") is True


@patch("argus.portfolio.runner_service.run_portfolio_autonomous_session")
def test_no_save_no_files(mock_auto: object, repo: Path) -> None:
    mock_auto.return_value = _minimal_autonomous_payload()

    assert _run_files(repo) == 0

    run_portfolio_runner_service(
        repo,
        write_service_artifacts=False,
        write_session_artifacts=False,
        write_stage_artifacts=False,
    )

    assert _run_files(repo) == 0


@patch("argus.portfolio.runner_service.run_portfolio_autonomous_session")
def test_heartbeat_artifacts_written(mock_auto: object, repo: Path) -> None:
    mock_auto.return_value = _minimal_autonomous_payload()

    run_portfolio_runner_service(
        repo,
        dry_run=True,
        write_service_artifacts=True,
        write_session_artifacts=False,
        write_stage_artifacts=False,
    )

    d = portfolio_runner_service_dir(repo)
    assert (d / "latest.json").is_file()
    assert (d / "latest.md").is_file()
    stamped = list(d.glob("*.json"))
    assert len(stamped) >= 2


def test_render_markdown() -> None:
    md = render_runner_service_markdown(
        {
            "schema": PORTFOLIO_RUNNER_SERVICE_SCHEMA,
            "service_run_id": "rid",
            "updated_at_utc": "t",
            "current_status": "stopped",
            "stop_reason": STOP_COMPLETED,
            "stop_reason_codes": [],
            "loop_count": 1,
            "service_started_at_utc": "t0",
            "service_finished_at_utc": "t1",
            "last_run_started_at_utc": "t2",
            "last_run_finished_at_utc": "t3",
            "next_planned_run_at_utc": None,
            "last_session_id": "sid",
            "last_autonomous_stop_reason": "max_cycles_reached",
            "last_run_summary": "hi",
        }
    )
    assert "heartbeat" in md.lower()
    assert "sid" in md
    assert "coherence" in md.lower()


@patch("argus.portfolio.runner_service.load_artifact_coherence_operational_snapshot")
@patch("argus.portfolio.runner_service.run_portfolio_autonomous_session")
def test_service_artifact_includes_coherence_when_present(mock_auto: object, mock_snap: object, repo: Path) -> None:
    mock_auto.return_value = _minimal_autonomous_payload()
    mock_snap.return_value = {
        "present": True,
        "overall_status": "degraded",
        "run_id": "ac1",
        "evaluated_at_utc": "2026-01-01T00:00:00Z",
        "summary": "substrate note",
    }
    out = run_portfolio_runner_service(
        repo,
        interval_seconds=0.0,
        max_runs=99,
        write_service_artifacts=False,
        write_session_artifacts=False,
        write_stage_artifacts=False,
    )
    ac = out.get("artifact_coherence") or {}
    assert ac.get("present") is True
    assert ac.get("overall_status") == "degraded"
    assert ac.get("run_id") == "ac1"


@patch("argus.portfolio.runner_service.load_artifact_coherence_operational_snapshot")
@patch("argus.portfolio.runner_service.run_portfolio_autonomous_session")
def test_service_artifact_coherence_present_false_when_missing(mock_auto: object, mock_snap: object, repo: Path) -> None:
    mock_auto.return_value = _minimal_autonomous_payload()
    mock_snap.return_value = {"present": False}
    out = run_portfolio_runner_service(
        repo,
        interval_seconds=0.0,
        max_runs=99,
        write_service_artifacts=False,
        write_session_artifacts=False,
        write_stage_artifacts=False,
    )
    assert out.get("artifact_coherence") == {"present": False}
