"""Tests for :mod:`argus.portfolio.history` and :mod:`argus.portfolio.artifact_index`."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from argus.portfolio.artifact_index import list_timestamped_portfolio_json_files
from argus.portfolio.cycle import PORTFOLIO_CYCLE_SCHEMA
from argus.portfolio.delta_report import PORTFOLIO_DELTA_REPORT_SCHEMA
from argus.portfolio.history import (
    PORTFOLIO_HISTORY_SCHEMA,
    align_artifacts_by_cycle,
    artifact_counts,
    evaluate_portfolio_history,
    latest_stamp_per_type,
    load_latest_n_artifacts,
    run_portfolio_history,
)
from argus.portfolio.intervention import PORTFOLIO_INTERVENTION_SCHEMA
from argus.portfolio.progression import PORTFOLIO_PROGRESSION_SCHEMA
from argus.portfolio.quiescence import PORTFOLIO_QUIESCENCE_SCHEMA


def _write(
    root: Path,
    rel: str,
    name: str,
    payload: dict,
) -> Path:
    d = root / "runs" / "portfolio" / rel
    d.mkdir(parents=True, exist_ok=True)
    p = d / name
    p.write_text(json.dumps(payload), encoding="utf-8")
    return p


def _delta_payload(run_id: str, pid: str, *, rank: int, tier: str, debt: float) -> dict:
    return {
        "schema": PORTFOLIO_DELTA_REPORT_SCHEMA,
        "run_id": run_id,
        "evaluated_at_utc": f"{run_id[:4]}-01-01T00:00:00+00:00",
        "baseline_for_next_run": {
            "evaluated_at_utc": run_id,
            "per_product": {
                pid: {
                    "queue_rank": rank,
                    "priority_score": 50.0,
                    "readiness_tier": tier,
                    "next_action": "signals_collect",
                    "understanding_debt": debt,
                }
            },
            "queue_product_order": [pid],
        },
    }


def _prog_payload(run_id: str, pid: str, outcome: str) -> dict:
    return {
        "schema": PORTFOLIO_PROGRESSION_SCHEMA,
        "run_id": run_id,
        "generated_at_utc": run_id,
        "products": [
            {
                "product_id": pid,
                "outcome": outcome,
            }
        ],
        "summary_counts": {outcome: 1},
    }


class TestPortfolioHistory(unittest.TestCase):
    def test_sparse_history_empty_counts(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pl = evaluate_portfolio_history(root, limit_history=10)
            self.assertEqual(pl.get("schema"), PORTFOLIO_HISTORY_SCHEMA)
            self.assertEqual(pl["artifact_counts"]["progression"], 0)
            self.assertFalse(pl["latest_timestamps"]["operator_queue"])

    def test_mixed_artifact_availability(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root, "progression", "20260101T000001Z.json", _prog_payload("20260101T000001Z", "a", "advanced"))
            _write(root, "intervention", "20260101T000002Z.json", {"schema": PORTFOLIO_INTERVENTION_SCHEMA, "flagged_products": []})
            c = artifact_counts(root)
            self.assertEqual(c["progression"], 1)
            self.assertEqual(c["intervention"], 1)
            self.assertEqual(c["delta_report"], 0)

    def test_list_timestamped_newest_first(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            d = root / "runs" / "portfolio" / "progression"
            d.mkdir(parents=True)
            (d / "20260101T000001Z.json").write_text("{}", encoding="utf-8")
            (d / "20260201T000001Z.json").write_text("{}", encoding="utf-8")
            files = list_timestamped_portfolio_json_files(d)
            self.assertEqual(files[0].stem, "20260201T000001Z")

    def test_trend_improving_vs_degrading(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root, "delta_report", "20260101T000000Z.json", _delta_payload("20260101T000000Z", "p", rank=5, tier="observe_gap", debt=0.5))
            _write(
                root,
                "delta_report",
                "20260201T000000Z.json",
                _delta_payload("20260201T000000Z", "p", rank=2, tier="interpret_gap", debt=0.2),
            )
            pl = evaluate_portfolio_history(root, limit_history=20)
            ts = pl["trend_summaries"]
            pids_rising = {x["product_id"] for x in ts["rising_priority"]}
            pids_imp = {x["product_id"] for x in ts["steadily_improving"]}
            self.assertIn("p", pids_rising)
            self.assertIn("p", pids_imp)

    def test_trend_degrading(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root, "delta_report", "20260101T000000Z.json", _delta_payload("20260101T000000Z", "q", rank=2, tier="interpret_gap", debt=0.2))
            _write(
                root,
                "delta_report",
                "20260201T000000Z.json",
                _delta_payload("20260201T000000Z", "q", rank=6, tier="observe_gap", debt=0.55),
            )
            pl = evaluate_portfolio_history(root, limit_history=20)
            ts = pl["trend_summaries"]
            pids_cool = {x["product_id"] for x in ts["cooling_down"]}
            self.assertIn("q", pids_cool)

    def test_chronic_blocked_progression(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            for i, rid in enumerate(["2026010{}T000000Z".format(i) for i in range(1, 5)]):
                _write(root, "progression", f"{rid}.json", _prog_payload(rid, "c", "blocked_waiting"))
            pl = evaluate_portfolio_history(root, limit_history=20)
            blocked = {x["product_id"] for x in pl["trend_summaries"]["chronically_blocked"]}
            self.assertIn("c", blocked)

    def test_multi_product_cycles(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            for pid, r in (("x", 1), ("y", 2)):
                _write(
                    root,
                    "delta_report",
                    f"2026010{r}T000000Z.json",
                    _delta_payload(f"2026010{r}T000000Z", pid, rank=r, tier="observe_gap", debt=0.3),
                )
            pl = evaluate_portfolio_history(root, limit_history=10)
            self.assertIn("x", pl["per_product_trends"])
            self.assertIn("y", pl["per_product_trends"])

    def test_alignment_quiescence_infer(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            cycle_payload = {
                "schema": PORTFOLIO_CYCLE_SCHEMA,
                "run_id": "20260315T120000Z",
                "generated_at_utc": "2026-03-15T12:00:00+00:00",
                "summary": {
                    "progression": {"run_id": "20260315T115959Z"},
                    "material_deltas": {"run_id": "20260315T115958Z"},
                    "intervention": {"run_id": "20260315T120001Z"},
                },
            }
            _write(root, "cycle", "20260315T120000Z.json", cycle_payload)
            _write(
                root,
                "quiescence",
                "20260315T115957Z.json",
                {"schema": PORTFOLIO_QUIESCENCE_SCHEMA, "baseline_for_next_run": {"per_product": {}, "queue_product_order": []}},
            )
            aligned, notes = align_artifacts_by_cycle(root, limit_cycles=5)
            self.assertEqual(len(aligned), 1)
            self.assertEqual(aligned[0]["linked_quiescence_run_id_inferred"], "20260315T115957Z")

    def test_alignment_mismatched_stamp(self) -> None:
        """Quiescence stamp after cycle: infer None for <= cycle."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(
                root,
                "cycle",
                "20260315T120000Z.json",
                {
                    "schema": PORTFOLIO_CYCLE_SCHEMA,
                    "summary": {"progression": {}, "material_deltas": {}, "intervention": {}},
                },
            )
            _write(
                root,
                "quiescence",
                "20260315T120001Z.json",
                {"schema": PORTFOLIO_QUIESCENCE_SCHEMA, "baseline_for_next_run": {"per_product": {}, "queue_product_order": []}},
            )
            aligned, _ = align_artifacts_by_cycle(root, limit_cycles=5)
            self.assertIsNone(aligned[0]["linked_quiescence_run_id_inferred"])

    def test_load_latest_n_operator_queue(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            d = root / "runs" / "portfolio" / "operator_queue"
            d.mkdir(parents=True)
            (d / "latest.json").write_text(
                json.dumps(
                    {
                        "schema": "argus.operator_queue.v1",
                        "generated_at_utc": "2026-01-01T00:00:00+00:00",
                        "entries": [],
                    }
                ),
                encoding="utf-8",
            )
            rows = load_latest_n_artifacts(root, "operator_queue", limit=5)
            self.assertEqual(len(rows), 1)

    def test_run_no_save(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_portfolio_history(root, limit_history=5, write_artifacts=False)
            self.assertFalse((root / "runs" / "portfolio" / "history").exists())

    def test_latest_stamp_per_type(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root, "progression", "20260102T000000Z.json", _prog_payload("20260102T000000Z", "a", "advanced"))
            st = latest_stamp_per_type(root)
            self.assertEqual(st["progression"], "20260102T000000Z")


if __name__ == "__main__":
    unittest.main()
