"""Cross-product batch: one advance on highest-priority product (deterministic)."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

from argus.orchestrator.advancement import (
    FAIRNESS_RULE_BATCH_ADVANCEMENT_V1,
    run_orchestration_batch_advance,
)
from argus.orchestrator.state_models import (
    ORCHESTRATION_BATCH_ADVANCEMENT_SCHEMA,
    ORCHESTRATION_OPERATOR_SUMMARY_SCHEMA,
)
from argus.orchestrator.state_pass import orchestration_operator_summary_path


def _entry(pid: str, has_elig: bool) -> dict:
    return {
        "product_id": pid,
        "priority_rank": 0,
        "priority_tuple": [0, 0, 0, 1 if has_elig else 0, 0],
        "priority_labels": {"has_eligible_actions": has_elig},
    }


class TestBatchAdvancement(unittest.TestCase):
    @patch("argus.orchestrator.advancement.advance_orchestration")
    @patch("argus.orchestrator.advancement.emit_orchestration_batch")
    def test_selects_first_ranked_calls_advance_once(
        self, mock_emit: MagicMock, mock_adv: MagicMock
    ) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            mock_emit.return_value = (
                {
                    "cross_product_prioritization": {
                        "ranked_product_ids": ["winner", "other"],
                        "entries": [],
                    },
                },
                None,
            )
            adv_path = root / "runs" / "orchestration" / "latest" / "advancements" / "winner.json"
            mock_adv.return_value = (
                adv_path,
                {"action_status": "skipped", "selected_action": None},
            )
            out, body = run_orchestration_batch_advance(
                root,
                ["other", "winner"],
                execute=False,
                write_state_and_index=True,
            )
            self.assertEqual(mock_adv.call_count, 1)
            self.assertEqual(mock_adv.call_args[0][1], "winner")
            self.assertFalse(mock_adv.call_args[1]["execute"])
            self.assertEqual(body.get("selected_product_id"), "winner")
            self.assertEqual(body.get("schema"), ORCHESTRATION_BATCH_ADVANCEMENT_SCHEMA)
            self.assertIn("advancement_payload", body)
            self.assertEqual(body["advancement_payload"]["action_status"], "skipped")
            self.assertTrue(out.is_file())
            raw = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(raw["selected_product_id"], "winner")
            self.assertEqual(raw.get("batch_advancement_fairness", {}).get("rule"), FAIRNESS_RULE_BATCH_ADVANCEMENT_V1)
            sum_p = orchestration_operator_summary_path(root)
            self.assertTrue(sum_p.is_file())
            osum = json.loads(sum_p.read_text(encoding="utf-8"))
            self.assertEqual(osum.get("schema"), ORCHESTRATION_OPERATOR_SUMMARY_SCHEMA)
            self.assertEqual(osum.get("selected_product_id"), "winner")
            self.assertFalse(osum.get("fairness_applied"))
            self.assertEqual(body.get("recent_selected_product_ids"), ["winner"])
            self.assertEqual(
                (osum.get("artifact_links") or {}).get("batch_advancement_repo_relative"),
                "runs/orchestration/latest/batch_advancement.json",
            )

    @patch("argus.orchestrator.advancement.advance_orchestration")
    @patch("argus.orchestrator.advancement.emit_orchestration_batch")
    def test_execute_passed_through_default_true(
        self, mock_emit: MagicMock, mock_adv: MagicMock
    ) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            mock_emit.return_value = (
                {"cross_product_prioritization": {"ranked_product_ids": ["a"], "entries": []}},
                None,
            )
            mock_adv.return_value = (Path("/x/a.json"), {})
            run_orchestration_batch_advance(root, ["a"])
            self.assertTrue(mock_adv.call_args[1]["execute"])

    @patch("argus.orchestrator.advancement.advance_orchestration")
    @patch("argus.orchestrator.advancement.emit_orchestration_batch")
    def test_tiebreak_first_in_ranked_list(
        self, mock_emit: MagicMock, mock_adv: MagicMock
    ) -> None:
        """Same priority tuple → build_cross_product_prioritization orders by product_id; first wins."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            mock_emit.return_value = (
                {
                    "cross_product_prioritization": {
                        "ranked_product_ids": ["m", "z"],
                        "entries": [],
                    },
                },
                None,
            )
            mock_adv.return_value = (root / "a.json", {})
            _, body = run_orchestration_batch_advance(root, ["z", "m"], execute=False)
            self.assertEqual(body["selected_product_id"], "m")
            mock_emit.assert_called_once_with(root.resolve(), ["m", "z"], write=True)

    @patch("argus.orchestrator.advancement.advance_orchestration")
    @patch("argus.orchestrator.advancement.emit_orchestration_batch")
    def test_no_ranked_skips_advance(
        self, mock_emit: MagicMock, mock_adv: MagicMock
    ) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            mock_emit.return_value = (
                {"cross_product_prioritization": {"ranked_product_ids": [], "entries": []}},
                None,
            )
            out, body = run_orchestration_batch_advance(root, ["x"], execute=False)
            self.assertEqual(mock_adv.call_count, 0)
            self.assertIsNone(body.get("selected_product_id"))
            self.assertEqual(body.get("reason"), "no_ranked_products")
            self.assertTrue(out.is_file())
            sum_p = orchestration_operator_summary_path(root)
            osum = json.loads(sum_p.read_text(encoding="utf-8"))
            self.assertEqual(osum.get("batch_reason"), "no_ranked_products")
            self.assertIsNone(osum.get("selected_product_id"))
            self.assertFalse(osum.get("fairness_applied"))
            self.assertEqual(body.get("recent_selected_product_ids"), [])

    @patch("argus.orchestrator.advancement.advance_orchestration")
    @patch("argus.orchestrator.advancement.emit_orchestration_batch")
    def test_no_ranked_preserves_prior_recent_history(
        self, mock_emit: MagicMock, mock_adv: MagicMock
    ) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            batch_path = root / "runs" / "orchestration" / "latest" / "batch_advancement.json"
            batch_path.parent.mkdir(parents=True, exist_ok=True)
            batch_path.write_text(
                json.dumps(
                    {
                        "schema": ORCHESTRATION_BATCH_ADVANCEMENT_SCHEMA,
                        "selected_product_id": "x",
                        "recent_selected_product_ids": ["a", "b"],
                    }
                ),
                encoding="utf-8",
            )
            mock_emit.return_value = (
                {"cross_product_prioritization": {"ranked_product_ids": [], "entries": []}},
                None,
            )
            _, body = run_orchestration_batch_advance(root, ["x"], execute=False)
            self.assertEqual(body.get("recent_selected_product_ids"), ["a", "b"])

    @patch("argus.orchestrator.advancement.advance_orchestration")
    @patch("argus.orchestrator.advancement.emit_orchestration_batch")
    def test_fairness_rotates_when_top_repeats_and_other_actionable(
        self, mock_emit: MagicMock, mock_adv: MagicMock
    ) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            batch_path = root / "runs" / "orchestration" / "latest" / "batch_advancement.json"
            batch_path.parent.mkdir(parents=True, exist_ok=True)
            batch_path.write_text(
                json.dumps({"schema": ORCHESTRATION_BATCH_ADVANCEMENT_SCHEMA, "selected_product_id": "winner"}),
                encoding="utf-8",
            )
            mock_emit.return_value = (
                {
                    "cross_product_prioritization": {
                        "ranked_product_ids": ["winner", "other"],
                        "entries": [_entry("winner", True), _entry("other", True)],
                    },
                },
                None,
            )
            mock_adv.return_value = (
                root / "runs" / "orchestration" / "latest" / "advancements" / "other.json",
                {"action_status": "skipped"},
            )
            _, body = run_orchestration_batch_advance(root, ["other", "winner"], execute=False)
            self.assertEqual(mock_adv.call_args[0][1], "other")
            ff = body.get("batch_advancement_fairness") or {}
            self.assertTrue(ff.get("skipped_repeat_top_for_fairness"))
            self.assertEqual(ff.get("would_have_selected_without_fairness"), "winner")
            osum = json.loads(orchestration_operator_summary_path(root).read_text(encoding="utf-8"))
            self.assertTrue(osum.get("fairness_applied"))
            self.assertEqual(osum.get("fairness_would_have_selected_without_fairness"), "winner")
            self.assertEqual(osum.get("fairness_selection_reason"), "fairness_rotate_after_repeat_top")

    @patch("argus.orchestrator.advancement.advance_orchestration")
    @patch("argus.orchestrator.advancement.emit_orchestration_batch")
    def test_fairness_does_not_override_stronger_priority(
        self, mock_emit: MagicMock, mock_adv: MagicMock
    ) -> None:
        """Higher-priority product is first in rank; last batch was a lower-ranked id — take top."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            batch_path = root / "runs" / "orchestration" / "latest" / "batch_advancement.json"
            batch_path.parent.mkdir(parents=True, exist_ok=True)
            batch_path.write_text(
                json.dumps({"schema": ORCHESTRATION_BATCH_ADVANCEMENT_SCHEMA, "selected_product_id": "weak"}),
                encoding="utf-8",
            )
            mock_emit.return_value = (
                {
                    "cross_product_prioritization": {
                        "ranked_product_ids": ["strong", "weak"],
                        "entries": [_entry("strong", True), _entry("weak", True)],
                    },
                },
                None,
            )
            mock_adv.return_value = (root / "a.json", {})
            _, body = run_orchestration_batch_advance(root, ["weak", "strong"], execute=False)
            self.assertEqual(mock_adv.call_args[0][1], "strong")
            self.assertFalse((body.get("batch_advancement_fairness") or {}).get("skipped_repeat_top_for_fairness"))
            osum = json.loads(orchestration_operator_summary_path(root).read_text(encoding="utf-8"))
            self.assertFalse(osum.get("fairness_applied"))

    @patch("argus.orchestrator.advancement.advance_orchestration")
    @patch("argus.orchestrator.advancement.emit_orchestration_batch")
    def test_short_history_dominance_skips_priority_top(
        self, mock_emit: MagicMock, mock_adv: MagicMock
    ) -> None:
        """A,B,A,B,A pattern in rolling history: skip top even when last batch was not top."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            batch_path = root / "runs" / "orchestration" / "latest" / "batch_advancement.json"
            batch_path.parent.mkdir(parents=True, exist_ok=True)
            batch_path.write_text(
                json.dumps(
                    {
                        "schema": ORCHESTRATION_BATCH_ADVANCEMENT_SCHEMA,
                        "selected_product_id": "other",
                        "recent_selected_product_ids": [
                            "winner",
                            "other",
                            "winner",
                            "other",
                            "winner",
                        ],
                    }
                ),
                encoding="utf-8",
            )
            mock_emit.return_value = (
                {
                    "cross_product_prioritization": {
                        "ranked_product_ids": ["winner", "other"],
                        "entries": [_entry("winner", True), _entry("other", True)],
                    },
                },
                None,
            )
            mock_adv.return_value = (
                root / "runs" / "orchestration" / "latest" / "advancements" / "other.json",
                {"action_status": "skipped"},
            )
            _, body = run_orchestration_batch_advance(root, ["other", "winner"], execute=False)
            self.assertEqual(mock_adv.call_args[0][1], "other")
            ff = body.get("batch_advancement_fairness") or {}
            self.assertTrue(ff.get("skipped_top_due_to_recent_dominance"))
            self.assertFalse(ff.get("skipped_repeat_top_for_fairness"))
            self.assertEqual(ff.get("selection_reason"), "fairness_skip_dominant_in_recent_history")
            self.assertEqual(
                body.get("recent_selected_product_ids"),
                ["other", "winner", "other", "winner", "other"],
            )
            osum = json.loads(orchestration_operator_summary_path(root).read_text(encoding="utf-8"))
            self.assertTrue(osum.get("fairness_applied"))

    @patch("argus.orchestrator.advancement.advance_orchestration")
    @patch("argus.orchestrator.advancement.emit_orchestration_batch")
    def test_higher_priority_product_still_selected_when_not_in_recent_history(
        self, mock_emit: MagicMock, mock_adv: MagicMock
    ) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            batch_path = root / "runs" / "orchestration" / "latest" / "batch_advancement.json"
            batch_path.parent.mkdir(parents=True, exist_ok=True)
            batch_path.write_text(
                json.dumps(
                    {
                        "schema": ORCHESTRATION_BATCH_ADVANCEMENT_SCHEMA,
                        "selected_product_id": "weak",
                        "recent_selected_product_ids": ["weak", "weak", "weak"],
                    }
                ),
                encoding="utf-8",
            )
            mock_emit.return_value = (
                {
                    "cross_product_prioritization": {
                        "ranked_product_ids": ["strong", "weak"],
                        "entries": [_entry("strong", True), _entry("weak", True)],
                    },
                },
                None,
            )
            mock_adv.return_value = (root / "s.json", {})
            _, body = run_orchestration_batch_advance(root, ["weak", "strong"], execute=False)
            self.assertEqual(mock_adv.call_args[0][1], "strong")
            self.assertFalse((body.get("batch_advancement_fairness") or {}).get("skipped_top_due_to_recent_dominance"))

    @patch("argus.orchestrator.advancement.advance_orchestration")
    @patch("argus.orchestrator.advancement.emit_orchestration_batch")
    def test_fairness_stays_on_top_when_no_other_actionable(
        self, mock_emit: MagicMock, mock_adv: MagicMock
    ) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            batch_path = root / "runs" / "orchestration" / "latest" / "batch_advancement.json"
            batch_path.parent.mkdir(parents=True, exist_ok=True)
            batch_path.write_text(
                json.dumps({"schema": ORCHESTRATION_BATCH_ADVANCEMENT_SCHEMA, "selected_product_id": "winner"}),
                encoding="utf-8",
            )
            mock_emit.return_value = (
                {
                    "cross_product_prioritization": {
                        "ranked_product_ids": ["winner", "other"],
                        "entries": [_entry("winner", True), _entry("other", False)],
                    },
                },
                None,
            )
            mock_adv.return_value = (root / "w.json", {})
            _, body = run_orchestration_batch_advance(root, ["winner", "other"], execute=False)
            self.assertEqual(mock_adv.call_args[0][1], "winner")
            self.assertEqual(
                (body.get("batch_advancement_fairness") or {}).get("selection_reason"),
                "priority_rank_first_no_other_actionable",
            )

    @patch("argus.orchestrator.advancement.advance_orchestration")
    @patch("argus.orchestrator.advancement.emit_orchestration_batch")
    def test_fairness_deterministic_same_inputs_same_selection(
        self, mock_emit: MagicMock, mock_adv: MagicMock
    ) -> None:
        mock_emit.return_value = (
            {
                "cross_product_prioritization": {
                    "ranked_product_ids": ["a", "b"],
                    "entries": [_entry("a", True), _entry("b", True)],
                },
            },
            None,
        )
        for _ in range(2):
            with TemporaryDirectory() as tmp:
                root = Path(tmp)
                mock_adv.return_value = (root / "x.json", {})
                _, body = run_orchestration_batch_advance(root, ["a", "b"], execute=False)
                self.assertEqual(body["selected_product_id"], "a")

    def test_operator_summary_integration_repo_relative_links(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "products" / "x").mkdir(parents=True)
            (root / "products" / "x" / "product.yaml").write_text(
                "id: x\nname: T\nowner:\n  team: t\nlifecycle:\n  stage: idea\n"
                "metrics:\n  local_paths: []\n  primary: []\n"
                "cost:\n  monthly_usd: 1\n  notes: \"\"\n"
                "signals: []\n"
                "actions:\n  start: ./s\n  stop: ./s\n  analyze: ./s\n"
                "constraints:\n  max_monthly_cost_usd: 10\n  min_activity_threshold: 0\n",
                encoding="utf-8",
            )
            _, body = run_orchestration_batch_advance(root, ["x"], execute=False)
            self.assertEqual(body.get("selected_product_id"), "x")
            osum = json.loads(orchestration_operator_summary_path(root).read_text(encoding="utf-8"))
            self.assertEqual(osum.get("schema"), ORCHESTRATION_OPERATOR_SUMMARY_SCHEMA)
            links = osum.get("artifact_links") or {}
            self.assertEqual(links.get("orchestration_index_repo_relative"), "runs/orchestration/latest/index.json")
            self.assertEqual(links.get("batch_advancement_repo_relative"), "runs/orchestration/latest/batch_advancement.json")
            self.assertEqual(links.get("orchestration_state_repo_relative"), "runs/orchestration/latest/x.json")
            self.assertEqual(
                links.get("advancement_artifact_repo_relative"),
                "runs/orchestration/latest/advancements/x.json",
            )
            self.assertEqual(osum.get("selected_action_id"), "signals_collect")


if __name__ == "__main__":
    unittest.main()
