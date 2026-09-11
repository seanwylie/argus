"""Soft portfolio strategy influence (creation, queue, strategy payload)."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from argus.portfolio.strategy import PORTFOLIO_STRATEGY_SCHEMA
from argus.portfolio.strategy_influence import (
    describe_soft_influence,
    queue_priority_nudge,
)
from argus.products.creation import evaluate_creation_proposals


def _write_mission(root: Path) -> None:
    cfg = root / "config" / "mission_profiles.yaml"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(
        """schema: argus.mission_registry.v1
default_mission_id: revenue
profiles:
  revenue:
    id: revenue
    primary_objective: x
    drivers: []
    risk_posture: moderate
    weights: {revenue_alignment: 1.0}
  education:
    id: education
    primary_objective: y
    drivers: []
    risk_posture: conservative
    weights: {learning_quality: 1.0}
""",
        encoding="utf-8",
    )


def _write_strategy(root: Path, posture: str) -> None:
    d = root / "runs" / "portfolio" / "strategy"
    d.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": PORTFOLIO_STRATEGY_SCHEMA,
        "run_id": "test",
        "evaluated_at_utc": "2020-01-01T00:00:00+00:00",
        "strategic_posture": posture,
        "posture_scores": {p: 0.0 for p in ("repair", "retire", "consolidate", "create", "expand", "harvest")},
        "rationale": [],
        "dominant_mission_mix": {},
        "portfolio_pressures": [],
        "top_opportunities": [],
        "top_risks": [],
        "recommended_next_portfolio_moves": [],
        "inputs": {},
    }
    (d / "latest.json").write_text(json.dumps(payload), encoding="utf-8")


class TestStrategyInfluenceCreation(unittest.TestCase):
    def test_neutral_without_strategy_file(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "products").mkdir()
            _write_mission(root)
            payload = evaluate_creation_proposals(root)
            psi = payload.get("portfolio_strategy_influence") or {}
            self.assertEqual(psi.get("creation_appetite"), "neutral")
            self.assertFalse(payload["inputs"].get("strategic_posture_loaded"))

    def test_create_posture_elevates_emphasis(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "products").mkdir()
            _write_mission(root)
            _write_strategy(root, "create")
            payload = evaluate_creation_proposals(root)
            psi = payload.get("portfolio_strategy_influence") or {}
            self.assertEqual(psi.get("creation_appetite"), "elevated")
            self.assertGreater(float(psi.get("creation_emphasis_score") or 0), 0.7)
            self.assertTrue(payload["inputs"].get("strategic_posture_loaded"))
            for p in payload.get("proposals") or []:
                self.assertEqual((p.get("strategy_influence") or {}).get("surfacing"), "highlight")

    def test_repair_posture_suppresses_appetite(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "products").mkdir()
            _write_mission(root)
            _write_strategy(root, "repair")
            payload = evaluate_creation_proposals(root)
            psi = payload.get("portfolio_strategy_influence") or {}
            self.assertEqual(psi.get("creation_appetite"), "suppressed")
            self.assertLess(float(psi.get("creation_emphasis_score") or 1), 0.5)
            for p in payload.get("proposals") or []:
                self.assertEqual((p.get("strategy_influence") or {}).get("surfacing"), "muted")

    def test_consolidate_favors_non_expansion(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "products").mkdir()
            _write_mission(root)
            _write_strategy(root, "consolidate")
            payload = evaluate_creation_proposals(root)
            psi = payload.get("portfolio_strategy_influence") or {}
            self.assertEqual(psi.get("creation_appetite"), "restrained")


class TestQueueNudge(unittest.TestCase):
    def test_repair_boosts_blocked(self) -> None:
        nudge, meta = queue_priority_nudge(
            "repair",
            lifecycle_stage="grow",
            orchestration_status="blocked_waiting_input",
            readiness_tier="interpret_gap",
            recommendation="stabilize",
        )
        self.assertGreater(nudge, 0.0)
        self.assertIn("repair_blocked_or_waiting", meta.get("components") or {})

    def test_consolidate_negative_for_advance_ready(self) -> None:
        nudge, meta = queue_priority_nudge(
            "consolidate",
            lifecycle_stage="grow",
            orchestration_status="eligible",
            readiness_tier="advance_ready",
            recommendation="ship",
        )
        self.assertLess(nudge, 0.0)
        self.assertIn("consolidate_deemphasize_advance_only", meta.get("components") or {})


class TestDescribeSoftInfluence(unittest.TestCase):
    def test_describe_contains_hints(self) -> None:
        d = describe_soft_influence("create")
        self.assertEqual(d.get("schema"), "argus.portfolio_strategy_influence.v1")
        self.assertIn("downstream_hints", d)


if __name__ == "__main__":
    unittest.main()
