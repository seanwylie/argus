"""Tests for product scaffold generation."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from argus.products.inventory import build_inventory
from argus.products.loader import load_yaml_file
from argus.products.scaffold import (
    TEMPLATE_TYPES,
    build_product_yaml_payload,
    create_product_scaffold,
    normalize_product_slug,
)


class TestNormalize(unittest.TestCase):
    def test_slug(self) -> None:
        self.assertEqual(normalize_product_slug("My Cool App"), "my-cool-app")
        self.assertEqual(normalize_product_slug("  foo_bar  "), "foo-bar")

    def test_invalid(self) -> None:
        with self.assertRaises(ValueError):
            normalize_product_slug("!!!")


class TestScaffoldGeneration(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "products").mkdir(parents=True)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_basic_generation(self) -> None:
        code, msg, git_info = create_product_scaffold(self.root, "fancy-widget", template_type="content_stream")
        self.assertEqual(code, 0, msg)
        pr = Path(msg)
        self.assertTrue((pr / "product.yaml").is_file())
        self.assertTrue((pr / "scripts" / "start.sh").is_file())
        self.assertTrue((pr / "scripts" / "stop.sh").is_file())
        self.assertTrue((pr / "scripts" / "analyze.sh").is_file())
        self.assertTrue((pr / "metrics").is_dir())
        self.assertTrue((pr / "app" / ".gitkeep").is_file())
        self.assertTrue((pr / "config" / ".gitkeep").is_file())
        self.assertTrue((pr / "README.md").is_file())
        self.assertTrue((pr / "argus.policy.yaml").is_file())
        if __import__("shutil").which("git"):
            self.assertTrue((pr / ".git").exists(), git_info)
            self.assertTrue(git_info.get("git_init_ok"), git_info)
            self.assertTrue(git_info.get("initial_commit_ok"), git_info)

        inv = build_inventory(self.root)
        self.assertIn("fancy-widget", inv.valid)

    def test_type_specific_metrics(self) -> None:
        code, msg, _gi = create_product_scaffold(self.root, "saas-one", template_type="micro_saas")
        self.assertEqual(code, 0, msg)
        raw, err = load_yaml_file(Path(msg) / "product.yaml")
        self.assertIsNone(err)
        assert raw is not None
        primary = (raw.get("metrics") or {}).get("primary") or []
        self.assertIn("mrr", primary)
        self.assertIn("activation_rate", primary)

        code2, msg2, _g2 = create_product_scaffold(self.root, "api-one", template_type="utility_api")
        self.assertEqual(code2, 0, msg2)
        raw2, _ = load_yaml_file(Path(msg2) / "product.yaml")
        assert raw2 is not None
        primary2 = (raw2.get("metrics") or {}).get("primary") or []
        self.assertIn("requests_per_day", primary2)
        self.assertIn("error_rate", primary2)

    def test_duplicate_without_force(self) -> None:
        code, _, _g = create_product_scaffold(self.root, "dup-test", template_type="static_site")
        self.assertEqual(code, 0)
        code2, msg2, _ = create_product_scaffold(self.root, "dup-test", template_type="static_site")
        self.assertEqual(code2, 1)
        self.assertIn("already exists", msg2)

    def test_force_replaces(self) -> None:
        code, msg, _ = create_product_scaffold(self.root, "replace-me", template_type="content_stream")
        self.assertEqual(code, 0)
        (Path(msg) / "README.md").write_text("stale\n", encoding="utf-8")
        code2, msg2, _ = create_product_scaffold(
            self.root, "replace-me", template_type="mobile_companion", force=True
        )
        self.assertEqual(code2, 0, msg2)
        text = (Path(msg2) / "README.md").read_text(encoding="utf-8")
        self.assertIn("mobile_companion", text)
        self.assertNotEqual(text.strip(), "stale")

    def test_create_respects_no_git(self) -> None:
        code, msg, gi = create_product_scaffold(
            self.root, "no-git-prod", template_type="content_stream", init_git=False
        )
        self.assertEqual(code, 0, msg)
        pr = Path(msg)
        self.assertFalse((pr / ".git").exists())
        self.assertEqual(gi, {})

    def test_payload_covers_template_types(self) -> None:
        for t in sorted(TEMPLATE_TYPES):
            p = build_product_yaml_payload(
                product_id="x",
                display_name="X",
                template_type=t,
            )
            self.assertEqual(p["type"], t)
            self.assertTrue((p.get("metrics") or {}).get("primary"))


if __name__ == "__main__":
    unittest.main()
