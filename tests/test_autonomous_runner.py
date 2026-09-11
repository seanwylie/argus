"""Tests for :mod:`argus.portfolio.autonomous_runner`."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from argus.portfolio.autonomous_runner import (
    portfolio_autonomous_runner_dir,
    run_portfolio_autonomous_session,
)


def _minimal_cycle_payload(
    *,
    overall: str = "run_again",
    quiescence_rec: str = "continue",
    material_change_count: int = 1,
    flagged_n: int = 0,
    ok: bool = True,
    run_id: str = "cyc1",
    artifact_coherence: dict | None = None,
) -> dict:
    ac = artifact_coherence if artifact_coherence is not None else {
        "status": "ok",
        "overall_status": "valid",
        "run_id": "coh1",
        "paths": {},
    }
    return {
        "schema": "argus.portfolio_cycle.v1",
        "run_id": run_id,
        "ok": ok,
        "stages": {
            "portfolio_quiescence": {
                "status": "ok",
                "products_with_material_change_count": material_change_count,
            },
        },
        "summary": {
            "quiescence": {"recommendation": quiescence_rec, "portfolio_quiescent": False},
            "intervention": {"flagged_products": [{"product_id": f"p{i}"} for i in range(flagged_n)]},
            "overall_operator_recommendation": overall,
        },
        "artifact_coherence": ac,
    }


def _minimal_refresh() -> tuple[int, dict]:
    return 0, {"ok": True, "schema": "argus.portfolio_refresh.v1"}


def _minimal_lifecycle() -> dict:
    return {"schema": "argus.portfolio_lifecycle.v1", "run_id": "life1"}


def _minimal_summary() -> dict:
    return {
        "schema": "argus.operator_summary.v1",
        "run_id": "sum1",
        "headline_status": "healthy",
        "evaluated_at_utc": "t",
    }


def _minimal_narrative() -> dict:
    return {"schema": "argus.operator_narrative.v1", "run_id": "nar1", "evaluated_at_utc": "t"}


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    (tmp_path / "runs").mkdir()
    return tmp_path


@patch("argus.portfolio.autonomous_runner.write_portfolio_autonomous_session_artifacts")
@patch("argus.portfolio.autonomous_runner.run_operator_narrative")
@patch("argus.portfolio.autonomous_runner.run_operator_summary")
@patch("argus.portfolio.autonomous_runner.run_portfolio_lifecycle")
@patch("argus.portfolio.autonomous_runner.run_portfolio_cycle")
@patch("argus.portfolio.autonomous_runner.run_portfolio_refresh")
def test_single_cycle_success_max_cycles_reached(
    m_ref,
    m_cyc,
    m_life,
    m_sum,
    m_nar,
    m_write,
    repo: Path,
) -> None:
    m_ref.side_effect = lambda *a, **k: _minimal_refresh()
    m_cyc.return_value = _minimal_cycle_payload()
    m_life.return_value = _minimal_lifecycle()
    m_sum.return_value = _minimal_summary()
    m_nar.return_value = _minimal_narrative()

    pl = run_portfolio_autonomous_session(
        repo,
        max_cycles=1,
        limit_per_cycle=3,
        write_session_artifacts=True,
        dry_run=False,
    )
    assert pl["cycles_run"] == 1
    assert pl["stop_reason"] == "max_cycles_reached"
    assert pl["schema"] == "argus.portfolio_autonomous_runner.v1"
    assert m_ref.call_count == 1
    assert m_cyc.call_count == 1
    m_write.assert_called_once()


@patch("argus.portfolio.autonomous_runner.write_portfolio_autonomous_session_artifacts")
@patch("argus.portfolio.autonomous_runner.run_operator_narrative")
@patch("argus.portfolio.autonomous_runner.run_operator_summary")
@patch("argus.portfolio.autonomous_runner.run_portfolio_lifecycle")
@patch("argus.portfolio.autonomous_runner.run_portfolio_cycle")
@patch("argus.portfolio.autonomous_runner.run_portfolio_refresh")
def test_stops_on_quiescence(
    m_ref,
    m_cyc,
    m_life,
    m_sum,
    m_nar,
    m_write,
    repo: Path,
) -> None:
    m_ref.side_effect = lambda *a, **k: _minimal_refresh()
    m_cyc.return_value = _minimal_cycle_payload(quiescence_rec="wait")
    m_life.return_value = _minimal_lifecycle()
    m_sum.return_value = _minimal_summary()
    m_nar.return_value = _minimal_narrative()

    pl = run_portfolio_autonomous_session(repo, max_cycles=5, write_session_artifacts=True)
    assert pl["cycles_run"] == 1
    assert pl["stop_reason"] == "quiescence_recommendation"
    assert any("quiescence.wait" in c for c in pl["stop_reason_codes"])
    assert m_cyc.call_count == 1


@patch("argus.portfolio.autonomous_runner.write_portfolio_autonomous_session_artifacts")
@patch("argus.portfolio.autonomous_runner.run_operator_narrative")
@patch("argus.portfolio.autonomous_runner.run_operator_summary")
@patch("argus.portfolio.autonomous_runner.run_portfolio_lifecycle")
@patch("argus.portfolio.autonomous_runner.run_portfolio_cycle")
@patch("argus.portfolio.autonomous_runner.run_portfolio_refresh")
def test_stops_on_artifact_coherence_invalid(
    m_ref,
    m_cyc,
    m_life,
    m_sum,
    m_nar,
    m_write,
    repo: Path,
) -> None:
    m_ref.side_effect = lambda *a, **k: _minimal_refresh()
    m_cyc.return_value = _minimal_cycle_payload(
        artifact_coherence={
            "status": "ok",
            "overall_status": "invalid",
            "run_id": "badcoh",
            "paths": {"latest_json": str(repo / "runs/debug/artifact_coherence/latest.json")},
        }
    )
    m_life.return_value = _minimal_lifecycle()
    m_sum.return_value = _minimal_summary()
    m_nar.return_value = _minimal_narrative()

    pl = run_portfolio_autonomous_session(repo, max_cycles=5, write_session_artifacts=True)
    assert pl["cycles_run"] == 0
    assert pl["stop_reason"] == "artifact_coherence_invalid"
    assert pl["artifact_coherence_policy"]["stopped_for_invalid_substrate"] is True
    assert any("artifact_coherence_invalid" in c for c in pl["stop_reason_codes"])
    m_life.assert_not_called()
    m_sum.assert_not_called()
    m_nar.assert_not_called()


@patch("argus.portfolio.autonomous_runner.write_portfolio_autonomous_session_artifacts")
@patch("argus.portfolio.autonomous_runner.run_operator_narrative")
@patch("argus.portfolio.autonomous_runner.run_operator_summary")
@patch("argus.portfolio.autonomous_runner.run_portfolio_lifecycle")
@patch("argus.portfolio.autonomous_runner.run_portfolio_cycle")
@patch("argus.portfolio.autonomous_runner.run_portfolio_refresh")
def test_intervention_heavy_streak(
    m_ref,
    m_cyc,
    m_life,
    m_sum,
    m_nar,
    m_write,
    repo: Path,
) -> None:
    m_ref.side_effect = lambda *a, **k: _minimal_refresh()
    m_cyc.side_effect = [
        _minimal_cycle_payload(flagged_n=4, run_id="a"),
        _minimal_cycle_payload(flagged_n=4, run_id="b"),
    ]
    m_life.return_value = _minimal_lifecycle()
    m_sum.return_value = _minimal_summary()
    m_nar.return_value = _minimal_narrative()

    pl = run_portfolio_autonomous_session(
        repo,
        max_cycles=5,
        intervention_flagged_threshold=4,
        intervention_heavy_streak=2,
        write_session_artifacts=True,
    )
    assert pl["cycles_run"] == 2
    assert pl["stop_reason"] == "intervention_heavy_streak"


@patch("argus.portfolio.autonomous_runner.write_portfolio_autonomous_session_artifacts")
@patch("argus.portfolio.autonomous_runner.run_operator_narrative")
@patch("argus.portfolio.autonomous_runner.run_operator_summary")
@patch("argus.portfolio.autonomous_runner.run_portfolio_lifecycle")
@patch("argus.portfolio.autonomous_runner.run_portfolio_cycle")
@patch("argus.portfolio.autonomous_runner.run_portfolio_refresh")
def test_no_material_change_streak(
    m_ref,
    m_cyc,
    m_life,
    m_sum,
    m_nar,
    m_write,
    repo: Path,
) -> None:
    m_ref.side_effect = lambda *a, **k: _minimal_refresh()
    m_cyc.side_effect = [
        _minimal_cycle_payload(material_change_count=0, run_id="a"),
        _minimal_cycle_payload(material_change_count=0, run_id="b"),
        _minimal_cycle_payload(material_change_count=0, run_id="c"),
    ]
    m_life.return_value = _minimal_lifecycle()
    m_sum.return_value = _minimal_summary()
    m_nar.return_value = _minimal_narrative()

    pl = run_portfolio_autonomous_session(
        repo,
        max_cycles=10,
        no_material_change_streak_limit=3,
        write_session_artifacts=True,
    )
    assert pl["cycles_run"] == 3
    assert pl["stop_reason"] == "no_material_change_streak"


@patch("argus.portfolio.autonomous_runner.write_portfolio_autonomous_session_artifacts")
@patch("argus.portfolio.autonomous_runner.run_operator_narrative")
@patch("argus.portfolio.autonomous_runner.run_operator_summary")
@patch("argus.portfolio.autonomous_runner.run_portfolio_lifecycle")
@patch("argus.portfolio.autonomous_runner.run_portfolio_cycle")
@patch("argus.portfolio.autonomous_runner.run_portfolio_refresh")
def test_dry_run_skips_persistence_calls(
    m_ref,
    m_cyc,
    m_life,
    m_sum,
    m_nar,
    m_write,
    repo: Path,
) -> None:
    m_ref.side_effect = lambda *a, **k: _minimal_refresh()
    m_cyc.return_value = _minimal_cycle_payload()
    m_life.return_value = _minimal_lifecycle()
    m_sum.return_value = _minimal_summary()
    m_nar.return_value = _minimal_narrative()

    run_portfolio_autonomous_session(repo, max_cycles=1, dry_run=True, write_session_artifacts=False)

    m_ref.assert_called_once()
    args, kwargs = m_ref.call_args
    assert kwargs.get("no_save") is True

    m_cyc.assert_called_once()
    _, ckwargs = m_cyc.call_args
    assert ckwargs.get("dry_run") is True
    assert ckwargs.get("write_cycle_artifacts") is False
    assert ckwargs.get("write_stage_artifacts") is False

    m_life.assert_called_once()
    assert m_life.call_args.kwargs.get("write_artifacts") is False
    assert m_sum.call_args.kwargs.get("write_artifacts") is False
    assert m_nar.call_args.kwargs.get("write_artifacts") is False
    m_write.assert_not_called()


@patch("argus.portfolio.autonomous_runner.collect_promotion_opportunities")
@patch("argus.portfolio.autonomous_runner.write_portfolio_autonomous_session_artifacts")
@patch("argus.portfolio.autonomous_runner.run_operator_narrative")
@patch("argus.portfolio.autonomous_runner.run_operator_summary")
@patch("argus.portfolio.autonomous_runner.run_portfolio_lifecycle")
@patch("argus.portfolio.autonomous_runner.run_portfolio_cycle")
@patch("argus.portfolio.autonomous_runner.run_portfolio_refresh")
def test_no_save_skips_all_persistence(
    m_ref,
    m_cyc,
    m_life,
    m_sum,
    m_nar,
    m_write,
    m_collect,
    repo: Path,
) -> None:
    """``--no-save`` (write_session + write_stage false): same stage kwargs as dry-run; no session write."""
    m_ref.side_effect = lambda *a, **k: _minimal_refresh()
    m_cyc.return_value = _minimal_cycle_payload()
    m_life.return_value = _minimal_lifecycle()
    m_sum.return_value = _minimal_summary()
    m_nar.return_value = _minimal_narrative()
    m_collect.return_value = {
        "schema": "argus.promotion_opportunities.v1",
        "promotable_actions": [],
        "blocked_promotions": [],
        "promotion_recommendations": [],
    }

    run_portfolio_autonomous_session(
        repo,
        max_cycles=1,
        dry_run=False,
        write_session_artifacts=False,
        write_stage_artifacts=False,
    )

    m_ref.assert_called_once()
    assert m_ref.call_args.kwargs.get("no_save") is True

    _, ckwargs = m_cyc.call_args
    assert ckwargs.get("dry_run") is True
    assert ckwargs.get("write_cycle_artifacts") is False
    assert ckwargs.get("write_stage_artifacts") is False

    assert m_life.call_args.kwargs.get("write_artifacts") is False
    assert m_sum.call_args.kwargs.get("write_artifacts") is False
    assert m_nar.call_args.kwargs.get("write_artifacts") is False
    m_write.assert_not_called()


@patch("argus.portfolio.autonomous_runner.collect_promotion_opportunities")
@patch("argus.portfolio.autonomous_runner.write_portfolio_autonomous_session_artifacts")
@patch("argus.portfolio.autonomous_runner.run_operator_narrative")
@patch("argus.portfolio.autonomous_runner.run_operator_summary")
@patch("argus.portfolio.autonomous_runner.run_portfolio_lifecycle")
@patch("argus.portfolio.autonomous_runner.run_portfolio_cycle")
@patch("argus.portfolio.autonomous_runner.run_portfolio_refresh")
def test_no_save_and_dry_run_leave_runs_empty(
    m_ref,
    m_cyc,
    m_life,
    m_sum,
    m_nar,
    m_write,
    m_collect,
    repo: Path,
) -> None:
    m_ref.side_effect = lambda *a, **k: _minimal_refresh()
    m_cyc.return_value = _minimal_cycle_payload()
    m_life.return_value = _minimal_lifecycle()
    m_sum.return_value = _minimal_summary()
    m_nar.return_value = _minimal_narrative()
    m_collect.return_value = {
        "schema": "argus.promotion_opportunities.v1",
        "promotable_actions": [],
        "blocked_promotions": [],
        "promotion_recommendations": [],
    }

    def count_run_files() -> int:
        r = repo / "runs"
        if not r.is_dir():
            return 0
        return sum(1 for p in r.rglob("*") if p.is_file())

    assert count_run_files() == 0

    run_portfolio_autonomous_session(
        repo,
        max_cycles=1,
        dry_run=False,
        write_session_artifacts=False,
        write_stage_artifacts=False,
    )
    assert count_run_files() == 0
    m_write.assert_not_called()

    run_portfolio_autonomous_session(
        repo,
        max_cycles=1,
        dry_run=True,
        write_session_artifacts=False,
        write_stage_artifacts=True,
    )
    assert count_run_files() == 0
    m_write.assert_not_called()


@patch("argus.portfolio.autonomous_runner.collect_promotion_opportunities")
@patch("argus.portfolio.autonomous_runner.write_portfolio_autonomous_session_artifacts")
@patch("argus.portfolio.autonomous_runner.run_operator_narrative")
@patch("argus.portfolio.autonomous_runner.run_operator_summary")
@patch("argus.portfolio.autonomous_runner.run_portfolio_lifecycle")
@patch("argus.portfolio.autonomous_runner.run_portfolio_cycle")
@patch("argus.portfolio.autonomous_runner.run_portfolio_refresh")
def test_full_persistence_writes_session_and_stages(
    m_ref,
    m_cyc,
    m_life,
    m_sum,
    m_nar,
    m_write,
    m_collect,
    repo: Path,
) -> None:
    m_ref.side_effect = lambda *a, **k: _minimal_refresh()
    m_cyc.return_value = _minimal_cycle_payload()
    m_life.return_value = _minimal_lifecycle()
    m_sum.return_value = _minimal_summary()
    m_nar.return_value = _minimal_narrative()
    m_collect.return_value = {
        "schema": "argus.promotion_opportunities.v1",
        "promotable_actions": [],
        "blocked_promotions": [],
        "promotion_recommendations": [],
    }

    run_portfolio_autonomous_session(
        repo,
        max_cycles=1,
        dry_run=False,
        write_session_artifacts=True,
        write_stage_artifacts=True,
    )
    assert m_ref.call_args.kwargs.get("no_save") is False
    assert m_cyc.call_args.kwargs.get("dry_run") is False
    assert m_cyc.call_args.kwargs.get("write_cycle_artifacts") is True
    assert m_cyc.call_args.kwargs.get("write_stage_artifacts") is True
    assert m_life.call_args.kwargs.get("write_artifacts") is True
    m_write.assert_called_once()


@patch("argus.portfolio.autonomous_runner.write_portfolio_autonomous_session_artifacts")
@patch("argus.portfolio.autonomous_runner.run_operator_narrative")
@patch("argus.portfolio.autonomous_runner.run_operator_summary")
@patch("argus.portfolio.autonomous_runner.run_portfolio_lifecycle")
@patch("argus.portfolio.autonomous_runner.run_portfolio_cycle")
@patch("argus.portfolio.autonomous_runner.run_portfolio_refresh")
def test_stop_sentinel_second_iteration(
    m_ref,
    m_cyc,
    m_life,
    m_sum,
    m_nar,
    m_write,
    repo: Path,
) -> None:
    adir = portfolio_autonomous_runner_dir(repo)
    adir.mkdir(parents=True)
    (adir / "STOP").write_text("", encoding="utf-8")

    m_ref.side_effect = lambda *a, **k: _minimal_refresh()
    m_cyc.return_value = _minimal_cycle_payload()
    m_life.return_value = _minimal_lifecycle()
    m_sum.return_value = _minimal_summary()
    m_nar.return_value = _minimal_narrative()

    pl = run_portfolio_autonomous_session(repo, max_cycles=3, write_session_artifacts=True)
    assert pl["stop_reason"] == "explicit_stop_sentinel"
    assert pl["cycles_run"] == 1
    assert m_cyc.call_count == 1


@patch("argus.portfolio.autonomous_runner.write_portfolio_autonomous_session_artifacts")
@patch("argus.portfolio.autonomous_runner.run_operator_narrative")
@patch("argus.portfolio.autonomous_runner.run_operator_summary")
@patch("argus.portfolio.autonomous_runner.run_portfolio_lifecycle")
@patch("argus.portfolio.autonomous_runner.run_portfolio_cycle")
@patch("argus.portfolio.autonomous_runner.run_portfolio_refresh")
def test_refresh_failure_stops(
    m_ref,
    m_cyc,
    m_life,
    m_sum,
    m_nar,
    m_write,
    repo: Path,
) -> None:
    m_ref.return_value = (1, {"ok": False, "error": "invalid_products", "schema": "argus.portfolio_refresh.v1"})

    pl = run_portfolio_autonomous_session(repo, max_cycles=3, write_session_artifacts=True)
    assert pl["stop_reason"] == "portfolio_refresh_failed"
    assert pl["cycles_run"] == 0
    m_cyc.assert_not_called()


@patch("argus.portfolio.autonomous_runner.write_portfolio_autonomous_session_artifacts")
@patch("argus.portfolio.autonomous_runner.run_operator_narrative")
@patch("argus.portfolio.autonomous_runner.run_operator_summary")
@patch("argus.portfolio.autonomous_runner.run_portfolio_lifecycle")
@patch("argus.portfolio.autonomous_runner.run_portfolio_cycle")
@patch("argus.portfolio.autonomous_runner.run_portfolio_refresh")
def test_skip_flags_passed_to_cycle(
    m_ref,
    m_cyc,
    m_life,
    m_sum,
    m_nar,
    m_write,
    repo: Path,
) -> None:
    m_ref.side_effect = lambda *a, **k: _minimal_refresh()
    m_cyc.return_value = _minimal_cycle_payload(quiescence_rec="wait")
    m_life.return_value = _minimal_lifecycle()
    m_sum.return_value = _minimal_summary()
    m_nar.return_value = _minimal_narrative()

    run_portfolio_autonomous_session(
        repo,
        max_cycles=1,
        skip_import_failed=True,
        skip_waiting=True,
        write_session_artifacts=True,
    )
    assert m_cyc.call_args.kwargs.get("skip_import_failed") is True
    assert m_cyc.call_args.kwargs.get("skip_waiting") is True


@patch("argus.portfolio.autonomous_runner.write_portfolio_autonomous_session_artifacts")
@patch("argus.portfolio.autonomous_runner.run_operator_narrative")
@patch("argus.portfolio.autonomous_runner.run_operator_summary")
@patch("argus.portfolio.autonomous_runner.run_portfolio_lifecycle")
@patch("argus.portfolio.autonomous_runner.run_portfolio_cycle")
@patch("argus.portfolio.autonomous_runner.run_portfolio_refresh")
def test_per_cycle_ordering(
    m_ref,
    m_cyc,
    m_life,
    m_sum,
    m_nar,
    m_write,
    repo: Path,
) -> None:
    order: list[str] = []

    def track_ref(*a, **k):
        order.append("refresh")
        return _minimal_refresh()

    def track_cyc(*a, **k):
        order.append("cycle")
        return _minimal_cycle_payload(quiescence_rec="wait")

    def track_life(*a, **k):
        order.append("lifecycle")
        return _minimal_lifecycle()

    def track_sum(*a, **k):
        order.append("summary")
        return _minimal_summary()

    def track_nar(*a, **k):
        order.append("narrative")
        return _minimal_narrative()

    m_ref.side_effect = track_ref
    m_cyc.side_effect = track_cyc
    m_life.side_effect = track_life
    m_sum.side_effect = track_sum
    m_nar.side_effect = track_nar

    run_portfolio_autonomous_session(repo, max_cycles=1, write_session_artifacts=True)
    assert order == ["refresh", "cycle", "lifecycle", "summary", "narrative"]
