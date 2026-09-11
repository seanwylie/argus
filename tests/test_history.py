"""Tests for portfolio history snapshots and deltas."""

from __future__ import annotations

import json
import textwrap
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from argus.core.serialize import dumps_json, to_jsonable
from argus.history.models import (
    PortfolioSnapshot,
    ProductSnapshot,
    portfolio_snapshot_from_dict,
)
from argus.history.snapshot import build_portfolio_snapshot, compute_snapshot_delta
from argus.history.storage import load_snapshot_file, write_snapshot_json
from argus.history.summarize import load_product_timeline


def _write(p: Path, content: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(textwrap.dedent(content).strip() + "\n", encoding="utf-8")


def _minimal_valid_yaml(product_id: str = "hist_p") -> str:
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
      monthly_usd: 12.5
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


class TestHistorySnapshot(unittest.TestCase):
    def test_build_snapshot_with_missing_artifacts(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pr = root / "products" / "hist_p"
            _write(pr / "product.yaml", _minimal_valid_yaml())
            (pr / "scripts").mkdir(parents=True)
            (pr / "scripts" / "s.sh").write_text("#!/bin/sh\n", encoding="utf-8")

            snap = build_portfolio_snapshot(
                root,
                snapshot_id="snap1",
                observed_at_utc="2026-04-01T00:00:00+00:00",
                label=None,
            )
            self.assertEqual(snap.snapshot_id, "snap1")
            self.assertEqual(len(snap.products), 1)
            p = snap.products[0]
            self.assertEqual(p.product_id, "hist_p")
            self.assertEqual(p.active_findings_count, 0)
            self.assertEqual(p.monthly_cost_usd, 12.5)
            self.assertIn("findings_latest", p.source_paths)

    def test_write_load_roundtrip(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pr = root / "products" / "hist_p"
            _write(pr / "product.yaml", _minimal_valid_yaml())
            (pr / "scripts").mkdir(parents=True)
            (pr / "scripts" / "s.sh").write_text("#!/bin/sh\n", encoding="utf-8")

            snap = build_portfolio_snapshot(
                root,
                snapshot_id="snap1",
                observed_at_utc="2026-04-01T00:00:00+00:00",
                label=None,
            )
            out = write_snapshot_json(root, snap)
            self.assertTrue(out.is_file())
            loaded = load_snapshot_file(out)
            self.assertEqual(loaded.snapshot_id, snap.snapshot_id)
            self.assertEqual(len(loaded.products), 1)
            self.assertEqual(loaded.products[0].product_id, "hist_p")

            latest = json.loads((root / "runs" / "history" / "latest.json").read_text())
            self.assertEqual(latest["snapshot_id"], "snap1")

    def test_delta_generation(self) -> None:
        older = PortfolioSnapshot(
            snapshot_id="a",
            observed_at_utc="2026-01-01T00:00:00+00:00",
            label=None,
            repo_root="/r",
            products=[
                ProductSnapshot(
                    snapshot_id="a",
                    product_id="p1",
                    observed_at_utc="2026-01-01T00:00:00+00:00",
                    state="idea",
                    status="x",
                    lifecycle_stage="idea",
                    monthly_cost_usd=10.0,
                    last_signal_at=None,
                    active_findings_count=2,
                    findings_by_severity={"high": 1},
                    top_recommended_action="old",
                    priority_score=1.0,
                    top_confidence=0.5,
                    escalation_count=0,
                    lifecycle_scores={},
                    kill_candidate=False,
                    source_paths={},
                )
            ],
        )
        newer = PortfolioSnapshot(
            snapshot_id="b",
            observed_at_utc="2026-02-01T00:00:00+00:00",
            label=None,
            repo_root="/r",
            products=[
                ProductSnapshot(
                    snapshot_id="b",
                    product_id="p1",
                    observed_at_utc="2026-02-01T00:00:00+00:00",
                    state="idea",
                    status="x",
                    lifecycle_stage="build",
                    monthly_cost_usd=15.0,
                    last_signal_at="2026-02-01T00:00:00+00:00",
                    active_findings_count=5,
                    findings_by_severity={"high": 2},
                    top_recommended_action="new",
                    priority_score=2.0,
                    top_confidence=0.8,
                    escalation_count=1,
                    lifecycle_scores={},
                    kill_candidate=True,
                    source_paths={},
                )
            ],
        )
        d = compute_snapshot_delta(older, newer)
        self.assertEqual(d.from_snapshot_id, "a")
        self.assertEqual(d.to_snapshot_id, "b")
        pd = d.product_deltas[0]
        self.assertEqual(pd.active_findings_count_delta, 3)
        self.assertEqual(pd.monthly_cost_usd_delta, 5.0)
        self.assertTrue(pd.top_recommended_action_changed)
        self.assertTrue(pd.lifecycle_stage_changed)
        self.assertEqual(pd.escalation_count_delta, 1)
        self.assertTrue(pd.kill_candidate_changed)

    def test_delta_new_product(self) -> None:
        older = PortfolioSnapshot(
            snapshot_id="a",
            observed_at_utc="2026-01-01T00:00:00+00:00",
            label=None,
            repo_root="/r",
            products=[],
        )
        newer = PortfolioSnapshot(
            snapshot_id="b",
            observed_at_utc="2026-02-01T00:00:00+00:00",
            label=None,
            repo_root="/r",
            products=[
                ProductSnapshot(
                    snapshot_id="b",
                    product_id="newp",
                    observed_at_utc="2026-02-01T00:00:00+00:00",
                    state="idea",
                    status="",
                    lifecycle_stage="idea",
                    monthly_cost_usd=None,
                    last_signal_at=None,
                    active_findings_count=0,
                    findings_by_severity={},
                    top_recommended_action="",
                    priority_score=None,
                    top_confidence=None,
                    escalation_count=0,
                    lifecycle_scores={},
                    kill_candidate=False,
                    source_paths={"product_yaml": "products/newp/product.yaml"},
                )
            ],
        )
        d = compute_snapshot_delta(older, newer)
        self.assertEqual(len(d.product_deltas), 1)
        self.assertEqual(d.product_deltas[0].active_findings_count_delta, 0)

    def test_snapshot_json_serialization(self) -> None:
        snap = PortfolioSnapshot(
            snapshot_id="x",
            observed_at_utc="2026-01-01T00:00:00+00:00",
            label="n",
            repo_root="/repo",
            products=[],
        )
        raw = dumps_json(to_jsonable(snap))
        back = portfolio_snapshot_from_dict(json.loads(raw))
        self.assertEqual(back.snapshot_id, "x")
        self.assertEqual(back.label, "n")

    def test_product_timeline(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pr = root / "products" / "hist_p"
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

            tl = load_product_timeline(root, "hist_p")
            self.assertEqual(len(tl), 2)
            self.assertEqual(tl[0][0], "t2")


if __name__ == "__main__":
    unittest.main()
