"""Tests for self-improvement findings, ranking, and planning output."""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from argus.self_improvement.findings import generate_self_findings
from argus.self_improvement.models import SelfFindingKind, SelfProposalKind
from argus.self_improvement.planning import (
    append_self_improvement_weekly_section,
    build_self_improvement_plan,
    load_latest_plan_json,
    write_plan_artifacts,
)
from argus.self_improvement.prioritize import rank_proposals
from argus.self_improvement.propose import proposals_from_findings


def _minimal_argus_tree(root: Path) -> None:
    """Enough structure for heuristics without full products."""
    (root / "argus" / "pkg").mkdir(parents=True)
    for i in range(25):
        (root / "argus" / "pkg" / f"m{i}.py").write_text("# mod\n", encoding="utf-8")
    (root / "tests").mkdir(parents=True)
    (root / "tests" / "test_x.py").write_text("def test_x():\n    assert 1\n", encoding="utf-8")


class TestSelfFindings(unittest.TestCase):
    def test_weak_test_coverage_finding(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _minimal_argus_tree(root)
            fs = generate_self_findings(root)
            kinds = {f.kind for f in fs}
            self.assertIn(SelfFindingKind.WEAK_TEST_COVERAGE, kinds)

    def test_proposals_cover_finding_kinds(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _minimal_argus_tree(root)
            fs = generate_self_findings(root)
            ps = proposals_from_findings(fs)
            self.assertEqual(len(ps), len(fs))
            kinds = {p.kind for p in ps}
            self.assertTrue(len(kinds) >= 1)


class TestSelfPrioritize(unittest.TestCase):
    def test_ranking_order_deterministic(self) -> None:
        from argus.self_improvement.models import (
            ProposalScores,
            SelfChangeRisk,
            SelfImprovementProposal,
        )

        a = SelfImprovementProposal(
            id="p_a",
            kind=SelfProposalKind.ADD_DOCS,
            title="low",
            summary="x",
            scores=ProposalScores(ecosystem_leverage=0.2, safety_impact=0.2, implementation_effort=0.9),
        )
        b = SelfImprovementProposal(
            id="p_b",
            kind=SelfProposalKind.STRENGTHEN_ARTIFACT_VALIDATION,
            title="high",
            summary="y",
            scores=ProposalScores(ecosystem_leverage=0.9, safety_impact=0.95, implementation_effort=0.2),
            risk=SelfChangeRisk.HIGH,
            requires_operator_approval=True,
        )
        ranked = rank_proposals([a, b], strategy_mode=None)
        self.assertEqual(ranked[0].proposal.id, "p_b")
        self.assertGreater(ranked[0].total_score, ranked[1].total_score)


class TestSelfPlanning(unittest.TestCase):
    def test_plan_artifacts_and_weekly_append(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _minimal_argus_tree(root)
            plan = build_self_improvement_plan(root)
            self.assertTrue(plan.top_this_week or not plan.proposals)
            paths = write_plan_artifacts(root, plan)
            self.assertTrue(paths["plan_latest.json"].is_file())
            raw = load_latest_plan_json(root)
            self.assertIsNotNone(raw)
            assert raw is not None
            self.assertEqual(raw.get("schema"), "argus.self_improvement_plan.v1")
            self.assertIn("defer", raw)
            md0 = "# Weekly\n\nhello"
            md1 = append_self_improvement_weekly_section(md0, root)
            self.assertIn("self-improvement", md1.lower())


if __name__ == "__main__":
    unittest.main()
