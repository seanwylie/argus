"""Signal Cursor review: prompt, validation, ingest persistence."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from argus.core.models.enums import SignalType
from argus.core.models.signal import SignalRecord
from argus.core.serialize import dumps_json
from argus.products.loader import load_yaml_file
from argus.products.validate import validate_manifest
from argus.signals.cursor_review import (
    PROVENANCE_SIGNAL_CURSOR_REVIEW,
    SIGNAL_CURSOR_REVIEW_SCHEMA,
    fingerprint_deterministic_signal_bundle,
    validate_signal_cursor_review_v1,
)
from argus.signals.persistence import save_collection
from argus.signals.review_ingest import (
    ingest_signal_cursor_review,
    load_signal_review_bundle,
    signal_review_bundle_path,
    signal_review_operator_summary,
)
from argus.signals.review_prompt import build_signal_review_prompt


def _valid_review_payload(pid: str) -> dict:
    return {
        "schema": SIGNAL_CURSOR_REVIEW_SCHEMA,
        "product_id": pid,
        "summary_lines": ["metrics adapter present; consider KPI alignment with product.yaml"],
        "findings": [
            {
                "title": "Instrumentation gap",
                "detail": "No automated test signal in bundle.",
                "severity": "info",
                "evidence_refs": ["products/p1/metrics/"],
            }
        ],
        "risks": [{"statement": "Blind spot: execution signals may be stale", "evidence_refs": []}],
        "enhancements": [
            {
                "title": "Add snapshot for primary KPI",
                "detail": "",
                "severity": "info",
                "evidence_refs": [],
            }
        ],
        "confidence": 0.7,
        "repo_evidence_refs": ["products/p1/product.yaml"],
        "limitations": ["static inspection only"],
        "provenance": PROVENANCE_SIGNAL_CURSOR_REVIEW,
    }


def _minimal_product_yaml(pid: str) -> str:
    return "\n".join(
        [
            f"id: {pid}",
            "name: P",
            "owner:",
            "  team: t",
            "lifecycle:",
            "  stage: idea",
            "metrics:",
            "  local_paths: []",
            "  primary: []",
            "cost:",
            "  monthly_usd: 0",
            "  notes: ''",
            "signals:",
            "  - type: filesystem",
            "    enabled: true",
            "actions:",
            '  start: "./scripts/s.sh"',
            '  stop: "./scripts/s.sh"',
            '  analyze: "./scripts/s.sh"',
            "constraints:",
            "  max_monthly_cost_usd: 1",
            "  min_activity_threshold: 0",
            "",
        ]
    )


class TestSignalCursorReview(unittest.TestCase):
    def test_validate_accepts_and_normalizes(self) -> None:
        p = _valid_review_payload("p1")
        out = validate_signal_cursor_review_v1(p, expected_product_id="p1")
        self.assertEqual(out["schema"], SIGNAL_CURSOR_REVIEW_SCHEMA)
        self.assertEqual(out["provenance"], PROVENANCE_SIGNAL_CURSOR_REVIEW)

    def test_validate_rejects_wrong_product_id(self) -> None:
        p = _valid_review_payload("p1")
        with self.assertRaises(ValueError) as ctx:
            validate_signal_cursor_review_v1(p, expected_product_id="other")
        self.assertIn("product_id mismatch", str(ctx.exception))

    def test_validate_rejects_bad_provenance(self) -> None:
        p = _valid_review_payload("p1")
        p["provenance"] = "wrong"
        with self.assertRaises(ValueError) as ctx:
            validate_signal_cursor_review_v1(p)
        self.assertIn("provenance", str(ctx.exception))

    def test_fingerprint_deterministic_stable(self) -> None:
        d = {
            "product_id": "p1",
            "collected_at_utc": "2020-01-01T00:00:00+00:00",
            "records": [{"id": "a", "signal_type": "metrics", "source": "m"}],
        }
        self.assertEqual(fingerprint_deterministic_signal_bundle(d), fingerprint_deterministic_signal_bundle(d))

    def test_prompt_generation(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pr = root / "products" / "p1"
            cfg = pr / "product.yaml"
            pr.mkdir(parents=True)
            cfg.write_text(_minimal_product_yaml("p1"), encoding="utf-8")
            (pr / "scripts").mkdir(parents=True)
            (pr / "scripts" / "s.sh").write_text("#!/bin/sh\n", encoding="utf-8")

            raw, err = load_yaml_file(cfg)
            self.assertIsNone(err)
            assert raw is not None
            res = validate_manifest(raw, repo_root=root, product_root=pr, config_path=cfg)
            self.assertIsNotNone(res.node)

            latest = root / "runs" / "signals" / "latest"
            latest.mkdir(parents=True)
            bundle = {
                "schema": "argus.signal_collection.v1",
                "product_id": "p1",
                "collected_at_utc": "2020-01-01T00:00:00+00:00",
                "repo_root": str(root),
                "record_count": 1,
                "records": [
                    {
                        "id": "sig-x",
                        "product_id": "p1",
                        "signal_type": "filesystem",
                        "source": "filesystem",
                        "observed_at": "2020-01-01T00:00:00+00:00",
                        "payload": {"ok": True},
                    }
                ],
            }
            (latest / "p1.json").write_text(dumps_json(bundle), encoding="utf-8")

            text = build_signal_review_prompt(root, "p1")
            self.assertIn(SIGNAL_CURSOR_REVIEW_SCHEMA, text)
            self.assertIn("p1", text)
            self.assertIn("runs/signals/review", text)
            self.assertIn(PROVENANCE_SIGNAL_CURSOR_REVIEW, text)

    def test_ingest_persistence_and_rerun_history(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pr = root / "products" / "p1"
            cfg = pr / "product.yaml"
            pr.mkdir(parents=True)
            cfg.write_text(_minimal_product_yaml("p1"), encoding="utf-8")
            (pr / "scripts").mkdir(parents=True)
            (pr / "scripts" / "s.sh").write_text("#!/bin/sh\n", encoding="utf-8")

            raw, err = load_yaml_file(cfg)
            self.assertIsNone(err)
            assert raw is not None
            res = validate_manifest(raw, repo_root=root, product_root=pr, config_path=cfg)
            self.assertIsNotNone(res.node)

            latest = root / "runs" / "signals" / "latest"
            latest.mkdir(parents=True)
            bundle = {
                "schema": "argus.signal_collection.v1",
                "product_id": "p1",
                "collected_at_utc": "2020-01-01T00:00:00+00:00",
                "repo_root": str(root),
                "record_count": 0,
                "records": [],
            }
            (latest / "p1.json").write_text(json.dumps(bundle), encoding="utf-8")

            p1 = _valid_review_payload("p1")
            out1 = ingest_signal_cursor_review(root, "p1", p1)
            path = signal_review_bundle_path(root, "p1")
            self.assertTrue(path.is_file())
            self.assertIn("signal_review", out1)
            self.assertEqual(out1["deterministic_signals_fingerprint"], fingerprint_deterministic_signal_bundle(bundle))

            loaded = load_signal_review_bundle(root, "p1")
            assert loaded is not None
            self.assertIn("ingest_history", loaded)

            p2 = dict(p1)
            p2["summary_lines"] = ["second review pass"]
            out2 = ingest_signal_cursor_review(root, "p1", p2)
            self.assertGreaterEqual(len(out2.get("ingest_history") or []), 1)
            self.assertEqual(
                out2["signal_review"]["summary_lines"],
                ["second review pass"],
            )

            with self.assertRaises(ValueError):
                ingest_signal_cursor_review(root, "p1", p2, overwrite=False)

    def test_deterministic_collect_does_not_delete_signal_review(self) -> None:
        """``save_collection`` / collect must not remove or overwrite the Cursor review sidecar."""
        from datetime import datetime, timezone

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pr = root / "products" / "p1"
            cfg = pr / "product.yaml"
            pr.mkdir(parents=True)
            cfg.write_text(_minimal_product_yaml("p1"), encoding="utf-8")
            (pr / "scripts").mkdir(parents=True)
            (pr / "scripts" / "s.sh").write_text("#!/bin/sh\n", encoding="utf-8")

            raw, err = load_yaml_file(cfg)
            self.assertIsNone(err)
            assert raw is not None
            res = validate_manifest(raw, repo_root=root, product_root=pr, config_path=cfg)
            self.assertIsNotNone(res.node)

            latest = root / "runs" / "signals" / "latest"
            latest.mkdir(parents=True)
            bundle = {
                "schema": "argus.signal_collection.v1",
                "product_id": "p1",
                "collected_at_utc": "2020-01-01T00:00:00+00:00",
                "repo_root": str(root),
                "record_count": 0,
                "records": [],
            }
            (latest / "p1.json").write_text(json.dumps(bundle), encoding="utf-8")

            ingest_signal_cursor_review(root, "p1", _valid_review_payload("p1"))
            rp = signal_review_bundle_path(root, "p1")
            snap = rp.read_text(encoding="utf-8")

            now = datetime.now(timezone.utc)
            save_collection(
                root,
                "p1",
                [
                    SignalRecord(
                        id="new-sig",
                        product_id="p1",
                        signal_type=SignalType.FILESYSTEM,
                        source="t",
                        observed_at=now,
                        payload={"x": 1},
                    )
                ],
            )
            self.assertTrue(rp.is_file())
            self.assertEqual(rp.read_text(encoding="utf-8"), snap)
            summ = signal_review_operator_summary(root, "p1")
            self.assertTrue(summ["signal_review_present"])
            self.assertIs(summ["signal_review_matches_deterministic_fingerprint"], False)


if __name__ == "__main__":
    unittest.main()
