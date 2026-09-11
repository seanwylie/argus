"""Bounded autonomy memory confidence adjustment for :mod:`argus.portfolio.autonomous_runner`."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from argus.portfolio.autonomous_runner import (
    PORTFOLIO_AUTONOMOUS_RUNNER_SCHEMA,
    run_portfolio_autonomous_session,
)
from argus.portfolio.escalation_inbox import ESCALATION_INBOX_SCHEMA
from tests.test_autonomous_runner import (
    _minimal_cycle_payload,
    _minimal_lifecycle,
    _minimal_narrative,
    _minimal_refresh,
    _minimal_summary,
)


def _seed_eligible_memory(root: Path) -> None:
    """Four stamped sessions with repeated inspect_specific cycle-overall pattern."""
    d = root / "runs" / "portfolio" / "autonomous_runner"
    d.mkdir(parents=True)
    for i in range(4):
        sid = f"2026060{i}T000000Z"
        pl = {
            "schema": PORTFOLIO_AUTONOMOUS_RUNNER_SCHEMA,
            "session_id": sid,
            "finished_at_utc": f"2026-06-0{i + 1}T12:00:00Z",
            "stop_reason": "cycle_overall_recommendation",
            "stop_reason_codes": ["autonomous_runner.stop.cycle_overall.inspect_specific_products"],
            "lifecycle_session_influence": {"primary_signal": "neutral", "inputs_snapshot": {}},
            "promotable_actions": [],
            "blocked_promotions": [],
            "promotion_execution": {"allow_promotion": False, "skipped_reason": "x", "steps": []},
        }
        (d / f"{sid}.json").write_text(json.dumps(pl), encoding="utf-8")


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
def test_defer_inspect_allows_extra_cycle_when_eligible(
    m_ref,
    m_cyc,
    m_life,
    m_sum,
    m_nar,
    m_write,
    repo: Path,
) -> None:
    _seed_eligible_memory(repo)
    m_ref.side_effect = lambda *a, **k: _minimal_refresh()
    m_cyc.side_effect = [
        _minimal_cycle_payload(overall="inspect_specific_products", run_id="a"),
        _minimal_cycle_payload(overall="run_again", run_id="b"),
    ]
    m_life.return_value = _minimal_lifecycle()
    m_sum.return_value = _minimal_summary()
    m_nar.return_value = _minimal_narrative()

    pl = run_portfolio_autonomous_session(
        repo,
        max_cycles=1,
        limit_per_cycle=3,
        limit_history=30,
        write_session_artifacts=True,
        dry_run=False,
    )
    ca = pl.get("confidence_adjustment") or {}
    assert ca.get("eligible") is True
    assert ca.get("effective_max_cycles") == 2
    assert len(ca.get("evaluations_per_iteration") or []) == 2
    assert pl["cycles_run"] == 2
    assert pl["stop_reason"] == "max_cycles_reached"
    assert pl["confidence_adjustment"].get("continuation_consumed") is True
    assert any(
        "defer_cycle_overall_inspect_specific_products" in c for c in pl["stop_reason_codes"]
    )
    assert (
        pl["per_cycle_outcomes"][0].get("confidence_adjustment", {}).get("action")
        == "continue_once_then_reassess"
    )


@patch("argus.portfolio.autonomous_runner.write_portfolio_autonomous_session_artifacts")
@patch("argus.portfolio.autonomous_runner.run_operator_narrative")
@patch("argus.portfolio.autonomous_runner.run_operator_summary")
@patch("argus.portfolio.autonomous_runner.run_portfolio_lifecycle")
@patch("argus.portfolio.autonomous_runner.run_portfolio_cycle")
@patch("argus.portfolio.autonomous_runner.run_portfolio_refresh")
def test_unsafe_stop_no_defer(
    m_ref,
    m_cyc,
    m_life,
    m_sum,
    m_nar,
    m_write,
    repo: Path,
) -> None:
    _seed_eligible_memory(repo)
    m_ref.side_effect = lambda *a, **k: _minimal_refresh()
    m_cyc.side_effect = [
        _minimal_cycle_payload(overall="run_again", run_id="a"),
        _minimal_cycle_payload(flagged_n=4, run_id="b"),
        _minimal_cycle_payload(flagged_n=4, run_id="c"),
    ]
    m_life.return_value = _minimal_lifecycle()
    m_sum.return_value = _minimal_summary()
    m_nar.return_value = _minimal_narrative()

    pl = run_portfolio_autonomous_session(
        repo,
        max_cycles=5,
        limit_history=30,
        intervention_flagged_threshold=4,
        intervention_heavy_streak=2,
        write_session_artifacts=True,
    )
    assert pl["stop_reason"] == "intervention_heavy_streak"
    assert not any("confidence_adjustment.defer" in c for c in pl["stop_reason_codes"])
    assert pl["confidence_adjustment"].get("continuation_consumed") is False


@patch("argus.portfolio.autonomous_runner.write_portfolio_autonomous_session_artifacts")
@patch("argus.portfolio.autonomous_runner.run_operator_narrative")
@patch("argus.portfolio.autonomous_runner.run_operator_summary")
@patch("argus.portfolio.autonomous_runner.run_portfolio_lifecycle")
@patch("argus.portfolio.autonomous_runner.run_portfolio_cycle")
@patch("argus.portfolio.autonomous_runner.run_portfolio_refresh")
def test_escalation_unsafe_blocks_adjustment(
    m_ref,
    m_cyc,
    m_life,
    m_sum,
    m_nar,
    m_write,
    repo: Path,
) -> None:
    _seed_eligible_memory(repo)
    esc = repo / "runs" / "portfolio" / "escalation_inbox"
    esc.mkdir(parents=True)
    inbox = {
        "schema": ESCALATION_INBOX_SCHEMA,
        "open_items": [
            {
                "item_id": "esc-1",
                "category": "unsafe_to_continue",
                "severity": "critical",
                "source": "autonomous_runner",
                "product_id": None,
            }
        ],
    }
    (esc / "latest.json").write_text(json.dumps(inbox), encoding="utf-8")

    m_ref.side_effect = lambda *a, **k: _minimal_refresh()
    m_cyc.return_value = _minimal_cycle_payload(overall="inspect_specific_products", run_id="a")
    m_life.return_value = _minimal_lifecycle()
    m_sum.return_value = _minimal_summary()
    m_nar.return_value = _minimal_narrative()

    pl = run_portfolio_autonomous_session(repo, max_cycles=3, limit_history=30, write_session_artifacts=True)
    ca = pl["confidence_adjustment"]
    assert ca.get("eligible") is False
    assert ca.get("block_reason") == "open_escalation.unsafe_to_continue"
    assert pl["cycles_run"] == 1
    assert pl["stop_reason"] == "cycle_overall_recommendation"
    assert not any("confidence_adjustment.defer" in c for c in pl["stop_reason_codes"])


@patch("argus.portfolio.autonomous_runner.write_portfolio_autonomous_session_artifacts")
@patch("argus.portfolio.autonomous_runner.run_operator_narrative")
@patch("argus.portfolio.autonomous_runner.run_operator_summary")
@patch("argus.portfolio.autonomous_runner.run_portfolio_lifecycle")
@patch("argus.portfolio.autonomous_runner.run_portfolio_cycle")
@patch("argus.portfolio.autonomous_runner.run_portfolio_refresh")
def test_sparse_history_ineligible_no_behavior_change(
    m_ref,
    m_cyc,
    m_life,
    m_sum,
    m_nar,
    m_write,
    repo: Path,
) -> None:
    m_ref.side_effect = lambda *a, **k: _minimal_refresh()
    m_cyc.return_value = _minimal_cycle_payload(overall="inspect_specific_products", run_id="a")
    m_life.return_value = _minimal_lifecycle()
    m_sum.return_value = _minimal_summary()
    m_nar.return_value = _minimal_narrative()

    pl = run_portfolio_autonomous_session(repo, max_cycles=3, limit_history=30, write_session_artifacts=True)
    assert pl["confidence_adjustment"].get("eligible") is False
    assert pl["cycles_run"] == 1
    assert pl["stop_reason"] == "cycle_overall_recommendation"
    assert pl["confidence_adjustment"].get("continuation_consumed") is False


def test_confidence_adjustment_visible_without_mock_write(tmp_path: Path) -> None:
    _seed_eligible_memory(tmp_path)
    with (
        patch("argus.portfolio.autonomous_runner.run_operator_narrative") as m_nar,
        patch("argus.portfolio.autonomous_runner.run_operator_summary") as m_sum,
        patch("argus.portfolio.autonomous_runner.run_portfolio_lifecycle") as m_life,
        patch("argus.portfolio.autonomous_runner.run_portfolio_cycle") as m_cyc,
        patch("argus.portfolio.autonomous_runner.run_portfolio_refresh") as m_ref,
    ):
        m_ref.side_effect = lambda *a, **k: _minimal_refresh()
        m_cyc.side_effect = [
            _minimal_cycle_payload(overall="inspect_specific_products", run_id="a"),
            _minimal_cycle_payload(overall="run_again", run_id="b"),
        ]
        m_life.return_value = _minimal_lifecycle()
        m_sum.return_value = _minimal_summary()
        m_nar.return_value = _minimal_narrative()
        pl = run_portfolio_autonomous_session(
            tmp_path,
            max_cycles=1,
            write_session_artifacts=False,
            dry_run=True,
        )
    from argus.portfolio.autonomous_runner import render_portfolio_autonomous_session_markdown

    md = render_portfolio_autonomous_session_markdown(pl)
    assert "Confidence adjustment" in md
    assert "autonomy memory" in md.lower()
