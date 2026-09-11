"""Tests for decision memory and churn analysis."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from argus.decision.history.analyze import analyze_churn, intent_bucket
from argus.decision.history.models import DecisionMemoryEntry
from argus.decision.history.store import bundle_to_entry, load_product_decision_history


def _bundle(
    product_id: str,
    ts: str,
    summary: str,
    intent: str,
    *,
    conf: float = 0.5,
) -> dict:
    return {
        "schema": "argus.decisions_bundle.v1",
        "product_id": product_id,
        "generated_at_utc": f"2026-01-{ts}T12:00:00+00:00",
        "repo_root": "/tmp",
        "lifecycle": {
            "stage": "idea",
            "kill_candidate": False,
            "scores": {},
            "reasoning": {},
        },
        "candidates": [
            {
                "summary": summary,
                "priority_score": 1.0,
                "confidence": conf,
                "metadata": {"intent": intent},
                "rationale": "test",
            }
        ],
    }


class TestDecisionMemoryStore(unittest.TestCase):
    def test_load_chronological(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            gen = root / "runs" / "decisions" / "generations"
            gen.mkdir(parents=True)
            for ts, sm, it in (
                ("10", "first", "improve"),
                ("11", "second", "improve"),
                ("12", "third", "hold"),
            ):
                name = f"202601{ts}T120000Z_mem_p.json"
                (gen / name).write_text(
                    json.dumps(_bundle("mem_p", ts, sm, it)),
                    encoding="utf-8",
                )
            hist = load_product_decision_history(root, "mem_p")
            self.assertEqual(len(hist), 3)
            self.assertEqual(hist[0].top_recommended_action, "first")
            self.assertEqual(hist[2].compared_to_previous, "changed")


class TestChurnAnalysis(unittest.TestCase):
    def test_repeated_recommendation(self) -> None:
        entries = [
            DecisionMemoryEntry(
                source_path="a",
                generated_at_utc="t1",
                product_id="p",
                top_recommended_action="same",
                top_intent="improve",
                priority_score=1.0,
                confidence=0.5,
                alternatives=[],
                rationale_summary="",
                compared_to_previous="first",
                lifecycle_stage="idea",
                kill_candidate=False,
            ),
            DecisionMemoryEntry(
                source_path="b",
                generated_at_utc="t2",
                product_id="p",
                top_recommended_action="same",
                top_intent="improve",
                priority_score=1.0,
                confidence=0.52,
                alternatives=[],
                rationale_summary="",
                compared_to_previous="unchanged",
                lifecycle_stage="idea",
                kill_candidate=False,
            ),
        ]
        r = analyze_churn("p", entries, repo_root=None)
        self.assertEqual(r.top_action_change_count, 0)
        self.assertGreaterEqual(r.max_consecutive_same_top, 2)
        self.assertGreater(r.stability_score, r.churn_score)

    def test_churn_scoring_on_flips(self) -> None:
        entries = [
            DecisionMemoryEntry(
                "a",
                "t1",
                "p",
                "hold text",
                "hold",
                1.0,
                0.4,
                [],
                "",
                "first",
                "idea",
                False,
            ),
            DecisionMemoryEntry(
                "b",
                "t2",
                "p",
                "deprecate product",
                "deprecate",
                1.0,
                0.9,
                [],
                "",
                "changed",
                "idea",
                False,
            ),
            DecisionMemoryEntry(
                "c",
                "t3",
                "p",
                "hold again",
                "hold",
                1.0,
                0.3,
                [],
                "",
                "changed",
                "idea",
                False,
            ),
        ]
        r = analyze_churn("p", entries, repo_root=None)
        self.assertEqual(r.top_action_change_count, 2)
        self.assertGreater(r.churn_score, 0.2)

    def test_stability_high_when_unchanged(self) -> None:
        entries = [
            DecisionMemoryEntry(
                "a",
                "t1",
                "p",
                "stable",
                "improve",
                1.0,
                0.5,
                [],
                "",
                "first",
                "idea",
                False,
            ),
            DecisionMemoryEntry(
                "b",
                "t2",
                "p",
                "stable",
                "improve",
                1.0,
                0.51,
                [],
                "",
                "unchanged",
                "idea",
                False,
            ),
        ]
        r = analyze_churn("p", entries, repo_root=None)
        self.assertLess(r.churn_score, 0.15)
        self.assertGreater(r.stability_score, 0.85)


class TestIntentBucket(unittest.TestCase):
    def test_buckets(self) -> None:
        self.assertEqual(intent_bucket("hold", ""), "hold")
        self.assertEqual(intent_bucket("", "deprecate the service"), "deprecate")


class TestBundleToEntry(unittest.TestCase):
    def test_alternatives(self) -> None:
        raw = _bundle("x", "15", "top", "improve")
        raw["candidates"].append(
            {
                "summary": "alt",
                "priority_score": 0.5,
                "confidence": 0.3,
                "metadata": {"intent": "other"},
            }
        )
        e = bundle_to_entry(raw, source_path="runs/x.json", previous_top_summary=None)
        self.assertEqual(len(e.alternatives), 1)
        self.assertEqual(e.alternatives[0].summary, "alt")


if __name__ == "__main__":
    unittest.main()
