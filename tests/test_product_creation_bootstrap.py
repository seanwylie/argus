"""Product creation bootstrap (``argus.product_creation_bootstrap.v1``)."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from argus.products.creation_bootstrap import (
    PRODUCT_CREATION_BOOTSTRAP_SCHEMA,
    creation_bootstrap_dir,
    evaluate_product_creation_bootstrap,
    run_product_creation_bootstrap,
)
from argus.products.scaffold import create_product_scaffold


def _write_mission(root: Path) -> None:
    cfg = root / "config" / "mission_profiles.yaml"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(
        """schema: argus.mission_registry.v1
default_mission_id: revenue
profiles:
  revenue:
    id: revenue
    primary_objective: Maximize sustainable revenue.
    drivers: []
    risk_posture: moderate
    weights:
      revenue_alignment: 1.0
""",
        encoding="utf-8",
    )


class TestProductCreationBootstrap(unittest.TestCase):
    def test_bootstrap_writes_artifacts(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_mission(root)
            code, _msg, _g = create_product_scaffold(
                root, "boot-art", template_type="content_stream", init_git=False
            )
            self.assertEqual(code, 0)
            out = run_product_creation_bootstrap(
                root,
                product_id="boot-art",
                minimal=True,
                dry_run=False,
                write_artifacts=True,
            )
            self.assertTrue(out.get("ok"), msg=out.get("error"))
            self.assertEqual(out["schema"], PRODUCT_CREATION_BOOTSTRAP_SCHEMA)
            d = creation_bootstrap_dir(root)
            self.assertTrue((d / "latest.json").is_file())
            self.assertTrue((d / "latest.md").is_file())
            raw = json.loads((d / "latest.json").read_text(encoding="utf-8"))
            self.assertIn("steps_executed", raw)
            self.assertIn("artifacts_created", raw)
            self.assertIn("initial_direction_summary", raw)

    def test_dry_run_no_bootstrap_artifacts(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_mission(root)
            _, _, _ = create_product_scaffold(
                root, "boot-dry", template_type="content_stream", init_git=False
            )
            run_product_creation_bootstrap(
                root,
                product_id="boot-dry",
                minimal=True,
                dry_run=True,
                write_artifacts=True,
            )
            d = creation_bootstrap_dir(root)
            self.assertFalse(d.exists())

    def test_no_save_skips_bootstrap_artifact_dir(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_mission(root)
            _, _, _ = create_product_scaffold(
                root, "boot-ns", template_type="content_stream", init_git=False
            )
            out = run_product_creation_bootstrap(
                root,
                product_id="boot-ns",
                minimal=True,
                dry_run=False,
                write_artifacts=False,
            )
            self.assertTrue(out.get("ok"))
            self.assertFalse(creation_bootstrap_dir(root).exists())

    def test_invalid_product_id(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_mission(root)
            out = evaluate_product_creation_bootstrap(root, "does-not-exist", dry_run=False)
            self.assertFalse(out.get("ok"))
            self.assertIn("error", out)

    def test_minimal_vs_full_steps(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_mission(root)
            _, _, _ = create_product_scaffold(
                root, "boot-mode", template_type="content_stream", init_git=False
            )
            m = evaluate_product_creation_bootstrap(root, "boot-mode", minimal=True, dry_run=False)
            f = evaluate_product_creation_bootstrap(root, "boot-mode", minimal=False, dry_run=False)
            self.assertTrue(m.get("ok"))
            self.assertTrue(f.get("ok"))
            m_steps = {s["step"] for s in m["steps_executed"]}
            f_steps = {s["step"] for s in f["steps_executed"]}
            self.assertNotIn("decisions_generate", m_steps)
            self.assertNotIn("ideas_pipeline", m_steps)
            self.assertIn("decisions_generate", f_steps)
            self.assertIn("ideas_pipeline", f_steps)


if __name__ == "__main__":
    unittest.main()
