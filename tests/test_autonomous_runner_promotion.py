"""Autonomous runner promotion awareness (detection + bounded execution)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from argus.portfolio.autonomous_runner import (
    render_portfolio_autonomous_session_markdown,
    run_portfolio_autonomous_session,
)
from argus.portfolio.lifecycle import collect_promotion_opportunities
from argus.products.creation import (
    PRODUCT_CREATION_PROPOSALS_SCHEMA,
    creation_proposals_dir,
    evaluate_creation_proposals,
)
from argus.products.deprecation import PRODUCT_DEPRECATION_PROPOSALS_SCHEMA


def _write_mission(root: Path) -> None:
    cfg = root / "config" / "mission_profiles.yaml"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(
        """schema: argus.mission_registry.v1
default_mission_id: revenue
profiles:
  revenue:
    id: revenue
    primary_objective: Maximize sustainable revenue.
    drivers:
      - conversion and retention
    risk_posture: moderate
    weights:
      revenue_alignment: 1.0
""",
        encoding="utf-8",
    )


def _seed_creation_proposal(root: Path) -> str:
    _write_mission(root)
    cp = evaluate_creation_proposals(root)
    assert cp["schema"] == PRODUCT_CREATION_PROPOSALS_SCHEMA
    pid = str(cp["proposals"][0]["proposal_id"])
    d = creation_proposals_dir(root)
    d.mkdir(parents=True, exist_ok=True)
    (d / "latest.json").write_text(json.dumps(cp), encoding="utf-8")
    return pid


def _product(root: Path, pid: str, *, stage: str = "validate") -> None:
    pr = root / "products" / pid
    pr.mkdir(parents=True)
    (pr / "product.yaml").write_text(
        f"""
id: {pid}
name: PromoTest
owner:
  team: test
lifecycle:
  stage: {stage}
metrics:
  local_paths: [metrics/]
  primary: []
cost:
  monthly_usd: 1
  notes: ""
signals:
  - type: filesystem
    enabled: true
actions:
  start: "./scripts/start.sh"
  stop: "./scripts/stop.sh"
  analyze: "./scripts/analyze.sh"
constraints:
  max_monthly_cost_usd: 10
  min_activity_threshold: 0
""",
        encoding="utf-8",
    )
    (pr / "scripts").mkdir(parents=True)
    for n in ("start.sh", "stop.sh", "analyze.sh"):
        (pr / "scripts" / n).write_text("#!/bin/sh\necho\n", encoding="utf-8")
    (pr / "metrics").mkdir(parents=True)
    (pr / "metrics" / "d.txt").write_text("1", encoding="utf-8")


def _minimal_cycle_payload(**kwargs):
    from tests.test_autonomous_runner import _minimal_cycle_payload as m

    return m(**kwargs)


def _minimal_refresh():
    from tests.test_autonomous_runner import _minimal_refresh as m

    return m()


def _minimal_lifecycle():
    from tests.test_autonomous_runner import _minimal_lifecycle as m

    return m()


def _minimal_summary():
    from tests.test_autonomous_runner import _minimal_summary as m

    return m()


def _minimal_narrative():
    from tests.test_autonomous_runner import _minimal_narrative as m

    return m()


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
def test_detection_only_no_allow_promotion(
    m_ref, m_cyc, m_life, m_sum, m_nar, m_write, repo: Path
) -> None:
    m_ref.side_effect = lambda *a, **k: _minimal_refresh()
    m_cyc.return_value = _minimal_cycle_payload(quiescence_rec="wait")
    m_life.return_value = _minimal_lifecycle()
    m_sum.return_value = _minimal_summary()
    m_nar.return_value = _minimal_narrative()

    pl = run_portfolio_autonomous_session(repo, max_cycles=1, write_session_artifacts=True, allow_promotion=False)
    assert pl["promotion_execution"]["allow_promotion"] is False
    assert pl["promotion_execution"].get("skipped_reason")
    assert "Promotion scan" in (pl.get("session_summary") or "")
    assert isinstance(pl.get("promotable_actions"), list)


@patch("argus.portfolio.autonomous_runner.write_portfolio_autonomous_session_artifacts")
@patch("argus.portfolio.autonomous_runner.run_operator_narrative")
@patch("argus.portfolio.autonomous_runner.run_operator_summary")
@patch("argus.portfolio.autonomous_runner.run_portfolio_lifecycle")
@patch("argus.portfolio.autonomous_runner.run_portfolio_cycle")
@patch("argus.portfolio.autonomous_runner.run_portfolio_refresh")
def test_allow_promotion_dry_run_previews(
    m_ref, m_cyc, m_life, m_sum, m_nar, m_write, tmp_path: Path
) -> None:
    root = tmp_path
    (root / "runs").mkdir()
    m_ref.side_effect = lambda *a, **k: _minimal_refresh()
    m_cyc.return_value = _minimal_cycle_payload(quiescence_rec="wait")
    m_life.return_value = _minimal_lifecycle()
    m_sum.return_value = _minimal_summary()
    m_nar.return_value = _minimal_narrative()

    _write_mission(root)
    _seed_creation_proposal(root)
    d = root / "runs" / "products" / "deprecation"
    d.mkdir(parents=True)
    (d / "latest.json").write_text(
        json.dumps(
            {
                "schema": PRODUCT_DEPRECATION_PROPOSALS_SCHEMA,
                "evaluated_at_utc": "2020-01-01T00:00:00Z",
                "proposal_count": 1,
                "proposals": [
                    {
                        "proposal_id": "dep_auto_1",
                        "product_id": "x",
                        "deprecation_posture": "retire",
                        "rationale": "t",
                        "supporting_evidence": {"portfolio_outcomes": {"overall_trajectory": "negative"}},
                        "confidence": "medium",
                    }
                ],
                "inputs": {},
            }
        ),
        encoding="utf-8",
    )
    _product(root, "x", stage="grow")

    pl = run_portfolio_autonomous_session(
        root,
        max_cycles=1,
        write_session_artifacts=True,
        dry_run=True,
        allow_promotion=True,
        promotion_include_bootstrap=False,
    )
    assert pl["promotion_execution"]["effective_dry_run"] is True
    steps = pl["promotion_execution"].get("steps") or []
    assert len(steps) >= 1
    kinds = {s.get("kind") for s in steps if isinstance(s, dict)}
    assert "deprecation_proposal_to_plan" in kinds
    assert "creation_proposal_to_scaffold" in kinds


def test_collect_blocked_duplicate_creation(tmp_path: Path) -> None:
    root = tmp_path
    (root / "runs").mkdir()
    pr_id = _seed_creation_proposal(root)
    from argus.products.scaffold import normalize_product_slug

    cp = json.loads((creation_proposals_dir(root) / "latest.json").read_text(encoding="utf-8"))
    title = str(cp["proposals"][0].get("concept_title") or "x")
    slug = normalize_product_slug(title)
    (root / "products").mkdir(parents=True)
    (root / "products" / slug).mkdir()
    (root / "products" / slug / ".keep").write_text("", encoding="utf-8")

    out = collect_promotion_opportunities(root)
    kinds = {b.get("kind") for b in out["blocked_promotions"]}
    assert "creation_proposal_to_scaffold" in kinds
    assert not any(a.get("proposal_id") == pr_id and a.get("kind") == "creation_proposal_to_scaffold" for a in out["promotable_actions"])


def test_collect_deprecation_promotable(tmp_path: Path) -> None:
    root = tmp_path
    (root / "runs").mkdir()
    _product(root, "retp", stage="grow")
    d = root / "runs" / "products" / "deprecation"
    d.mkdir(parents=True)
    (d / "latest.json").write_text(
        json.dumps(
            {
                "schema": PRODUCT_DEPRECATION_PROPOSALS_SCHEMA,
                "evaluated_at_utc": "2020-01-01T00:00:00Z",
                "proposal_count": 1,
                "proposals": [
                    {
                        "proposal_id": "dep_surf_1",
                        "product_id": "retp",
                        "deprecation_posture": "harvest",
                        "rationale": "test",
                        "supporting_evidence": {},
                        "confidence": "high",
                    }
                ],
                "inputs": {},
            }
        ),
        encoding="utf-8",
    )
    out = collect_promotion_opportunities(root)
    dep = [a for a in out["promotable_actions"] if a.get("kind") == "deprecation_proposal_to_plan"]
    assert len(dep) == 1
    assert dep[0]["proposal_id"] == "dep_surf_1"
    assert any("dep_surf_1" in r for r in out["promotion_recommendations"])


def test_markdown_includes_promotion_sections() -> None:
    md = render_portfolio_autonomous_session_markdown(
        {
            "schema": "argus.portfolio_autonomous_runner.v1",
            "session_id": "s",
            "started_at_utc": "t",
            "finished_at_utc": "t",
            "cycles_run": 1,
            "stop_reason": "x",
            "stop_reason_codes": [],
            "artifacts_refreshed": [],
            "session_summary": "sum",
            "per_cycle_outcomes": [],
            "dashboard_refresh_status": {},
            "lifecycle_session_influence": {"primary_signal": "sig", "priority_hints": [], "session_notes": []},
            "inputs": {"allow_promotion": False},
            "promotable_actions": [{"kind": "deprecation_proposal_to_plan", "proposal_id": "p1", "product_id": "z", "safe_for_auto": True}],
            "promotion_recommendations": ["Do the thing"],
            "blocked_promotions": [{"kind": "creation_proposal_to_scaffold", "reason": "exists"}],
            "promotion_execution": {"allow_promotion": False, "skipped_reason": "x"},
        }
    )
    assert "Lifecycle promotions" in md
    assert "Promotion execution" in md
    assert "deprecation_proposal_to_plan" in md


@patch("argus.portfolio.autonomous_runner.write_portfolio_autonomous_session_artifacts")
@patch("argus.portfolio.autonomous_runner.run_operator_narrative")
@patch("argus.portfolio.autonomous_runner.run_operator_summary")
@patch("argus.portfolio.autonomous_runner.run_portfolio_lifecycle")
@patch("argus.portfolio.autonomous_runner.run_portfolio_cycle")
@patch("argus.portfolio.autonomous_runner.run_portfolio_refresh")
@patch("argus.portfolio.autonomous_runner.run_promote_deprecation")
def test_allow_promotion_non_dry_calls_deprecation_promotion(
    m_dep, m_ref, m_cyc, m_life, m_sum, m_nar, m_write, tmp_path: Path
) -> None:
    root = tmp_path
    (root / "runs").mkdir()
    m_ref.side_effect = lambda *a, **k: _minimal_refresh()
    m_cyc.return_value = _minimal_cycle_payload(quiescence_rec="wait")
    m_life.return_value = _minimal_lifecycle()
    m_sum.return_value = _minimal_summary()
    m_nar.return_value = _minimal_narrative()
    m_dep.return_value = {
        "schema": "argus.lifecycle_promotion_action.v1",
        "result_status": "success",
        "nested_results": {"deprecation_plan": {"ok": True}},
    }

    d = root / "runs" / "products" / "deprecation"
    d.mkdir(parents=True)
    (d / "latest.json").write_text(
        json.dumps(
            {
                "schema": PRODUCT_DEPRECATION_PROPOSALS_SCHEMA,
                "evaluated_at_utc": "2020-01-01T00:00:00Z",
                "proposal_count": 1,
                "proposals": [
                    {
                        "proposal_id": "dep_exec_1",
                        "product_id": "p1",
                        "deprecation_posture": "retire",
                        "rationale": "t",
                        "supporting_evidence": {},
                        "confidence": "high",
                    }
                ],
                "inputs": {},
            }
        ),
        encoding="utf-8",
    )
    _product(root, "p1", stage="grow")

    run_portfolio_autonomous_session(
        root,
        max_cycles=1,
        write_session_artifacts=True,
        dry_run=False,
        allow_promotion=True,
    )
    m_dep.assert_called_once()
    call_kw = m_dep.call_args.kwargs
    assert call_kw.get("dry_run") is False
    assert call_kw.get("proposal_id") == "dep_exec_1"


def test_session_summary_lists_promotion_scan(repo: Path) -> None:
    with patch("argus.portfolio.autonomous_runner.write_portfolio_autonomous_session_artifacts"):
        with patch("argus.portfolio.autonomous_runner.run_operator_narrative") as m_nar:
            with patch("argus.portfolio.autonomous_runner.run_operator_summary") as m_sum:
                with patch("argus.portfolio.autonomous_runner.run_portfolio_lifecycle") as m_life:
                    with patch("argus.portfolio.autonomous_runner.run_portfolio_cycle") as m_cyc:
                        with patch("argus.portfolio.autonomous_runner.run_portfolio_refresh") as m_ref:
                            m_ref.side_effect = lambda *a, **k: _minimal_refresh()
                            m_cyc.return_value = _minimal_cycle_payload(quiescence_rec="wait")
                            m_life.return_value = _minimal_lifecycle()
                            m_sum.return_value = _minimal_summary()
                            m_nar.return_value = _minimal_narrative()
                            pl = run_portfolio_autonomous_session(
                                repo, max_cycles=1, write_session_artifacts=False, allow_promotion=False
                            )
    assert "Promotion scan" in pl["session_summary"]
