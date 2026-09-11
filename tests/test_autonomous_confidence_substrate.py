"""Substrate snapshot visibility and invalid-substrate gating for confidence adjustment."""

from __future__ import annotations

import json
from pathlib import Path

from argus.portfolio.artifact_coherence import ARTIFACT_COHERENCE_REPORT_SCHEMA
from argus.portfolio.autonomous_runner import (
    PORTFOLIO_AUTONOMOUS_RUNNER_SCHEMA,
    run_portfolio_autonomous_session,
)
from argus.portfolio.autonomy_memory import evaluate_autonomous_confidence_adjustment
from tests.test_autonomous_runner import (
    _minimal_cycle_payload,
    _minimal_lifecycle,
    _minimal_narrative,
    _minimal_refresh,
    _minimal_summary,
)


def _seed_eligible_memory(root: Path) -> None:
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


def test_durable_invalid_substrate_blocks_confidence_deferral(tmp_path: Path) -> None:
    _seed_eligible_memory(tmp_path)
    coh = tmp_path / "runs" / "debug" / "artifact_coherence"
    coh.mkdir(parents=True)
    (coh / "latest.json").write_text(
        json.dumps(
            {
                "schema": ARTIFACT_COHERENCE_REPORT_SCHEMA,
                "overall_status": "invalid",
                "run_id": "coh_inv",
                "evaluated_at_utc": "2026-01-01T00:00:00Z",
                "checks": {},
            }
        ),
        encoding="utf-8",
    )
    adj = evaluate_autonomous_confidence_adjustment(tmp_path, limit_history=30)
    assert adj["block_reason"] == "substrate_coherence_invalid"
    assert adj["deferral_available"] is False
    assert adj["eligible"] is False
    ctx = adj.get("substrate_coherence_context") or {}
    assert ctx.get("overall_status") == "invalid"


def test_evaluations_per_iteration_include_substrate_context(tmp_path: Path) -> None:
    """Post-pipeline confidence snapshot includes substrate_coherence_context (inspectable)."""
    # No stamped session history → ineligible → single iteration (max_cycles=1, no +1 budget).
    coh = tmp_path / "runs" / "debug" / "artifact_coherence"
    coh.mkdir(parents=True)
    (coh / "latest.json").write_text(
        json.dumps(
            {
                "schema": ARTIFACT_COHERENCE_REPORT_SCHEMA,
                "overall_status": "valid",
                "run_id": "coh_ok",
                "evaluated_at_utc": "2026-01-01T00:00:00Z",
                "checks": {},
            }
        ),
        encoding="utf-8",
    )

    from unittest.mock import patch

    with (
        patch("argus.portfolio.autonomous_runner.write_portfolio_autonomous_session_artifacts"),
        patch("argus.portfolio.autonomous_runner.run_operator_narrative") as m_nar,
        patch("argus.portfolio.autonomous_runner.run_operator_summary") as m_sum,
        patch("argus.portfolio.autonomous_runner.run_portfolio_lifecycle") as m_life,
        patch("argus.portfolio.autonomous_runner.run_portfolio_cycle") as m_cyc,
        patch("argus.portfolio.autonomous_runner.run_portfolio_refresh") as m_ref,
    ):
        m_ref.side_effect = lambda *a, **k: _minimal_refresh()
        m_cyc.return_value = _minimal_cycle_payload(overall="run_again", run_id="a")
        m_life.return_value = _minimal_lifecycle()
        m_sum.return_value = _minimal_summary()
        m_nar.return_value = _minimal_narrative()
        pl = run_portfolio_autonomous_session(
            tmp_path,
            max_cycles=1,
            limit_history=30,
            write_session_artifacts=True,
        )
    ev = pl.get("confidence_adjustment", {}).get("evaluations_per_iteration") or []
    assert len(ev) == 1
    row = ev[0]
    assert "substrate_coherence_context" in row
    assert row["substrate_coherence_context"].get("overall_status") == "valid"
