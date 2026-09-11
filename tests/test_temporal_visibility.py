"""Temporal pipeline visibility (dashboard payload + doctor report)."""

from __future__ import annotations

import json
import tempfile
import textwrap
import unittest
from pathlib import Path

from argus.dashboard.data import build_dashboard_payload
from argus.products.inventory import build_inventory
from argus.temporal.visibility import (
    build_doctor_temporal_report,
    compute_product_temporal_visibility,
)


def _write(p: Path, content: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(textwrap.dedent(content).strip() + "\n", encoding="utf-8")


def _minimal_yaml(pid: str) -> str:
    return f"""
    id: {pid}
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


def _minimal_findings_bundle(pid: str, root: Path) -> None:
    p = root / "runs" / "findings" / "latest" / f"{pid}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps(
            {
                "schema": "argus.findings_bundle.v1",
                "product_id": pid,
                "generated_at_utc": "2026-04-01T00:00:00+00:00",
                "repo_root": str(root),
                "finding_count": 0,
                "findings": [],
            }
        ),
        encoding="utf-8",
    )


def _minimal_decisions(pid: str, root: Path) -> None:
    p = root / "runs" / "decisions" / "latest" / f"{pid}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps({"candidates": [], "lifecycle": {}}),
        encoding="utf-8",
    )


class TestTemporalDoctor(unittest.TestCase):
    def test_missing_signals_reported(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            pr = root / "products" / "miss_p"
            _write(pr / "product.yaml", _minimal_yaml("miss_p"))
            (pr / "scripts").mkdir(parents=True)
            (pr / "scripts" / "s.sh").write_text("#!/bin/sh\ntrue\n", encoding="utf-8")

            inv = build_inventory(root)
            rep = build_doctor_temporal_report(root, inv.valid)
            codes = [c["code"] for c in rep["checks"]]
            self.assertIn("missing_signals", codes)
            self.assertIn("pipeline_incomplete_while_signals_missing", codes)
            self.assertNotIn("missing_findings", codes)
            self.assertNotIn("missing_decisions", codes)

    def test_stale_signal_age_warns(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            pr = root / "products" / "stale_p"
            _write(pr / "product.yaml", _minimal_yaml("stale_p"))
            (pr / "scripts").mkdir(parents=True)
            (pr / "scripts" / "s.sh").write_text("#!/bin/sh\ntrue\n", encoding="utf-8")

            sig = root / "runs" / "signals" / "latest" / "stale_p.json"
            sig.parent.mkdir(parents=True, exist_ok=True)
            sig.write_text(
                json.dumps(
                    {
                        "collected_at_utc": "2020-01-01T00:00:00+00:00",
                        "records": [],
                    }
                ),
                encoding="utf-8",
            )
            _minimal_findings_bundle("stale_p", root)
            _minimal_decisions("stale_p", root)

            inv = build_inventory(root)
            rep = build_doctor_temporal_report(root, inv.valid)
            codes = [c["code"] for c in rep["checks"]]
            self.assertIn("signals_stale_by_age", codes)

    def test_malformed_temporal_bundle_flag(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            pr = root / "products" / "mal_p"
            _write(pr / "product.yaml", _minimal_yaml("mal_p"))
            (pr / "scripts").mkdir(parents=True)
            (pr / "scripts" / "s.sh").write_text("#!/bin/sh\ntrue\n", encoding="utf-8")

            sig = root / "runs" / "signals" / "latest" / "mal_p.json"
            sig.parent.mkdir(parents=True, exist_ok=True)
            sig.write_text(
                json.dumps({"collected_at_utc": "2026-04-01T00:00:00+00:00", "records": []}),
                encoding="utf-8",
            )
            _minimal_findings_bundle("mal_p", root)
            _minimal_decisions("mal_p", root)

            tp = root / "runs" / "temporal" / "latest" / "mal_p.json"
            tp.parent.mkdir(parents=True, exist_ok=True)
            tp.write_text("{not json", encoding="utf-8")

            inv = build_inventory(root)
            vis = compute_product_temporal_visibility(root, "mal_p", inv.valid["mal_p"].node)
            self.assertIn("temporal_bundle_malformed", vis["flags"])

            rep = build_doctor_temporal_report(root, inv.valid)
            codes = [c["code"] for c in rep["checks"]]
            self.assertIn("temporal_bundle_malformed", codes)

    def test_missing_temporal_sidecar_flag(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            pr = root / "products" / "side_p"
            _write(pr / "product.yaml", _minimal_yaml("side_p"))
            (pr / "scripts").mkdir(parents=True)
            (pr / "scripts" / "s.sh").write_text("#!/bin/sh\ntrue\n", encoding="utf-8")

            sig = root / "runs" / "signals" / "latest" / "side_p.json"
            sig.parent.mkdir(parents=True, exist_ok=True)
            sig.write_text(
                json.dumps(
                    {
                        "collected_at_utc": "2026-04-01T00:00:00+00:00",
                        "records": [],
                    }
                ),
                encoding="utf-8",
            )
            _minimal_findings_bundle("side_p", root)
            _minimal_decisions("side_p", root)

            inv = build_inventory(root)
            vis = compute_product_temporal_visibility(root, "side_p", inv.valid["side_p"].node)
            self.assertIn("missing_temporal_bundle", vis["flags"])

            rep = build_doctor_temporal_report(root, inv.valid)
            codes = [c["code"] for c in rep["checks"]]
            self.assertIn("missing_temporal_bundle", codes)


class TestTemporalDoctorPortfolio(unittest.TestCase):
    def test_missing_portfolio_json_in_temporal_report(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "products").mkdir()
            fd = root / "runs" / "findings" / "latest"
            fd.mkdir(parents=True)
            (fd / "orphan.json").write_text("{}", encoding="utf-8")
            inv = build_inventory(root)
            rep = build_doctor_temporal_report(root, inv.valid)
            codes = [c["code"] for c in rep["checks"]]
            self.assertIn("missing_portfolio_json", codes)


class TestTemporalDoctorClockAndShape(unittest.TestCase):
    def test_future_collected_at_flags_integrity(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            pr = root / "products" / "ft_p"
            _write(pr / "product.yaml", _minimal_yaml("ft_p"))
            (pr / "scripts").mkdir(parents=True)
            (pr / "scripts" / "s.sh").write_text("#!/bin/sh\ntrue\n", encoding="utf-8")
            sig = root / "runs" / "signals" / "latest" / "ft_p.json"
            sig.parent.mkdir(parents=True)
            sig.write_text(
                json.dumps(
                    {
                        "schema": "argus.signal_collection.v1",
                        "product_id": "ft_p",
                        "collected_at_utc": "2099-12-31T00:00:00+00:00",
                        "repo_root": str(root),
                        "record_count": 0,
                        "records": [],
                    }
                ),
                encoding="utf-8",
            )
            _minimal_findings_bundle("ft_p", root)
            _minimal_decisions("ft_p", root)
            inv = build_inventory(root)
            rep = build_doctor_temporal_report(root, inv.valid)
            codes = [c["code"] for c in rep["checks"]]
            self.assertIn("collection_timestamp_in_future", codes)

    def test_records_must_be_array(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            pr = root / "products" / "shape_p"
            _write(pr / "product.yaml", _minimal_yaml("shape_p"))
            (pr / "scripts").mkdir(parents=True)
            (pr / "scripts" / "s.sh").write_text("#!/bin/sh\ntrue\n", encoding="utf-8")
            sig = root / "runs" / "signals" / "latest" / "shape_p.json"
            sig.parent.mkdir(parents=True)
            sig.write_text(
                json.dumps(
                    {
                        "product_id": "shape_p",
                        "collected_at_utc": "2026-04-01T00:00:00+00:00",
                        "records": "not-a-list",
                    }
                ),
                encoding="utf-8",
            )
            inv = build_inventory(root)
            rep = build_doctor_temporal_report(root, inv.valid)
            codes = [c["code"] for c in rep["checks"]]
            self.assertIn("signals_bundle_validation", codes)


class TestTemporalDashboard(unittest.TestCase):
    def test_payload_includes_temporal_visibility(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            pr = root / "products" / "dash_p"
            _write(pr / "product.yaml", _minimal_yaml("dash_p"))
            (pr / "scripts").mkdir(parents=True)
            (pr / "scripts" / "s.sh").write_text("#!/bin/sh\ntrue\n", encoding="utf-8")

            p = build_dashboard_payload(root)
            self.assertIn("temporal", p)
            self.assertEqual(p["temporal"]["schema"], "argus.dashboard_temporal.v2")
            prod = p["products"][0]
            self.assertIn("temporal_visibility", prod)
            tv = prod["temporal_visibility"]
            self.assertIn("overall", tv)
            self.assertIn("flags", tv)
            self.assertIn("temporal_bundle", tv)
            self.assertIn("summary_counts", p["temporal"])
            self.assertIn("recent_temporal_findings", p["temporal"])


if __name__ == "__main__":
    unittest.main()
