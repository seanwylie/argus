"""Tests for product doctrine (YAML, violations, scoring nudges)."""

from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from argus.core.models.enums import FindingKind
from argus.core.models.signal import SignalRecord
from argus.core.models.validation import validate_finding
from argus.doctrine.models import DoctrineConstraints, DoctrineScoring, ProductDoctrine
from argus.doctrine.scoring import apply_doctrine_to_decision_candidates
from argus.doctrine.validate import DoctrineValidationError, validate_doctrine_raw
from argus.doctrine.violations import doctrine_violation_findings
from argus.findings.engine import generate_findings
from argus.lifecycle.model import LifecycleAssessment
from tests.test_decision_engine import _product


class TestDoctrineValidate(unittest.TestCase):
    def test_minimal_valid(self) -> None:
        d = validate_doctrine_raw(
            {
                "schema": "argus.doctrine.v1",
                "summary": "Ship carefully",
            }
        )
        self.assertEqual(d.schema_id, "argus.doctrine.v1")
        self.assertFalse(d.constraints.require_human_review_when_kill_candidate)

    def test_rejects_wrong_schema(self) -> None:
        with self.assertRaises(DoctrineValidationError):
            validate_doctrine_raw({"schema": "other", "summary": "x"})


class TestDoctrineViolations(unittest.TestCase):
    def test_cost_cap_finding(self) -> None:
        p = _product(monthly=100.0, cap=500.0)
        doc = ProductDoctrine(
            schema_id="argus.doctrine.v1",
            summary=None,
            principles=(),
            constraints=DoctrineConstraints(max_monthly_cost_usd=40.0),
            scoring=DoctrineScoring(),
        )
        fs = doctrine_violation_findings(p, [], doc, assessment_for_kill_rule=None)
        self.assertEqual(len(fs), 1)
        self.assertEqual(fs[0].kind, FindingKind.DOCTRINE_VIOLATION)
        validate_finding(fs[0])

    def test_kill_review_finding(self) -> None:
        p = _product()
        doc = ProductDoctrine(
            schema_id="argus.doctrine.v1",
            summary=None,
            principles=(),
            constraints=DoctrineConstraints(require_human_review_when_kill_candidate=True),
            scoring=DoctrineScoring(),
        )
        a = LifecycleAssessment(
            product_id=p.id,
            stage=p.lifecycle.stage,
            move_forward=0.1,
            hold=0.2,
            improve=0.2,
            deprecate=0.3,
            kill=0.9,
            reasoning={},
            kill_candidate=True,
            metadata={},
        )
        fs = doctrine_violation_findings(p, [], doc, assessment_for_kill_rule=a)
        self.assertEqual(len(fs), 1)


class TestDoctrineScoring(unittest.TestCase):
    def test_intent_multiplier(self) -> None:
        from argus.core.models.decision import DecisionCandidate
        from argus.core.models.enums import ActionType

        c = DecisionCandidate(
            id="d1",
            product_id="p1",
            action_type=ActionType.ANALYZE,
            summary="s",
            expected_impact="",
            rationale="r",
            priority_score=50.0,
            metadata={"intent": "escalate_to_human"},
        )
        doc = ProductDoctrine(
            schema_id="argus.doctrine.v1",
            summary=None,
            principles=(),
            constraints=DoctrineConstraints(),
            scoring=DoctrineScoring(intent_priority_multiplier={"escalate_to_human": 1.2}),
        )
        out = apply_doctrine_to_decision_candidates([c], doc)
        self.assertEqual(out[0].priority_score, 60.0)
        self.assertEqual(out[0].metadata.get("doctrine_intent_multiplier"), 1.2)


class TestFindingsEngineDoctrine(unittest.TestCase):
    def test_generate_findings_appends_doctrine(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            pr = repo / "products" / "doc_test"
            pr.mkdir(parents=True)
            (pr / "doctrine.yaml").write_text(
                """schema: argus.doctrine.v1
summary: cap spend
constraints:
  max_monthly_cost_usd: 30
""",
                encoding="utf-8",
            )
            p = replace(
                _product(pid="doc_test", monthly=99.0),
                product_root="products/doc_test",
                config_path="products/doc_test/product.yaml",
            )
            sigs: list[SignalRecord] = []
            fs = generate_findings(p, sigs, repo_root=repo)
            kinds = [f.kind for f in fs]
            self.assertIn(FindingKind.DOCTRINE_VIOLATION, kinds)


if __name__ == "__main__":
    unittest.main()
