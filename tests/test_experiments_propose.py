"""Tests for deterministic experiment proposal generation."""

from __future__ import annotations

import textwrap
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from argus.core.models.enums import (
    EffortBucket,
    FindingKind,
    SeverityLevel,
)
from argus.core.models.finding import Finding
from argus.experiments.propose import format_proposals_text, propose_experiments
from argus.findings.persistence import save_findings_bundle


def _write(p: Path, content: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(textwrap.dedent(content).strip() + "\n", encoding="utf-8")


def _minimal_yaml(pid: str, *, stage: str = "grow") -> str:
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


class TestProposeExperiments(unittest.TestCase):
    def _scaffold_product(self, root: Path, pid: str, *, stage: str = "grow") -> None:
        pr = root / "products" / pid
        _write(pr / "product.yaml", _minimal_yaml(pid, stage=stage))
        (pr / "scripts").mkdir(parents=True)
        (pr / "scripts" / "s.sh").write_text("#!/bin/sh\n", encoding="utf-8")

    def test_grow_stage_yields_marketing_proposal_without_other_signals(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._scaffold_product(root, "grow_only")
            run = propose_experiments(root, product_id="grow_only")
            self.assertTrue(any("marketing" in p.rationale for p in run.proposals))
            self.assertTrue(run.proposals)
            ids_a = [p.proposal_id for p in run.proposals]
            run2 = propose_experiments(root, product_id="grow_only")
            ids_b = [p.proposal_id for p in run2.proposals]
            self.assertEqual(ids_a, ids_b)

    def test_cost_finding_adds_cost_reduction_proposal(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._scaffold_product(root, "costy", stage="maintain")
            f = Finding(
                id="f1",
                product_id="costy",
                kind=FindingKind.COST_RISK,
                severity=SeverityLevel.HIGH,
                effort=EffortBucket.SMALL,
                title="Spend",
                summary="High spend",
                recommendation="Review infra",
                confidence=0.8,
            )
            save_findings_bundle(root, "costy", [f])
            run = propose_experiments(root, product_id="costy")
            kinds = {p.type.value for p in run.proposals}
            self.assertIn("cost_reduction", kinds)
            self.assertTrue(any(p.rationale.startswith("finding:cost") for p in run.proposals))

    def test_format_proposals_text_non_empty(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._scaffold_product(root, "fmt")
            run = propose_experiments(root, product_id="fmt")
            text = format_proposals_text(run)
            self.assertIn("Experiment proposals", text)
            self.assertIn("fmt", text)


if __name__ == "__main__":
    unittest.main()
