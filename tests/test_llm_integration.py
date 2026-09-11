"""LLM integration: gated expansion, advisor council metrics, safe fallback (mocked; no network)."""

from __future__ import annotations

import json
import os
import textwrap
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from argus.decision_assessment.evaluate import evaluate_decision_context
from argus.idea_generation.models import Idea, IdeaSource, IdeaType, new_idea_id
from argus.llm import advisor_runner as advisor_runner_mod
from argus.llm import idea_expander as idea_expander_mod
from argus.llm.advisor_runner import (
    council_result_to_jsonable,
    load_council_metrics,
    run_advisor_council,
)
from argus.llm.client import LLMCompletionResult, LLMCompletionStatus
from argus.llm.idea_expander import expand_idea_with_llm


def _write(p: Path, content: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(textwrap.dedent(content).strip() + "\n", encoding="utf-8")


def _minimal_valid_yaml(product_id: str = "p1") -> str:
    return f"""
    id: {product_id}
    name: Test
    owner:
      team: test
    lifecycle:
      stage: idea
    metrics:
      local_paths: []
      primary: []
    cost:
      monthly_usd: 0
      notes: ""
    signals:
      - type: filesystem
        enabled: true
    actions:
      start: "./scripts/s.sh"
      stop: "./scripts/s.sh"
      analyze: "./scripts/s.sh"
    constraints:
      max_monthly_cost_usd: 1
      min_activity_threshold: 0
    """


def _minimal_product_layout(root: Path, product_id: str = "p1") -> None:
    _write(root / "products" / product_id / "product.yaml", _minimal_valid_yaml(product_id))
    (root / "products" / product_id / "scripts").mkdir(parents=True, exist_ok=True)
    (root / "products" / product_id / "scripts" / "s.sh").write_text("#!/bin/sh\ntrue\n", encoding="utf-8")


class TestLLMDisabledNoExpansion(unittest.TestCase):
    def test_disabled_leaves_expansion_none(self) -> None:
        idea = Idea(
            idea_id=new_idea_id("idea"),
            title="T",
            description="D",
            type=IdeaType.EXPLORE,
            source=IdeaSource.SYNTHESIS,
            novelty_score=0.5,
            adjacency_score=0.5,
            expected_value_score=0.5,
            confidence_score=0.5,
            cost_estimate="small",
            channel_type="hybrid",
            monetization_type="hybrid",
            rationale="r",
            product_id="p1",
        )
        with patch.dict(os.environ, {"ARGUS_LLM_ENABLED": ""}, clear=False):
            expand_idea_with_llm(idea, product_id="p1")
        self.assertIsNone(idea.llm_expansion)


class TestLLMExpansionMocked(unittest.TestCase):
    def test_enabled_adds_fields(self) -> None:
        idea = Idea(
            idea_id="idea_fixed",
            title="T",
            description="D",
            type=IdeaType.EXPLORE,
            source=IdeaSource.SYNTHESIS,
            novelty_score=0.5,
            adjacency_score=0.5,
            expected_value_score=0.5,
            confidence_score=0.5,
            cost_estimate="small",
            channel_type="hybrid",
            monetization_type="hybrid",
            rationale="r",
            product_id="p1",
        )
        payload = json.dumps(
            {
                "expanded_description": "More concrete",
                "improved_title": None,
                "concrete_examples": ["ex1"],
                "monetization_suggestions": ["m1"],
            }
        )
        with patch.dict(os.environ, {"ARGUS_LLM_ENABLED": "1", "ARGUS_OPENAI_API_KEY": "sk-test"}, clear=False):
            with patch.object(
                idea_expander_mod,
                "complete",
                return_value=LLMCompletionResult(LLMCompletionStatus.OK, payload),
            ):
                expand_idea_with_llm(idea, product_id="p1")
        self.assertIsNotNone(idea.llm_expansion)
        assert idea.llm_expansion is not None
        self.assertEqual(idea.llm_expansion.get("expanded_description"), "More concrete")
        self.assertEqual(idea.title, "T")


class TestAdvisorCouncilDecisionNudge(unittest.TestCase):
    def setUp(self) -> None:
        self._env = patch.dict(
            os.environ,
            {"ARGUS_LLM_ENABLED": "1", "ARGUS_OPENAI_API_KEY": "x"},
            clear=False,
        )
        self._env.start()

    def tearDown(self) -> None:
        self._env.stop()

    @patch.object(
        advisor_runner_mod,
        "complete",
        return_value=LLMCompletionResult(LLMCompletionStatus.ERROR, None),
    )
    def test_council_graceful_when_llm_errors(self, _mock: object) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _minimal_product_layout(root, "p1")
            r = run_advisor_council(root, "p1")
            self.assertEqual(len(r.per_advisor), 5)
            self.assertTrue(all("(llm unavailable)" in o.critique for o in r.per_advisor.values()))


class TestCouncilMetrics(unittest.TestCase):
    def test_load_align_and_conflict(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            d = root / "runs" / "advisors"
            d.mkdir(parents=True)
            payload = {
                "schema": "argus.llm_advisor_council.v1",
                "aggregated_sentiment": "conflict",
                "agreement_signal": 0.2,
            }
            (d / "llm_council_px.latest.json").write_text(json.dumps(payload), encoding="utf-8")
            a, c, s = load_council_metrics(root, "px")
            self.assertAlmostEqual(a, 0.2)
            self.assertTrue(c)
            self.assertIn("conflict", s)


class TestEvaluateAdvisorFields(unittest.TestCase):
    def test_assessment_has_advisor_fields(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _minimal_product_layout(root, "p1")
            # Minimal artifacts for evaluate path
            (root / "runs" / "signals" / "latest").mkdir(parents=True, exist_ok=True)
            (root / "runs" / "signals" / "latest" / "p1.json").write_text(
                json.dumps({"collected_at_utc": "2026-01-01T00:00:00+00:00", "records": []}),
                encoding="utf-8",
            )
            ass = evaluate_decision_context(root, "p1")
            self.assertIsInstance(ass.advisor_alignment_score, (float, type(None)))
            self.assertIsInstance(ass.advisor_conflict_flag, bool)
            self.assertIsInstance(ass.advisor_summary, str)


class TestCouncilResultJson(unittest.TestCase):
    def test_serializes(self) -> None:
        from argus.llm.advisor_runner import AdvisorCouncilResult, AdvisorPerspectiveOutput

        r = AdvisorCouncilResult(
            product_id="p",
            per_advisor={
                "finance": AdvisorPerspectiveOutput(
                    advisor_id="finance",
                    critique="c",
                    risks=["r"],
                    suggested_improvements=["s"],
                    score=0.8,
                    raw_status="ok",
                )
            },
            aggregated_sentiment="aligned",
            agreement_signal=0.9,
        )
        j = council_result_to_jsonable(r)
        self.assertEqual(j["schema"], "argus.llm_advisor_council.v1")
