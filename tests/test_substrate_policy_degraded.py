"""Degraded-substrate policy and soak summary (yellow-light, bounded)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from argus.debug.substrate_soak_summary import summarize_substrate_soak
from argus.portfolio.autonomous_runner import (
    portfolio_autonomous_runner_dir,
    run_portfolio_autonomous_session,
)
from argus.portfolio.substrate_policy_state import (
    substrate_policy_state_path,
    write_substrate_policy_state,
)
from tests.test_autonomous_runner import (
    _minimal_cycle_payload,
    _minimal_lifecycle,
    _minimal_narrative,
    _minimal_refresh,
    _minimal_summary,
)


def _degraded_ac() -> dict:
    return {"status": "ok", "overall_status": "degraded", "run_id": "coh_deg", "paths": {}}


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
def test_single_degraded_does_not_suppress_promotions(
    m_ref,
    m_cyc,
    m_life,
    m_sum,
    m_nar,
    m_write,
    repo: Path,
) -> None:
    m_ref.side_effect = lambda *a, **k: _minimal_refresh()
    m_cyc.return_value = _minimal_cycle_payload(artifact_coherence=_degraded_ac())
    m_life.return_value = _minimal_lifecycle()
    m_sum.return_value = _minimal_summary()
    m_nar.return_value = _minimal_narrative()

    pl = run_portfolio_autonomous_session(
        repo,
        max_cycles=1,
        write_session_artifacts=True,
        allow_promotion=True,
        degraded_suppress_promotions_after=2,
    )
    assert pl["cycles_run"] == 1
    dsb = (pl.get("artifact_coherence_policy") or {}).get("degraded_substrate_policy") or {}
    assert dsb.get("promotions_suppressed_for_degraded_substrate") is False
    assert dsb.get("consecutive_degraded_sessions_after_session") == 1
    pex = pl.get("promotion_execution") or {}
    assert pex.get("promotions_suppressed_for_degraded_substrate") is not True


@patch("argus.portfolio.autonomous_runner.collect_promotion_opportunities")
@patch("argus.portfolio.autonomous_runner.write_portfolio_autonomous_session_artifacts")
@patch("argus.portfolio.autonomous_runner.run_operator_narrative")
@patch("argus.portfolio.autonomous_runner.run_operator_summary")
@patch("argus.portfolio.autonomous_runner.run_portfolio_lifecycle")
@patch("argus.portfolio.autonomous_runner.run_portfolio_cycle")
@patch("argus.portfolio.autonomous_runner.run_portfolio_refresh")
def test_two_consecutive_degraded_suppresses_promotions_when_allowed(
    m_ref,
    m_cyc,
    m_life,
    m_sum,
    m_nar,
    m_write,
    m_collect,
    repo: Path,
) -> None:
    write_substrate_policy_state(
        repo,
        {
            "consecutive_degraded_sessions": 1,
            "last_session_id": "prior",
            "last_session_substrate_overall": "degraded",
            "thresholds": {
                "suppress_promotions_after_consecutive_degraded_sessions": 2,
                "abort_new_session_after_consecutive_degraded_sessions": 4,
            },
        },
    )
    m_ref.side_effect = lambda *a, **k: _minimal_refresh()
    m_cyc.return_value = _minimal_cycle_payload(artifact_coherence=_degraded_ac())
    m_life.return_value = _minimal_lifecycle()
    m_sum.return_value = _minimal_summary()
    m_nar.return_value = _minimal_narrative()
    m_collect.return_value = {
        "schema": "argus.promotion_opportunities.v1",
        "promotable_actions": [
            {
                "kind": "scaffolded_product_to_bootstrap",
                "product_id": "x",
                "safe_for_auto": True,
            }
        ],
        "blocked_promotions": [],
        "promotion_recommendations": [],
    }

    pl = run_portfolio_autonomous_session(
        repo,
        max_cycles=1,
        write_session_artifacts=True,
        allow_promotion=True,
        degraded_suppress_promotions_after=2,
    )
    dsb = (pl.get("artifact_coherence_policy") or {}).get("degraded_substrate_policy") or {}
    assert dsb.get("promotions_suppressed_for_degraded_substrate") is True
    pex = pl.get("promotion_execution") or {}
    assert pex.get("promotions_suppressed_for_degraded_substrate") is True
    assert "degraded" in str(pex.get("skipped_reason", "")).lower()


@patch("argus.portfolio.autonomous_runner.write_portfolio_autonomous_session_artifacts")
@patch("argus.portfolio.autonomous_runner.run_operator_narrative")
@patch("argus.portfolio.autonomous_runner.run_operator_summary")
@patch("argus.portfolio.autonomous_runner.run_portfolio_lifecycle")
@patch("argus.portfolio.autonomous_runner.run_portfolio_cycle")
@patch("argus.portfolio.autonomous_runner.run_portfolio_refresh")
def test_invalid_substrate_resets_degraded_streak(
    m_ref,
    m_cyc,
    m_life,
    m_sum,
    m_nar,
    m_write,
    repo: Path,
) -> None:
    write_substrate_policy_state(
        repo,
        {
            "consecutive_degraded_sessions": 3,
            "last_session_id": "prior",
            "last_session_substrate_overall": "degraded",
        },
    )
    m_ref.side_effect = lambda *a, **k: _minimal_refresh()
    m_cyc.return_value = _minimal_cycle_payload(
        artifact_coherence={
            "status": "ok",
            "overall_status": "invalid",
            "run_id": "bad",
            "paths": {},
        }
    )
    m_life.return_value = _minimal_lifecycle()
    m_sum.return_value = _minimal_summary()
    m_nar.return_value = _minimal_narrative()

    run_portfolio_autonomous_session(repo, max_cycles=3, write_session_artifacts=True)
    st_path = substrate_policy_state_path(repo)
    data = json.loads(st_path.read_text(encoding="utf-8"))
    assert data.get("consecutive_degraded_sessions") == 0


@patch("argus.portfolio.autonomous_runner.write_portfolio_autonomous_session_artifacts")
def test_abort_when_prior_streak_at_threshold(m_write, repo: Path) -> None:
    write_substrate_policy_state(
        repo,
        {
            "consecutive_degraded_sessions": 4,
            "last_session_id": "prior",
            "last_session_substrate_overall": "degraded",
        },
    )
    pl = run_portfolio_autonomous_session(
        repo,
        max_cycles=3,
        write_session_artifacts=True,
        degraded_abort_session_after=4,
    )
    assert pl["stop_reason"] == "artifact_coherence_degraded_persistent"
    assert pl["cycles_run"] == 0
    m_write.assert_called_once()


def test_summarize_substrate_soak_counts(repo: Path) -> None:
    d = portfolio_autonomous_runner_dir(repo)
    d.mkdir(parents=True, exist_ok=True)
    (d / "20260101T120000Z.json").write_text(
        json.dumps(
            {
                "schema": "argus.portfolio_autonomous_runner.v1",
                "session_id": "20260101T120000Z",
                "stop_reason": "max_cycles_reached",
                "per_cycle_outcomes": [
                    {
                        "artifact_coherence": {"overall_status": "degraded"},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    out = summarize_substrate_soak(repo, limit_sessions=5)
    assert out["schema"] == "argus.substrate_soak_summary.v1"
    assert out["substrate_status_counts_from_sessions"]["degraded"] == 1

