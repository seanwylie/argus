"""Tests for :mod:`argus.portfolio.artifact_coherence`."""

from __future__ import annotations

import json
import subprocess
import sys
import unittest
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from argus.cli.portfolio_cmd import cmd_portfolio_artifact_coherence
from argus.dashboard.operator_summary import evaluate_operator_summary
from argus.portfolio.artifact_coherence import (
    artifact_coherence_dir,
    coherence_strict_should_fail,
    evaluate_artifact_coherence,
    run_artifact_coherence,
)
from argus.portfolio.cycle import PORTFOLIO_CYCLE_SCHEMA, run_portfolio_cycle
from argus.portfolio.intervention import PORTFOLIO_INTERVENTION_SCHEMA
from argus.portfolio.intervention_inbox import INTERVENTION_INBOX_SCHEMA
from argus.portfolio.operator_queue import OPERATOR_QUEUE_SCHEMA
from argus.portfolio.outcomes import PORTFOLIO_OUTCOMES_SCHEMA
from argus.portfolio.patterns import PORTFOLIO_PATTERNS_SCHEMA
from argus.portfolio.strategy import PORTFOLIO_STRATEGY_SCHEMA

_REPO = Path(__file__).resolve().parents[1]


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _minimal_outcomes(root: Path) -> None:
    _write_json(
        root / "runs" / "portfolio" / "outcomes" / "latest.json",
        {
            "schema": PORTFOLIO_OUTCOMES_SCHEMA,
            "run_id": "o1",
            "evaluated_at_utc": "2020-01-01T00:00:00Z",
            "per_product_outcomes": [],
            "products_with_negative_trajectory": [],
            "portfolio_mission_provenance": {},
        },
    )


def _minimal_queue(root: Path) -> None:
    _write_json(
        root / "runs" / "portfolio" / "operator_queue" / "latest.json",
        {
            "schema": OPERATOR_QUEUE_SCHEMA,
            "generated_at_utc": "2020-01-01T00:00:00Z",
            "scoring_weights_version": "1",
            "weight_ledger": {},
            "entries": [],
        },
    )


def _minimal_patterns(root: Path) -> None:
    _write_json(
        root / "runs" / "portfolio" / "patterns" / "latest.json",
        {
            "schema": PORTFOLIO_PATTERNS_SCHEMA,
            "run_id": "p1",
            "evaluated_at_utc": "2020-01-01T00:00:00Z",
            "detected_patterns": [],
        },
    )


def _minimal_intervention_report(root: Path) -> None:
    _write_json(
        root / "runs" / "portfolio" / "intervention" / "latest.json",
        {
            "schema": PORTFOLIO_INTERVENTION_SCHEMA,
            "run_id": "i1",
            "evaluated_at_utc": "2020-01-01T00:00:00Z",
            "flagged_products": [],
            "stable_benign_products": [],
        },
    )


def _minimal_intervention_inbox(root: Path) -> None:
    _write_json(
        root / "runs" / "portfolio" / "intervention_inbox" / "latest.json",
        {
            "schema": INTERVENTION_INBOX_SCHEMA,
            "built_at_utc": "2020-01-01T00:00:00Z",
            "source_intervention_run_id": "i1",
            "recent_intervention_runs_considered": 10,
            "open_items": [],
            "action_state": {},
        },
    )


def _minimal_cycle(root: Path, *, flagged: list[dict]) -> None:
    _write_json(
        root / "runs" / "portfolio" / "cycle" / "latest.json",
        {
            "schema": PORTFOLIO_CYCLE_SCHEMA,
            "run_id": "c1",
            "generated_at_utc": "2020-01-01T00:00:00Z",
            "ok": True,
            "inputs": {},
            "stages": {},
            "satellite_refresh": {},
            "summary": {
                "intervention": {"flagged_products": flagged},
            },
            "artifact_paths": {},
        },
    )


def _minimal_strategy(root: Path, *, posture: str = "harvest") -> None:
    _write_json(
        root / "runs" / "portfolio" / "strategy" / "latest.json",
        {
            "schema": PORTFOLIO_STRATEGY_SCHEMA,
            "run_id": "s1",
            "evaluated_at_utc": "2020-01-01T00:00:00Z",
            "strategic_posture": posture,
            "posture_scores": {},
            "rationale": [],
            "dominant_mission_mix": {},
            "portfolio_pressures": [],
            "top_opportunities": [],
            "top_risks": [],
            "inputs": {
                "creation_proposals_loaded": False,
                "canonical_artifacts": {
                    "portfolio_outcomes_latest": True,
                    "portfolio_intervention_latest": True,
                    "intervention_inbox_latest": True,
                    "operator_queue_latest": True,
                    "portfolio_patterns_latest": True,
                },
            },
        },
    )


def _minimal_product(root: Path, pid: str) -> None:
    pr = root / "products" / pid
    pr.mkdir(parents=True)
    (pr / "scripts").mkdir(parents=True)
    (pr / "scripts" / "s.sh").write_text("#!/bin/sh\necho\n", encoding="utf-8")
    (pr / "product.yaml").write_text(
        f"""
id: {pid}
name: T
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
""",
        encoding="utf-8",
    )


class TestArtifactCoherence(unittest.TestCase):
    def test_coherence_strict_should_fail_matrix(self) -> None:
        self.assertFalse(coherence_strict_should_fail("valid", strict_mode="all"))
        self.assertTrue(coherence_strict_should_fail("degraded", strict_mode="all"))
        self.assertTrue(coherence_strict_should_fail("warning", strict_mode="all"))
        self.assertTrue(coherence_strict_should_fail("invalid", strict_mode="all"))
        self.assertFalse(coherence_strict_should_fail("degraded", strict_mode="invalid-only"))
        self.assertFalse(coherence_strict_should_fail("warning", strict_mode="invalid-only"))
        self.assertTrue(coherence_strict_should_fail("invalid", strict_mode="invalid-only"))

    def test_canonical_outcomes_missing_fails(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "products").mkdir()
            pl = evaluate_artifact_coherence(root)
            self.assertEqual(pl["overall_status"], "invalid")
            codes = [f.get("code") for f in pl.get("findings", [])]
            self.assertIn("artifact_coherence.outcomes.canonical_missing", codes)

    def test_lifecycle_outcomes_mismatch_finding(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "products").mkdir()
            with patch(
                "argus.portfolio.artifact_coherence.evaluate_portfolio_lifecycle"
            ) as m_lc:
                m_lc.return_value = {"inputs": {"outcomes_artifact_present": True}}
                pl = evaluate_artifact_coherence(root)
            self.assertEqual(pl["overall_status"], "invalid")
            codes = [f.get("code") for f in pl.get("findings", [])]
            self.assertIn("artifact_coherence.outcomes.lifecycle_inputs_mismatch", codes)

    def test_cycle_flagged_without_intervention_report(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "products").mkdir()
            _minimal_outcomes(root)
            _minimal_cycle(
                root,
                flagged=[{"product_id": "x", "intervention_category": "c", "severity": "high"}],
            )
            pl = evaluate_artifact_coherence(root)
            self.assertEqual(pl["overall_status"], "invalid")
            codes = [f.get("code") for f in pl.get("findings", [])]
            self.assertIn(
                "artifact_coherence.intervention.flagged_without_canonical_report", codes
            )

    def test_strategy_create_without_creation_proposals_warns(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _minimal_product(root, "a")
            _minimal_outcomes(root)
            _minimal_queue(root)
            _minimal_patterns(root)
            _minimal_intervention_report(root)
            _minimal_intervention_inbox(root)
            _minimal_cycle(root, flagged=[])
            _minimal_strategy(root, posture="create")
            pl = evaluate_artifact_coherence(root)
            self.assertIn(
                pl["overall_status"],
                ("degraded", "warning", "invalid"),
            )
            codes = [f.get("code") for f in pl.get("findings", [])]
            self.assertIn(
                "artifact_coherence.strategy.create_without_creation_proposals", codes
            )

    def test_happy_path_valid(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _minimal_product(root, "a")
            _minimal_outcomes(root)
            _minimal_queue(root)
            _minimal_patterns(root)
            _minimal_intervention_report(root)
            _minimal_intervention_inbox(root)
            _minimal_cycle(root, flagged=[])
            _minimal_strategy(root, posture="harvest")
            pl = evaluate_artifact_coherence(root)
            self.assertEqual(pl["overall_status"], "valid")

    def test_write_artifacts_creates_latest_and_stamped(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _minimal_product(root, "a")
            _minimal_outcomes(root)
            _minimal_queue(root)
            _minimal_patterns(root)
            _minimal_intervention_report(root)
            _minimal_intervention_inbox(root)
            _minimal_cycle(root, flagged=[])
            _minimal_strategy(root)
            pl = run_artifact_coherence(root, write_artifacts=True)
            rid = str(pl.get("run_id") or "")
            self.assertTrue(rid)
            d = artifact_coherence_dir(root)
            self.assertTrue((d / "latest.json").is_file())
            self.assertTrue((d / "latest.md").is_file())
            self.assertTrue((d / f"{rid}.json").is_file())

    def test_cli_strict_nonzero_on_invalid(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "products").mkdir()
            class Args:
                json = False
                strict = True
                strict_mode = None
                products_dir = None
                no_write_artifact = True

            code = cmd_portfolio_artifact_coherence(root, Args())
            self.assertEqual(code, 1)

    def test_cli_strict_zero_on_valid_repo(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _minimal_product(root, "a")
            _minimal_outcomes(root)
            _minimal_queue(root)
            _minimal_patterns(root)
            _minimal_intervention_report(root)
            _minimal_intervention_inbox(root)
            _minimal_cycle(root, flagged=[])
            _minimal_strategy(root)

            class Args:
                json = False
                strict = True
                strict_mode = None
                products_dir = None
                no_write_artifact = True

            code = cmd_portfolio_artifact_coherence(root, Args())
            self.assertEqual(code, 0)

    @patch("argus.cli.portfolio_cmd.run_artifact_coherence")
    def test_cli_strict_mode_invalid_only_exits_zero_on_degraded(self, mock_run: object) -> None:
        mock_run.return_value = {"overall_status": "degraded", "run_id": "x", "findings": []}
        with TemporaryDirectory() as tmp:
            root = Path(tmp)

            class Args:
                json = False
                strict = False
                strict_mode = "invalid-only"
                products_dir = None
                no_write_artifact = True

            code = cmd_portfolio_artifact_coherence(root, Args())
            self.assertEqual(code, 0)

    @patch("argus.cli.portfolio_cmd.run_artifact_coherence")
    def test_cli_strict_mode_all_exits_nonzero_on_degraded(self, mock_run: object) -> None:
        mock_run.return_value = {"overall_status": "degraded", "run_id": "x", "findings": []}
        with TemporaryDirectory() as tmp:
            root = Path(tmp)

            class Args:
                json = False
                strict = False
                strict_mode = "all"
                products_dir = None
                no_write_artifact = True

            code = cmd_portfolio_artifact_coherence(root, Args())
            self.assertEqual(code, 1)

    @patch("argus.cli.portfolio_cmd.run_artifact_coherence")
    def test_cli_json_includes_cli_evaluation(self, mock_run: object) -> None:
        mock_run.return_value = {"overall_status": "valid", "run_id": "r", "findings": []}
        with TemporaryDirectory() as tmp:
            root = Path(tmp)

            class Args:
                json = True
                strict = True
                strict_mode = None
                products_dir = None
                no_write_artifact = True

            buf = StringIO()
            with patch("sys.stdout", buf):
                code = cmd_portfolio_artifact_coherence(root, Args())
            self.assertEqual(code, 0)
            out = json.loads(buf.getvalue())
            ce = out.get("cli_evaluation") or {}
            self.assertEqual(ce.get("strict_mode"), "all")
            self.assertTrue(ce.get("strict_exit_requested"))
            self.assertFalse(ce.get("would_exit_nonzero"))

    def test_operator_summary_outcomes_not_synthesized_when_missing(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "products").mkdir()
            pl = evaluate_operator_summary(root, limit_history=5)
            self.assertFalse(
                (pl.get("inputs") or {}).get("canonical_portfolio_outcomes_loaded")
            )
            o = pl.get("outcome_summary") or {}
            self.assertFalse(o.get("present"))

    def test_cycle_satellite_writes_patterns_visible_to_coherence(self) -> None:
        """Normal persisted cycle refreshes portfolio patterns so coherence sees them (no silent read-path generation)."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _minimal_product(root, "a")
            pl = run_portfolio_cycle(
                root,
                limit=0,
                write_cycle_artifacts=True,
                write_stage_artifacts=True,
                write_artifact_coherence=True,
            )
            self.assertTrue(pl.get("ok"))
            self.assertEqual((pl.get("satellite_refresh") or {}).get("portfolio_patterns", {}).get("status"), "ok")
            ac = evaluate_artifact_coherence(root)
            st = (ac.get("checks") or {}).get("strategy_input_integrity") or {}
            present = st.get("present_inputs") or {}
            self.assertTrue(
                present.get("portfolio_patterns"),
                msg=f"expected portfolio_patterns in present_inputs: {st}",
            )


class TestArtifactCoherenceCliSmoke(unittest.TestCase):
    def test_portfolio_artifact_coherence_help(self) -> None:
        r = subprocess.run(
            [sys.executable, "-m", "argus.cli.main", "portfolio", "artifact-coherence", "--help"],
            cwd=_REPO,
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        self.assertIn("artifact", r.stdout.lower())


if __name__ == "__main__":
    unittest.main()
