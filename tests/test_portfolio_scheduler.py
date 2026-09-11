"""Tests for :mod:`argus.portfolio.scheduler`."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from argus.portfolio.scheduler import (
    PORTFOLIO_SCHEDULER_SESSION_SCHEMA,
    portfolio_scheduler_dir,
    run_portfolio_scheduler_session,
    write_portfolio_scheduler_session_artifacts,
)


def _base_cycle(
    *,
    run_id: str,
    qrec: str = "run_again",
    overall: str = "run_again",
    mat: int = 1,
    flagged: list[dict[str, str]] | None = None,
) -> dict[str, object]:
    flagged = flagged or []
    return {
        "schema": "argus.portfolio_cycle.v1",
        "run_id": run_id,
        "ok": True,
        "stages": {
            "portfolio_quiescence": {
                "status": "ok",
                "products_with_material_change_count": mat,
            },
        },
        "summary": {
            "quiescence": {"recommendation": qrec, "portfolio_quiescent": qrec == "wait"},
            "intervention": {"flagged_products": flagged},
            "overall_operator_recommendation": overall,
            "progression": {"summary_counts": {"advanced": 1, "no_action": 0}},
        },
    }


class TestPortfolioScheduler(unittest.TestCase):
    def test_single_cycle_stops_on_max_cycles(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch(
                "argus.portfolio.scheduler.run_portfolio_cycle",
                side_effect=[_base_cycle(run_id="c1")],
            ) as m:
                pl = run_portfolio_scheduler_session(
                    root,
                    max_cycles=1,
                    write_session_artifacts=False,
                    write_stage_artifacts=False,
                )
            self.assertEqual(pl["schema"], PORTFOLIO_SCHEDULER_SESSION_SCHEMA)
            self.assertEqual(pl["cycles_run"], 1)
            self.assertEqual(pl["stop_reason"], "max_cycles_reached")
            self.assertEqual(m.call_count, 1)

    def test_multi_cycle_progression_then_wait(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            seq = [
                _base_cycle(run_id="c1", qrec="run_again", mat=1),
                _base_cycle(run_id="c2", qrec="wait", mat=0),
            ]
            with patch("argus.portfolio.scheduler.run_portfolio_cycle", side_effect=seq) as m:
                pl = run_portfolio_scheduler_session(
                    root,
                    max_cycles=5,
                    no_material_change_streak_limit=10,
                    write_session_artifacts=False,
                    write_stage_artifacts=False,
                )
            self.assertEqual(pl["cycles_run"], 2)
            self.assertEqual(pl["stop_reason"], "quiescence_recommendation")
            self.assertIn("scheduler.stop.quiescence.wait", pl["stop_reason_codes"])
            self.assertEqual(m.call_count, 2)

    def test_intervention_heavy_streak_stop(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            heavy = [{"intervention_category": "x"} for _ in range(5)]
            seq = [
                _base_cycle(run_id="a", flagged=heavy),
                _base_cycle(run_id="b", flagged=heavy),
            ]
            with patch("argus.portfolio.scheduler.run_portfolio_cycle", side_effect=seq):
                pl = run_portfolio_scheduler_session(
                    root,
                    max_cycles=10,
                    intervention_flagged_threshold=4,
                    intervention_heavy_streak=2,
                    write_session_artifacts=False,
                    write_stage_artifacts=False,
                )
            self.assertEqual(pl["stop_reason"], "intervention_heavy_streak")
            self.assertEqual(pl["cycles_run"], 2)

    def test_no_material_change_early_stop(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            seq = [
                _base_cycle(run_id="n1", qrec="run_again", mat=0),
                _base_cycle(run_id="n2", qrec="run_again", mat=0),
                _base_cycle(run_id="n3", qrec="run_again", mat=0),
            ]
            with patch("argus.portfolio.scheduler.run_portfolio_cycle", side_effect=seq):
                pl = run_portfolio_scheduler_session(
                    root,
                    max_cycles=10,
                    no_material_change_streak_limit=3,
                    write_session_artifacts=False,
                    write_stage_artifacts=False,
                )
            self.assertEqual(pl["stop_reason"], "no_material_change_streak")
            self.assertEqual(pl["cycles_run"], 3)

    def test_dry_run_passed_to_cycle(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch(
                "argus.portfolio.scheduler.run_portfolio_cycle",
                return_value=_base_cycle(run_id="d1"),
            ) as m:
                run_portfolio_scheduler_session(
                    root,
                    max_cycles=1,
                    dry_run=True,
                    write_session_artifacts=False,
                    write_stage_artifacts=False,
                )
            m.assert_called_once()
            kwargs = m.call_args.kwargs
            self.assertTrue(kwargs.get("dry_run"))

    def test_writes_artifacts(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch(
                "argus.portfolio.scheduler.run_portfolio_cycle",
                return_value=_base_cycle(run_id="w1"),
            ):
                run_portfolio_scheduler_session(
                    root,
                    max_cycles=1,
                    write_session_artifacts=True,
                    write_stage_artifacts=False,
                )
            d = portfolio_scheduler_dir(root)
            self.assertTrue((d / "latest.json").is_file())
            self.assertTrue((d / "latest.md").is_file())
            raw = json.loads((d / "latest.json").read_text(encoding="utf-8"))
            self.assertEqual(raw.get("schema"), PORTFOLIO_SCHEDULER_SESSION_SCHEMA)

    def test_cycle_overall_stops_before_quiescence_run_again(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch(
                "argus.portfolio.scheduler.run_portfolio_cycle",
                return_value=_base_cycle(
                    run_id="hr1",
                    qrec="run_again",
                    overall="request_human_review",
                ),
            ):
                pl = run_portfolio_scheduler_session(
                    root,
                    max_cycles=5,
                    write_session_artifacts=False,
                    write_stage_artifacts=False,
                )
            self.assertEqual(pl["stop_reason"], "cycle_overall_recommendation")
            self.assertIn("scheduler.stop.cycle_overall.request_human_review", pl["stop_reason_codes"])

    def test_stop_sentinel_skips_further_cycles(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            d = portfolio_scheduler_dir(root)
            d.mkdir(parents=True)
            seq = [_base_cycle(run_id="s1"), _base_cycle(run_id="s2")]
            with patch("argus.portfolio.scheduler.run_portfolio_cycle", side_effect=seq) as m:
                (d / "STOP").write_text("", encoding="utf-8")
                pl = run_portfolio_scheduler_session(
                    root,
                    max_cycles=5,
                    write_session_artifacts=False,
                    write_stage_artifacts=False,
                )
            self.assertEqual(pl["cycles_run"], 1)
            self.assertEqual(pl["stop_reason"], "explicit_stop_sentinel")
            self.assertEqual(m.call_count, 1)

    def test_no_save_skips_scheduler_files(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch(
                "argus.portfolio.scheduler.run_portfolio_cycle",
                return_value=_base_cycle(run_id="ns1"),
            ):
                run_portfolio_scheduler_session(
                    root,
                    max_cycles=1,
                    write_session_artifacts=False,
                    write_stage_artifacts=False,
                )
            self.assertFalse(portfolio_scheduler_dir(root).exists())

    def test_write_session_artifacts_helper(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pl = {
                "schema": PORTFOLIO_SCHEDULER_SESSION_SCHEMA,
                "run_id": "sess1",
                "session_started_utc": "t",
                "cycles_run": 0,
                "cycle_run_ids": [],
                "stop_reason": "max_cycles_reached",
                "stop_reason_codes": [],
                "per_cycle": [],
                "session_summary": {},
                "inputs": {},
                "guardrails_note": "",
            }
            write_portfolio_scheduler_session_artifacts(root, pl)
            d = portfolio_scheduler_dir(root)
            self.assertTrue((d / "sess1.json").is_file())
