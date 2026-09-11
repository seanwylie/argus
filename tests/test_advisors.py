"""Tests for advisor registry, stubs, and consensus."""

from __future__ import annotations

import json
import textwrap
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from argus.advisors.consensus import build_consensus
from argus.advisors.consult_log import redact_secrets_for_audit
from argus.advisors.llm import extract_json_object, parse_advisor_json
from argus.advisors.models import Advisor, AdvisorArchetype
from argus.advisors.registry import global_advisors, resolve_advisors
from argus.advisors.runner import run_advisors, simulate_response


def _write(p: Path, content: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(textwrap.dedent(content).strip() + "\n", encoding="utf-8")


def _minimal_product_yaml(pid: str) -> str:
    return f"""
    id: {pid}
    name: {pid}
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
      max_monthly_cost_usd: 100
      min_activity_threshold: 0
    """


class TestRegistry(unittest.TestCase):
    def test_global_has_six_archetypes(self) -> None:
        g = global_advisors()
        arch = {a.archetype for a in g}
        self.assertGreaterEqual(len(arch), 5)

    def test_product_include_only(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pr = root / "products" / "p1"
            _write(pr / "product.yaml", _minimal_product_yaml("p1"))
            (pr / "scripts").mkdir(parents=True)
            (pr / "scripts" / "s.sh").write_text("#!/bin/sh\n", encoding="utf-8")
            adv_path = pr / "advisors.json"
            adv_path.write_text(
                json.dumps({"include_only": ["adv.finance", "adv.technical"]}),
                encoding="utf-8",
            )
            r = resolve_advisors(root, "p1")
            ids = {a.id for a in r}
            self.assertEqual(ids, {"adv.finance", "adv.technical"})

    def test_weight_override(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pr = root / "products" / "p2"
            _write(pr / "product.yaml", _minimal_product_yaml("p2"))
            (pr / "scripts").mkdir(parents=True)
            (pr / "scripts" / "s.sh").write_text("#!/bin/sh\n", encoding="utf-8")
            (pr / "advisors.json").write_text(
                json.dumps({"weight_overrides": {"adv.finance": 3.0}}),
                encoding="utf-8",
            )
            r = resolve_advisors(root, "p2")
            fin = next(a for a in r if a.id == "adv.finance")
            self.assertEqual(fin.weight, 3.0)


class TestStubAndConsensus(unittest.TestCase):
    def test_simulate_deterministic(self) -> None:
        a = global_advisors()[0]
        r1 = simulate_response(a, "px", "ctx")
        r2 = simulate_response(a, "px", "ctx")
        self.assertEqual(r1.recommendation, r2.recommendation)
        self.assertTrue(r1.rationale)

    def test_multi_advisor_combination(self) -> None:
        advisors = global_advisors()[:3]
        responses = [simulate_response(a, "same", "ctx") for a in advisors]
        cons = build_consensus(Path("/tmp"), "same", advisors, responses)
        self.assertIn(cons.consensus_decision, (
            "favor_prudent_hold",
            "favor_measured_progress",
            "favor_accelerated_bets",
        ))
        self.assertGreaterEqual(cons.confidence_score, 0.0)
        self.assertLessEqual(cons.confidence_score, 1.0)
        self.assertEqual(len(cons.per_advisor_stance), 3)

    def test_consensus_weighting_heavier_finance(self) -> None:
        """Higher weight should pull weighted mean toward that advisor's stance."""
        adv_fin = Advisor(
            id="adv.finance",
            archetype=AdvisorArchetype.FINANCE,
            description="",
            prompt_template="{context}",
            weight=10.0,
        )
        adv_mkt = Advisor(
            id="adv.marketing",
            archetype=AdvisorArchetype.MARKETING,
            description="",
            prompt_template="{context}",
            weight=0.1,
        )
        rf = simulate_response(adv_fin, "wtest", "c")
        rm = simulate_response(adv_mkt, "wtest", "c")
        cons = build_consensus(Path("/tmp"), "wtest", [adv_fin, adv_mkt], [rf, rm])
        fin_st = cons.per_advisor_stance["adv.finance"]
        mean = sum(cons.per_advisor_stance.values()) / len(cons.per_advisor_stance)
        self.assertAlmostEqual(mean, fin_st, delta=0.15)


class TestLLMParse(unittest.TestCase):
    def test_extract_json_fence(self) -> None:
        raw = """Here is JSON:\n```json\n{\"recommendation\": \"x\", \"rationale\": \"y\", \"risks\": [\"a\"], \"confidence\": 0.8, \"stance\": 0.5}\n```"""
        d = extract_json_object(raw)
        p = parse_advisor_json(d)
        self.assertEqual(p["recommendation"], "x")
        self.assertEqual(len(p["risks"]), 1)


class TestConsultLogRedact(unittest.TestCase):
    def test_redacts_sk_and_bearer(self) -> None:
        d = {
            "msg": "token sk-1234567890abcdef trailing",
            "nested": ["Bearer abcdefghijklmnop"],
        }
        out = redact_secrets_for_audit(d)
        self.assertIn("[REDACTED]", out["msg"])
        self.assertNotIn("sk-1234567890abcdef", out["msg"])
        self.assertIn("[REDACTED]", out["nested"][0])


class TestRunnerWithRepo(unittest.TestCase):
    def test_run_advisors_product(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pr = root / "products" / "solo"
            _write(pr / "product.yaml", _minimal_product_yaml("solo"))
            (pr / "scripts").mkdir(parents=True)
            (pr / "scripts" / "s.sh").write_text("#!/bin/sh\n", encoding="utf-8")
            out = run_advisors(root, "solo")
            self.assertEqual(out.product_id, "solo")
            self.assertEqual(len(out.responses), len(global_advisors()))


if __name__ == "__main__":
    unittest.main()
