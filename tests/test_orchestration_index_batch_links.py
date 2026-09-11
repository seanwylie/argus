"""Additive batch advancement pointers on orchestration index.json."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from argus.orchestrator.state_pass import (
    attach_batch_advancement_links_to_index_payload,
    emit_orchestration_batch,
    orchestration_batch_advancement_path,
)


class TestOrchestrationIndexBatchLinks(unittest.TestCase):
    def test_emit_index_null_when_no_batch_artifact(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            payload, path = emit_orchestration_batch(root, ["x"], write=False)
            self.assertIsNone(path)
            self.assertIsNone(payload.get("batch_advancement_artifact_path_repo_relative"))
            self.assertIsNone(payload.get("batch_advancement_selected_advancement_path_repo_relative"))

    def test_index_links_deterministic_repo_relative(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            batch_p = orchestration_batch_advancement_path(root)
            batch_p.parent.mkdir(parents=True, exist_ok=True)
            batch_p.write_text(
                json.dumps(
                    {
                        "advancement_artifact_path_repo_relative": "runs/orchestration/latest/advancements/p.json",
                    }
                ),
                encoding="utf-8",
            )
            base: dict[str, object] = {"schema": "argus.orchestration_index.v1", "products": []}
            out = attach_batch_advancement_links_to_index_payload(root, base)
            self.assertEqual(
                out.get("batch_advancement_artifact_path_repo_relative"),
                "runs/orchestration/latest/batch_advancement.json",
            )
            self.assertEqual(
                out.get("batch_advancement_selected_advancement_path_repo_relative"),
                "runs/orchestration/latest/advancements/p.json",
            )

    def test_batch_without_advancement_path_selected_null(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            batch_p = orchestration_batch_advancement_path(root)
            batch_p.parent.mkdir(parents=True, exist_ok=True)
            batch_p.write_text(json.dumps({"selected_product_id": None}), encoding="utf-8")
            out = attach_batch_advancement_links_to_index_payload(root, {"schema": "x", "products": []})
            self.assertEqual(out.get("batch_advancement_artifact_path_repo_relative"), "runs/orchestration/latest/batch_advancement.json")
            self.assertIsNone(out.get("batch_advancement_selected_advancement_path_repo_relative"))

    def test_emit_write_includes_links_when_batch_exists(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "products" / "x").mkdir(parents=True)
            (root / "products" / "x" / "product.yaml").write_text(
                "id: x\nname: T\nowner:\n  team: t\nlifecycle:\n  stage: idea\n"
                "metrics:\n  local_paths: []\n  primary: []\n"
                "cost:\n  monthly_usd: 1\n  notes: \"\"\n"
                "signals: []\n"
                "actions:\n  start: ./s\n  stop: ./s\n  analyze: ./s\n"
                "constraints:\n  max_monthly_cost_usd: 10\n  min_activity_threshold: 0\n",
                encoding="utf-8",
            )
            batch_p = orchestration_batch_advancement_path(root)
            batch_p.parent.mkdir(parents=True, exist_ok=True)
            batch_p.write_text(
                json.dumps({"advancement_artifact_path_repo_relative": "runs/orchestration/latest/advancements/x.json"}),
                encoding="utf-8",
            )
            _, idx_path = emit_orchestration_batch(root, ["x"], write=True)
            assert idx_path is not None
            idx = json.loads(idx_path.read_text(encoding="utf-8"))
            self.assertEqual(
                idx.get("batch_advancement_artifact_path_repo_relative"),
                "runs/orchestration/latest/batch_advancement.json",
            )
            self.assertEqual(
                idx.get("batch_advancement_selected_advancement_path_repo_relative"),
                "runs/orchestration/latest/advancements/x.json",
            )


if __name__ == "__main__":
    unittest.main()
