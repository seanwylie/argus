"""Creation-candidate artifact stays aligned with operator summary evaluation."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from argus.dashboard.operator_summary import evaluate_operator_summary, run_operator_summary
from argus.policy.learning_synthesis import OPERATOR_LEARNING_SYNTHESIS_SCHEMA
from argus.world_context.persist import WORLD_CONTEXT_CREATION_CANDIDATES_SCHEMA
from argus.world_context.service import (
    build_world_context_payload,
    load_world_context_creation_candidates,
)

_LEARN_STUB: dict = {
    "schema": OPERATOR_LEARNING_SYNTHESIS_SCHEMA,
    "run_id": "learn_stub",
    "top_lessons_so_far": ["Stub lesson A."],
    "sparse_signal_warnings": [],
}


class TestOperatorSummaryCreationCoherence(unittest.TestCase):
    def test_persisted_creation_candidates_match_embedded_payload(self) -> None:
        fix = Path(__file__).resolve().parents[1] / "tmp" / "combined_world_context_signals.json"
        if not fix.is_file():
            self.skipTest("tmp/combined_world_context_signals.json not present")
        signals = json.loads(fix.read_text(encoding="utf-8"))
        wc, errors = build_world_context_payload(signals)
        self.assertFalse(errors)
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            d = root / "runs" / "world_context"
            d.mkdir(parents=True)
            (d / "latest.json").write_text(json.dumps(wc), encoding="utf-8")
            with patch(
                "argus.policy.learning_synthesis.evaluate_operator_learning_synthesis",
                return_value=_LEARN_STUB,
            ):
                pl = evaluate_operator_summary(root, limit_history=5)
            disk = load_world_context_creation_candidates(root)
            emb = pl.get("world_context_creation_candidates")
            self.assertIsNotNone(disk)
            self.assertIsNotNone(emb)
            assert isinstance(disk, dict) and isinstance(emb, dict)
            self.assertEqual(disk.get("schema"), WORLD_CONTEXT_CREATION_CANDIDATES_SCHEMA)
            self.assertEqual(disk.get("primary_candidate_id"), emb.get("primary_candidate_id"))
            self.assertEqual(
                [c.get("candidate_id") for c in (disk.get("candidates") or [])],
                [c.get("candidate_id") for c in (emb.get("candidates") or [])],
            )
            self.assertEqual(disk.get("situation_summary"), emb.get("situation_summary"))

    def test_stale_creation_candidates_removed_when_rules_yield_none(self) -> None:
        from argus.world_context.creation_candidates import build_creation_candidates_payload
        from argus.world_context.interpretation import build_interpretation_payload

        wc = {
            "schema": "argus.world_context.v1",
            "generated_at_utc": "t",
            "advisory_only": True,
            "disclaimer": "d",
            "summary": {"signal_count": 2},
            "signals": [
                {
                    "source": "google_analytics",
                    "signal_type": "traffic",
                    "entity": "a",
                    "value": 10,
                    "unit": "users_last_28d",
                    "observed_at_utc": "t",
                    "freshness_status": "fresh",
                    "confidence": "high",
                    "provenance": "p",
                },
                {
                    "source": "google_analytics",
                    "signal_type": "traffic",
                    "entity": "b",
                    "value": 100,
                    "unit": "users_last_28d",
                    "observed_at_utc": "t",
                    "freshness_status": "fresh",
                    "confidence": "high",
                    "provenance": "p",
                },
            ],
        }
        interp = build_interpretation_payload(wc)
        assert interp is not None
        self.assertIsNone(build_creation_candidates_payload(wc, interp))

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            d = root / "runs" / "world_context"
            d.mkdir(parents=True)
            (d / "latest.json").write_text(json.dumps(wc), encoding="utf-8")
            cc_dir = d / "creation_candidates"
            cc_dir.mkdir(parents=True)
            stale = {
                "schema": WORLD_CONTEXT_CREATION_CANDIDATES_SCHEMA,
                "generated_at_utc": "old",
                "candidates": [{"candidate_id": "stale_only"}],
            }
            (cc_dir / "latest.json").write_text(json.dumps(stale), encoding="utf-8")
            with patch(
                "argus.policy.learning_synthesis.evaluate_operator_learning_synthesis",
                return_value=_LEARN_STUB,
            ):
                pl = evaluate_operator_summary(root, limit_history=5)
            self.assertIsNone(pl.get("world_context_creation_candidates"))
            self.assertFalse((cc_dir / "latest.json").is_file())

    def test_no_save_does_not_touch_creation_candidates_artifact(self) -> None:
        fix = Path(__file__).resolve().parents[1] / "tmp" / "combined_world_context_signals.json"
        if not fix.is_file():
            self.skipTest("tmp/combined_world_context_signals.json not present")
        signals = json.loads(fix.read_text(encoding="utf-8"))
        wc, errors = build_world_context_payload(signals)
        self.assertFalse(errors)
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            d = root / "runs" / "world_context"
            d.mkdir(parents=True)
            (d / "latest.json").write_text(json.dumps(wc), encoding="utf-8")
            cc_path = d / "creation_candidates" / "latest.json"
            cc_path.parent.mkdir(parents=True)
            cc_path.write_text('{"schema": "argus.world_context.creation_candidates.v1", "stale": true}', encoding="utf-8")
            with patch(
                "argus.policy.learning_synthesis.evaluate_operator_learning_synthesis",
                return_value=_LEARN_STUB,
            ):
                run_operator_summary(root, limit_history=5, write_artifacts=False)
            self.assertIn("stale", cc_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
