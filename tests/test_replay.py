"""Tests for read-only orchestration and portfolio replay."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from argus.orchestrator.replay import (
    ORCHESTRATION_REPLAY_SCHEMA,
    replay_orchestration_product,
    run_orchestration_replay,
)
from argus.portfolio.cycle import PORTFOLIO_CYCLE_SCHEMA
from argus.portfolio.operator_queue import OPERATOR_QUEUE_SCHEMA
from argus.portfolio.replay import PORTFOLIO_REPLAY_SCHEMA, replay_portfolio, run_portfolio_replay

ORCH_STATE_SCHEMA = "argus.orchestration_state.v1"
SNAP_SCHEMA = "argus.operator_snapshot.v1"


def _write(p: Path, payload: dict) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload), encoding="utf-8")


class TestOrchestrationReplay(unittest.TestCase):
    def test_full_artifacts(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p1"
            (root / "products" / pid).mkdir(parents=True)
            (root / "products" / pid / "product.yaml").write_text(f"id: {pid}\nname: X\n", encoding="utf-8")
            _write(
                root / "runs" / "orchestration" / "latest" / f"{pid}.json",
                {
                    "schema": ORCH_STATE_SCHEMA,
                    "product_id": pid,
                    "orchestration_status": "eligible",
                    "next_action": "signals_collect",
                    "orchestration_status_reason_codes": ["orch.test"],
                    "readiness": {"readiness_tier": "observe_gap", "understanding_debt": 0.3},
                    "import_health": {"first_pass_status": "success", "gating_tier": "success"},
                    "blockers": [],
                },
            )
            _write(
                root / "runs" / "orchestration" / "operator_snapshot" / f"{pid}.json",
                {
                    "schema": SNAP_SCHEMA,
                    "product_id": pid,
                    "next_action": "signals_collect",
                    "readiness": {"readiness_tier": "observe_gap"},
                },
            )
            _write(root / "runs" / "signals" / "latest" / f"{pid}.json", {"schema": "x", "records": []})
            pl = replay_orchestration_product(root, pid)
            self.assertEqual(pl["schema"], ORCHESTRATION_REPLAY_SCHEMA)
            self.assertEqual(pl["completeness"], "full")
            self.assertIn("orch.test", pl["reason_codes_surfaced"])

    def test_partial_artifacts(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p2"
            _write(
                root / "runs" / "orchestration" / "latest" / f"{pid}.json",
                {
                    "schema": ORCH_STATE_SCHEMA,
                    "product_id": pid,
                    "orchestration_status": "eligible",
                    "next_action": "none",
                    "readiness": {},
                },
            )
            pl = replay_orchestration_product(root, pid)
            self.assertEqual(pl["completeness"], "partial")

    def test_legacy_missing_newer_artifacts(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "legacy"
            _write(
                root / "runs" / "orchestration" / "operator_snapshot" / f"{pid}.json",
                {
                    "schema": SNAP_SCHEMA,
                    "product_id": pid,
                    "next_action": "audit_run",
                    "readiness": {"readiness_tier": "observe_gap"},
                },
            )
            pl = replay_orchestration_product(root, pid)
            self.assertEqual(pl["completeness"], "partial")

    def test_no_usable_history(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pl = replay_orchestration_product(root, "missing")
            self.assertEqual(pl["completeness"], "none")

    def test_no_save(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "x"
            _write(
                root / "runs" / "orchestration" / "latest" / f"{pid}.json",
                {"schema": ORCH_STATE_SCHEMA, "product_id": pid, "orchestration_status": "eligible", "next_action": "none", "readiness": {}},
            )
            run_orchestration_replay(root, pid, write_artifacts=False)
            self.assertFalse((root / "runs" / "orchestration" / "replay" / pid).exists())


class TestPortfolioReplay(unittest.TestCase):
    def test_full_cycle_bundle(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(
                root / "runs" / "portfolio" / "cycle" / "latest.json",
                {
                    "schema": PORTFOLIO_CYCLE_SCHEMA,
                    "run_id": "20260101T000000Z",
                    "ok": True,
                    "generated_at_utc": "2026-01-01T00:00:00+00:00",
                    "summary": {
                        "overall_operator_recommendation": "wait",
                        "overall_rationale_codes": ["cycle.x"],
                    },
                    "stages": {
                        "operator_queue": {"status": "ok"},
                        "portfolio_progression": {"status": "ok"},
                    },
                },
            )
            _write(
                root / "runs" / "portfolio" / "operator_queue" / "latest.json",
                {
                    "schema": OPERATOR_QUEUE_SCHEMA,
                    "generated_at_utc": "2026-01-01T00:00:00+00:00",
                    "entries": [{"product_id": "a", "queue_rank": 1}],
                },
            )
            _write(root / "runs" / "orchestration" / "operator_snapshot" / "a.json", {"schema": SNAP_SCHEMA, "product_id": "a"})
            pl = replay_portfolio(root)
            self.assertEqual(pl["schema"], PORTFOLIO_REPLAY_SCHEMA)
            self.assertEqual(pl["completeness"], "full")

    def test_partial_without_cycle(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(
                root / "runs" / "portfolio" / "progression" / "latest.json",
                {
                    "schema": "argus.portfolio_progression.v1",
                    "run_id": "r",
                    "summary_counts": {"advanced": 1},
                },
            )
            pl = replay_portfolio(root)
            self.assertEqual(pl["completeness"], "partial")

    def test_cycle_with_failures(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(
                root / "runs" / "portfolio" / "cycle" / "latest.json",
                {
                    "schema": PORTFOLIO_CYCLE_SCHEMA,
                    "ok": False,
                    "stages": {
                        "portfolio_progression": {"status": "error", "error": "boom"},
                    },
                },
            )
            pl = replay_portfolio(root)
            self.assertIn("portfolio_progression", pl["failure_stages"])

    def test_portfolio_no_save(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pl = run_portfolio_replay(root, write_artifacts=False)
            self.assertEqual(pl["schema"], PORTFOLIO_REPLAY_SCHEMA)
            self.assertFalse((root / "runs" / "portfolio" / "replay").exists())


if __name__ == "__main__":
    unittest.main()
