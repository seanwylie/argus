"""Tests for weekly portfolio planning (deterministic synthesis)."""

from __future__ import annotations

import textwrap
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from argus.planning.plan_actions import build_planning_actions
from argus.planning.prioritize import (
    ProductSignals,
    pick_deprecate_review,
    pick_focus_top3,
    pick_risk_top3,
    pick_watchlist,
    risk_score,
)
from argus.planning.render import plan_to_json, render_markdown, render_terminal_summary
from argus.planning.weekly import build_weekly_plan


def _write(p: Path, content: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(textwrap.dedent(content).strip() + "\n", encoding="utf-8")


def _minimal_product_yaml(pid: str, *, stage: str = "idea", monthly: float = 0.0, cap: float = 100.0) -> str:
    return f"""
    id: {pid}
    name: {pid}
    owner:
      team: test
    lifecycle:
      stage: {stage}
    metrics:
      local_paths: []
      primary: []
    cost:
      monthly_usd: {monthly}
      notes: ""
    signals:
      - type: filesystem
        enabled: true
    actions:
      start: "./scripts/s.sh"
      stop: "./scripts/s.sh"
      analyze: "./scripts/s.sh"
    constraints:
      max_monthly_cost_usd: {cap}
      min_activity_threshold: 0
    """


def _sig(
    pid: str,
    *,
    rank: int = 1,
    ps: float = 0.0,
    kill: bool = False,
    findings: int = 0,
    esc: int = 0,
    churn: float | None = None,
    intent: str = "",
    summary: str = "",
    stage: str = "grow",
    over: bool = False,
    has_fa: bool = True,
) -> ProductSignals:
    return ProductSignals(
        product_id=pid,
        rank=rank,
        priority_score=ps,
        top_intent=intent,
        summary=summary,
        lifecycle_stage=stage,
        kill_candidate=kill,
        findings_count=findings,
        escalation_count=esc,
        monthly_cost_usd=10.0,
        over_budget=over,
        churn_score=churn,
        has_findings_artifact=has_fa,
    )


class TestPrioritize(unittest.TestCase):
    def test_focus_top3_by_priority_score(self) -> None:
        p = [
            _sig("a", ps=10.0),
            _sig("b", ps=30.0),
            _sig("c", ps=20.0),
            _sig("d", ps=5.0),
        ]
        self.assertEqual(pick_focus_top3(p), ["b", "c", "a"])

    def test_risk_top3_deterministic_order(self) -> None:
        p = [
            _sig("x", ps=1.0, kill=True),
            _sig("y", ps=1.0, kill=True, findings=5),
            _sig("z", ps=1.0, esc=2, kill=True),
        ]
        # Escalations dominate composite risk vs findings-only
        out = pick_risk_top3(p)
        self.assertEqual(len(out), 3)
        self.assertEqual(out[0], "z")
        self.assertEqual(out[1], "y")
        self.assertEqual(out[2], "x")

    def test_risk_score_ordering(self) -> None:
        low = _sig("low", esc=0, findings=0)
        high = _sig("high", esc=2, findings=10, over=True, kill=True)
        self.assertGreater(risk_score(high), risk_score(low))

    def test_watchlist_prefers_quiet_low_pressure(self) -> None:
        p = [
            _sig("loud", ps=50.0, findings=0, esc=1),
            _sig("quiet", ps=1.0, findings=1, esc=0),
            _sig("quieter", ps=0.5, findings=0, esc=0),
        ]
        w = pick_watchlist(p)
        self.assertIn("quieter", w)
        self.assertNotIn("loud", w)

    def test_deprecate_review_flags(self) -> None:
        p = [
            _sig("k", kill=True),
            _sig("d", summary="Plan sunset in Q3"),
        ]
        dr = pick_deprecate_review(p)
        self.assertIn("k", dr)
        self.assertIn("d", dr)


class TestWeeklyPlanBuild(unittest.TestCase):
    def test_empty_portfolio(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "products").mkdir(parents=True)
            plan = build_weekly_plan(root)
            self.assertEqual(plan.inventory_valid_count, 0)
            self.assertEqual(plan.focus_products, [])
            self.assertEqual(plan.risk_focus, [])
            self.assertEqual(plan.per_product, [])
            md = render_markdown(plan)
            self.assertIn("Argus weekly portfolio plan", md)
            self.assertIn("empty or insufficient", md)
            self.assertIn("=== Argus weekly plan ===", render_terminal_summary(plan))
            self.assertIn("schema", plan_to_json(plan))

    def test_smoke_with_one_product(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pr = root / "products" / "solo"
            _write(pr / "product.yaml", _minimal_product_yaml("solo"))
            (pr / "scripts").mkdir(parents=True)
            (pr / "scripts" / "s.sh").write_text("#!/bin/sh\necho\n", encoding="utf-8")
            plan = build_weekly_plan(root)
            self.assertEqual(plan.inventory_valid_count, 1)
            self.assertEqual(plan.focus_products, ["solo"])
            self.assertEqual(len(plan.per_product), 1)
            self.assertEqual(plan.per_product[0].product_id, "solo")

    def test_planning_actions_emits_contracts(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pr = root / "products" / "solo"
            _write(pr / "product.yaml", _minimal_product_yaml("solo"))
            (pr / "scripts").mkdir(parents=True)
            (pr / "scripts" / "s.sh").write_text("#!/bin/sh\necho\n", encoding="utf-8")
            bundle = build_planning_actions(root)
            self.assertEqual(bundle.schema, "argus.planning_actions.v1")
            self.assertGreaterEqual(len(bundle.actions), 2)
            cmds = [a.command for a in bundle.actions]
            self.assertTrue(any("argus portfolio refresh" in c for c in cmds))
            self.assertTrue(any("solo" in c for c in cmds))
            for a in bundle.actions:
                self.assertTrue(a.command)
                self.assertIsNotNone(a.expected_outcome)
                self.assertIsNotNone(a.rollback_notes)


if __name__ == "__main__":
    unittest.main()
