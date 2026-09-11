"""Tests for dashboard payload generation."""

from __future__ import annotations

import json
import re
import tempfile
import textwrap
import unittest
from pathlib import Path

from argus.dashboard.data import build_dashboard_payload
from argus.dashboard.render import dashboard_html_template, write_dashboard_html
from argus.history.snapshot import build_portfolio_snapshot
from argus.history.storage import write_snapshot_json


def _write(p: Path, content: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(textwrap.dedent(content).strip() + "\n", encoding="utf-8")


def _minimal_valid_yaml(product_id: str = "dash_p") -> str:
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
      monthly_usd: 10
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


class TestDashboardPayload(unittest.TestCase):
    def test_build_empty_inventory(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "products").mkdir()
            p = build_dashboard_payload(root)
            self.assertEqual(p["schema"], "argus.dashboard.v4")
            self.assertIn("diagnostics", p)
            self.assertIn("integrity", p)
            self.assertEqual(p["products"], [])
            self.assertIn("history", p)
            self.assertEqual(p["history"]["snapshots_count"], 0)
            self.assertIn("economics_resources", p)
            self.assertEqual(p["economics_resources"]["schema"], "argus.dashboard_economics_resources.v1")
            self.assertFalse(p["economics_resources"]["present"])
            self.assertIn("actions_panel", p)
            self.assertEqual(p["actions_panel"]["schema"], "argus.dashboard_actions.v1")
            self.assertIn("proposed", p["actions_panel"])
            self.assertEqual(p.get("temporal_findings_portfolio_total"), 0)
            self.assertIsInstance(p.get("temporal_finding_kind_values"), list)
            self.assertIn("temporal", p)
            self.assertEqual(p["temporal"]["schema"], "argus.dashboard_temporal.v2")
            self.assertIn("summary_counts", p["temporal"])
            self.assertIn("recent_temporal_findings", p["temporal"])
            self.assertIn("ideas", p)
            self.assertEqual(p["ideas"]["schema"], "argus.dashboard_ideas.v1")
            self.assertFalse(p["ideas"].get("present"))
            self.assertIn("last_loop_run", p)
            self.assertEqual(p["last_loop_run"]["schema"], "argus.dashboard_last_loop_run.v1")
            self.assertFalse(p["last_loop_run"].get("present"))

    def test_payload_includes_history_after_snapshots(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            pr = root / "products" / "dash_p"
            _write(pr / "product.yaml", _minimal_valid_yaml())
            (pr / "scripts").mkdir(parents=True)
            (pr / "scripts" / "s.sh").write_text("#!/bin/sh\n", encoding="utf-8")

            s1 = build_portfolio_snapshot(
                root,
                snapshot_id="t1",
                observed_at_utc="2026-04-01T00:00:00+00:00",
                label=None,
            )
            write_snapshot_json(root, s1)
            s2 = build_portfolio_snapshot(
                root,
                snapshot_id="t2",
                observed_at_utc="2026-04-02T00:00:00+00:00",
                label=None,
            )
            write_snapshot_json(root, s2)

            p = build_dashboard_payload(root)
            self.assertEqual(p["schema"], "argus.dashboard.v4")
            self.assertEqual(p["history"]["snapshots_count"], 2)
            self.assertEqual(len(p["history"]["snapshots_catalog"]), 2)
            prod = next(x for x in p["products"] if x["product_id"] == "dash_p")
            self.assertEqual(len(prod["history_points"]), 2)
            self.assertIsNotNone(prod.get("trend_summary"))
            self.assertIn("artifact_links", p)


class TestDashboardWrite(unittest.TestCase):
    def test_writes_html(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "products").mkdir()
            out = root / "dash.html"
            path, _pl = write_dashboard_html(root, out)
            self.assertTrue(path.is_file())
            text = path.read_text(encoding="utf-8")
            self.assertIn("Argus portfolio", text)
            self.assertIn("argus.dashboard.v4", text)
            self.assertIn("Economics — cost resources", text)
            self.assertIn("Actions — execution preview", text)
            self.assertIn("Ideas — generation engine", text)

    def test_template_contains_history_controls(self) -> None:
        t = dashboard_html_template()
        self.assertIn("histWin", t)
        self.assertIn("Δ find", t)
        self.assertIn("sparklineSVG", t)
        self.assertIn("renderEconomicsPanel", t)
        self.assertIn("renderTemporalPanel", t)
        self.assertIn("renderIdeasPanel", t)
        self.assertIn("ideasPanelRoot", t)
        self.assertIn("temporal_overall", t)
        self.assertIn("operatorAlerts", t)
        self.assertIn("miniBarScore", t)
        self.assertIn("operator_visibility", t)
        self.assertIn("orchPortfolioPriRoot", t)
        self.assertIn("orchPortfolioTrendsRoot", t)
        self.assertIn("renderOrchestrationPortfolioPriorities", t)
        self.assertIn("renderOrchestrationPortfolioTrends", t)


class TestDashboardIdeas(unittest.TestCase):
    def test_payload_includes_ideas_with_latest_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            pr = root / "products" / "dash_p"
            _write(pr / "product.yaml", _minimal_valid_yaml())
            (pr / "scripts").mkdir(parents=True)
            (pr / "scripts" / "s.sh").write_text("#!/bin/sh\n", encoding="utf-8")

            ideas_path = root / "runs" / "ideas" / "latest.json"
            ideas_path.parent.mkdir(parents=True, exist_ok=True)
            ideas_path.write_text(
                json.dumps(
                    {
                        "schema": "argus.ideas_bundle.v1",
                        "generated_at_utc": "2026-06-01T12:00:00+00:00",
                        "repo_root": str(root),
                        "product_id": "dash_p",
                        "idea_count": 1,
                        "ideas": [
                            {
                                "idea_id": "idea_1",
                                "title": "Test invent",
                                "description": "Novel hybrid platform",
                                "type": "invent",
                                "source": "synthesis",
                                "novelty_score": 0.8,
                                "adjacency_score": 0.5,
                                "expected_value_score": 0.6,
                                "confidence_score": 0.7,
                                "cost_estimate": "medium",
                                "channel_type": "hybrid",
                                "monetization_type": "subscription",
                                "rationale": "Synthetic test",
                                "product_id": "dash_p",
                                "schema": "argus.idea.v1",
                            }
                        ],
                        "meta": {
                            "rejected_duplicates": [
                                {
                                    "title": "dup",
                                    "type": "explore",
                                    "source": "mutation",
                                    "reason": "duplicate_title",
                                    "selection": "rejected_duplicate",
                                }
                            ]
                        },
                    }
                ),
                encoding="utf-8",
            )

            p = build_dashboard_payload(root)
            ib = p["ideas"]
            self.assertTrue(ib.get("present"))
            self.assertEqual(ib["portfolio"]["idea_total"], 1)
            self.assertEqual(ib["portfolio"]["by_type"]["invent"], 1)
            self.assertGreaterEqual(ib["portfolio"]["rejected_duplicate_total"], 1)
            prod = next(x for x in p["products"] if x["product_id"] == "dash_p")
            self.assertEqual(prod["ideas_count"], 1)
            self.assertEqual(prod["ideas_invent_count"], 1)
            self.assertEqual(len(prod["ideas"]), 1)
            self.assertEqual(prod["ideas"][0]["selection_status"], "selected")
            self.assertIn("diversity_score", prod["ideas"][0])


class TestDashboardEmptyHistoryFallback(unittest.TestCase):
    def test_html_valid_json_with_empty_history(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "products").mkdir()
            out = root / "dash.html"
            write_dashboard_html(root, out)
            text = out.read_text(encoding="utf-8")
            m = re.search(
                r'<script id="payload"[^>]*>([\s\S]*?)</script>',
                text,
            )
            self.assertIsNotNone(m)
            payload = json.loads(m.group(1))
            self.assertEqual(payload["history"]["snapshots_count"], 0)


class TestDashboardDiagnostics(unittest.TestCase):
    def test_invalid_history_snapshot_json_warns(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "products").mkdir()
            bad = root / "runs" / "history" / "snapshots" / "bad_snap"
            bad.mkdir(parents=True)
            (bad / "snapshot.json").write_text("{ not valid json", encoding="utf-8")

            p = build_dashboard_payload(root, strict=False)
            diag = p["diagnostics"]
            self.assertTrue(any(w.get("code") == "history_snapshot_invalid" for w in diag["warnings"]))
            self.assertEqual(diag["errors"], [])
            self.assertEqual(p["integrity"]["history_snapshots"]["skipped_invalid"], 1)

    def test_invalid_history_snapshot_strict_adds_errors(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "products").mkdir()
            bad = root / "runs" / "history" / "snapshots" / "bad_snap2"
            bad.mkdir(parents=True)
            (bad / "snapshot.json").write_text("{ not valid json", encoding="utf-8")

            p = build_dashboard_payload(root, strict=True)
            diag = p["diagnostics"]
            self.assertTrue(diag["errors"])
            self.assertTrue(any(e.get("code") == "strict_json" for e in diag["errors"]))

    def test_missing_economics_resource_warns(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "products").mkdir()
            p = build_dashboard_payload(root)
            codes = [w.get("code") for w in p["diagnostics"]["warnings"]]
            self.assertIn("economics_resources_missing", codes)


if __name__ == "__main__":
    unittest.main()
