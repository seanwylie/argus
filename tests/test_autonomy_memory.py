"""Tests for :mod:`argus.portfolio.autonomy_memory`."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from argus.portfolio.autonomous_runner import PORTFOLIO_AUTONOMOUS_RUNNER_SCHEMA
from argus.portfolio.autonomy_memory import (
    PORTFOLIO_AUTONOMY_MEMORY_SCHEMA,
    build_portfolio_autonomy_memory_payload,
    portfolio_autonomy_memory_dir,
    render_portfolio_autonomy_memory_markdown,
    write_portfolio_autonomy_memory_artifacts,
)
from argus.portfolio.escalation_inbox import ESCALATION_INBOX_SCHEMA
from argus.portfolio.runner_service import PORTFOLIO_RUNNER_SERVICE_SCHEMA


def _base_session(
    *,
    sid: str,
    finished: str,
    stop_reason: str,
    stop_codes: list[str],
    primary: str = "neutral",
    repair_pressure: list[str] | None = None,
    promotion_exec: dict | None = None,
    promotable_actions: list | None = None,
    blocked_promotions: list | None = None,
) -> dict:
    lsi = {
        "primary_signal": primary,
        "inputs_snapshot": {
            "products_under_repair_pressure": list(repair_pressure or []),
            "products_under_retirement_pressure": [],
            "products_entering": [],
            "products_exiting": [],
        },
    }
    pe = promotion_exec
    if pe is None:
        pe = {"allow_promotion": False, "skipped_reason": "allow_promotion is false (detection-only)", "steps": []}
    return {
        "schema": PORTFOLIO_AUTONOMOUS_RUNNER_SCHEMA,
        "session_id": sid,
        "started_at_utc": finished,
        "finished_at_utc": finished,
        "cycles_run": 1,
        "stop_reason": stop_reason,
        "stop_reason_codes": stop_codes,
        "lifecycle_session_influence": lsi,
        "promotable_actions": promotable_actions or [],
        "blocked_promotions": blocked_promotions or [],
        "promotion_execution": pe,
    }


class AutonomyMemoryTests(unittest.TestCase):
    def test_repeated_identical_stop_reasons(self) -> None:
        with TemporaryDirectory() as td:
            root = Path(td)
            ar = root / "runs" / "portfolio" / "autonomous_runner"
            ar.mkdir(parents=True)
            for i in range(3):
                sid = f"2026010{i + 1}T000000Z"
                pl = _base_session(
                    sid=sid,
                    finished=f"2026-01-0{i + 1}T12:00:00Z",
                    stop_reason="quiescence_recommendation",
                    stop_codes=["autonomous_runner.stop.quiescence.wait"],
                )
                (ar / f"{sid}.json").write_text(json.dumps(pl), encoding="utf-8")
            payload = build_portfolio_autonomy_memory_payload(root, limit_history=10)
            self.assertEqual(payload["schema"], PORTFOLIO_AUTONOMY_MEMORY_SCHEMA)
            rsr = payload["repeated_stop_reasons"]
            self.assertTrue(any(x["value"] == "quiescence_recommendation" and x["count"] >= 2 for x in rsr))
            self.assertIn("Repeated stop_reason", " ".join(payload["confidence_accumulation_notes"]))

    def test_repeated_mixed_sparse_lifecycle(self) -> None:
        with TemporaryDirectory() as td:
            root = Path(td)
            ar = root / "runs" / "portfolio" / "autonomous_runner"
            ar.mkdir(parents=True)
            for i in range(4):
                sid = f"2026020{i}T000000Z"
                pl = _base_session(
                    sid=sid,
                    finished=f"2026-02-0{i + 1}T12:00:00Z",
                    stop_reason="max_cycles_reached",
                    stop_codes=["autonomous_runner.stop.max_cycles=5"],
                    primary="mixed_sparse",
                )
                (ar / f"{sid}.json").write_text(json.dumps(pl), encoding="utf-8")
            payload = build_portfolio_autonomy_memory_payload(root, limit_history=10)
            self.assertGreaterEqual(
                payload["repeated_sparse_or_mixed_conditions"]["sessions_with_primary_mixed_sparse"], 2
            )
            self.assertTrue(any(x["value"] == "mixed_sparse" for x in payload["repeated_lifecycle_primary_signals"]))

    def test_sessions_no_promotions(self) -> None:
        with TemporaryDirectory() as td:
            root = Path(td)
            ar = root / "runs" / "portfolio" / "autonomous_runner"
            ar.mkdir(parents=True)
            promo_exec = {
                "allow_promotion": True,
                "steps": [
                    {
                        "kind": "creation_proposal_to_scaffold",
                        "attempted": True,
                        "result_status": "failed",
                        "detail": "x",
                    }
                ],
            }
            for i in range(4):
                sid = f"2026030{i}T000000Z"
                pl = _base_session(
                    sid=sid,
                    finished=f"2026-03-0{i + 1}T12:00:00Z",
                    stop_reason="max_cycles_reached",
                    stop_codes=["autonomous_runner.stop.max_cycles=5"],
                    primary="neutral",
                    promotion_exec=promo_exec,
                    promotable_actions=[{"kind": "creation_proposal_to_scaffold", "proposal_id": "p1"}],
                )
                (ar / f"{sid}.json").write_text(json.dumps(pl), encoding="utf-8")
            payload = build_portfolio_autonomy_memory_payload(root, limit_history=10)
            self.assertGreaterEqual(len(payload["recurring_no_promotion_sessions"]), 3)

    def test_recurring_safe_stoppage(self) -> None:
        with TemporaryDirectory() as td:
            root = Path(td)
            ar = root / "runs" / "portfolio" / "autonomous_runner"
            ar.mkdir(parents=True)
            for i in range(4):
                sid = f"2026040{i}T000000Z"
                pl = _base_session(
                    sid=sid,
                    finished=f"2026-04-0{i + 1}T12:00:00Z",
                    stop_reason="no_material_change_streak",
                    stop_codes=["autonomous_runner.stop.no_material_change_streak_n=3"],
                    primary="neutral",
                    repair_pressure=["alpha"],
                )
                (ar / f"{sid}.json").write_text(json.dumps(pl), encoding="utf-8")
            payload = build_portfolio_autonomy_memory_payload(root, limit_history=10)
            safe = payload["recurring_patterns"]["safe_caution_session_ids_in_window"]
            self.assertGreaterEqual(len(safe), 3)
            self.assertEqual(len(payload["recurring_patterns"]["unsafe_session_ids_in_window"]), 0)
            stalled = {x["product_id"] for x in payload["safe_but_stalled_products"]}
            self.assertIn("alpha", stalled)

    def test_unsafe_stops(self) -> None:
        with TemporaryDirectory() as td:
            root = Path(td)
            ar = root / "runs" / "portfolio" / "autonomous_runner"
            ar.mkdir(parents=True)
            for i in range(2):
                sid = f"2026050{i}T000000Z"
                pl = _base_session(
                    sid=sid,
                    finished=f"2026-05-0{i + 1}T12:00:00Z",
                    stop_reason="intervention_heavy_streak",
                    stop_codes=["autonomous_runner.stop.intervention"],
                    primary="repair_heavy",
                )
                (ar / f"{sid}.json").write_text(json.dumps(pl), encoding="utf-8")
            payload = build_portfolio_autonomy_memory_payload(root, limit_history=10)
            self.assertGreaterEqual(len(payload["recurring_patterns"]["unsafe_session_ids_in_window"]), 2)
            self.assertTrue(any("failure" in x.lower() for x in payload["caution_persistence_notes"]))

    def test_sparse_history_empty(self) -> None:
        with TemporaryDirectory() as td:
            root = Path(td)
            payload = build_portfolio_autonomy_memory_payload(root, limit_history=5)
            self.assertEqual(payload["session_window_summary"]["sessions_loaded"], 0)
            self.assertIn("No stamped autonomous", " ".join(payload["top_autonomy_lessons"]))

    def test_runner_and_escalation_provenance(self) -> None:
        with TemporaryDirectory() as td:
            root = Path(td)
            rs = root / "runs" / "portfolio" / "runner_service"
            rs.mkdir(parents=True)
            hb = {
                "schema": PORTFOLIO_RUNNER_SERVICE_SCHEMA,
                "service_run_id": "svc1",
                "loop_count": 2,
                "last_autonomous_stop_reason": "max_cycles_reached",
                "current_status": "stopped",
                "stop_reason": "completed",
            }
            (rs / "latest.json").write_text(json.dumps(hb), encoding="utf-8")
            esc = root / "runs" / "portfolio" / "escalation_inbox"
            esc.mkdir(parents=True)
            inbox = {
                "schema": ESCALATION_INBOX_SCHEMA,
                "open_items": [{"product_id": "p1", "source": "t", "kind": "k"}],
            }
            (esc / "latest.json").write_text(json.dumps(inbox), encoding="utf-8")
            payload = build_portfolio_autonomy_memory_payload(root, limit_history=5)
            self.assertEqual(payload["session_window_summary"]["runner_heartbeat_snapshots"], 1)
            self.assertEqual(payload["escalation_open_product_tally"].get("p1"), 1)

    def test_write_artifacts(self) -> None:
        with TemporaryDirectory() as td:
            root = Path(td)
            payload = build_portfolio_autonomy_memory_payload(root, limit_history=5)
            write_portfolio_autonomy_memory_artifacts(root, payload, run_id="testrid")
            d = portfolio_autonomy_memory_dir(root)
            self.assertTrue((d / "latest.json").is_file())
            md = render_portfolio_autonomy_memory_markdown(payload)
            self.assertIn("Portfolio autonomy memory", md)
            self.assertIn("What looks chronic", md)


if __name__ == "__main__":
    unittest.main()
