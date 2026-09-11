"""Tests for evidence-backed product shape classification (importer)."""

from __future__ import annotations

import textwrap
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from argus.importer.classification import classify_product_shape, shape_dependency_description
from argus.importer.discover import scan_repo


def _write(p: Path, content: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(textwrap.dedent(content).strip() + "\n", encoding="utf-8")


class TestClassifyProductShape(unittest.TestCase):
    def test_mixed_app_py_and_package_json(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(
                root / "pyproject.toml",
                '[project]\nname = "x"\nversion = "0"\n[project.scripts]\nx = "x:main"\n',
            )
            _write(root / "package.json", '{"name":"x","version":"1"}')
            facts = scan_repo(root, product_id="x", github_url="https://github.com/o/r")
            r = classify_product_shape(facts)
            self.assertEqual(r.label, "mixed_app")
            self.assertIn("Python", " ".join(r.evidence))
            self.assertIn("package.json", " ".join(r.evidence))

    def test_python_cli_scripts(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(
                root / "pyproject.toml",
                '[project]\nname = "cli"\nversion = "0"\n[project.scripts]\ncli = "cli:main"\n',
            )
            facts = scan_repo(root, product_id="cli", github_url="https://github.com/o/r")
            r = classify_product_shape(facts)
            self.assertEqual(r.label, "python_cli")
            self.assertTrue(any("Console script" in e for e in r.evidence))

    def test_js_frontend_vite(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root / "package.json", '{"name":"fe","scripts":{"dev":"vite"}}')
            _write(root / "vite.config.ts", "export default {}\n")
            (root / "src").mkdir()
            facts = scan_repo(root, product_id="fe", github_url="https://github.com/o/r")
            r = classify_product_shape(facts)
            self.assertEqual(r.label, "js_frontend")
            self.assertIn("vite.config", " ".join(r.evidence).lower())

    def test_static_site_index_html(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root / "index.html", "<!doctype html><title>x</title>")
            facts = scan_repo(root, product_id="st", github_url="https://github.com/o/r")
            r = classify_product_shape(facts)
            self.assertEqual(r.label, "static_site")
            self.assertIn("index.html", " ".join(r.evidence))

    def test_python_service_docker(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(
                root / "pyproject.toml",
                '[project]\nname = "svc"\nversion = "0"\n',
            )
            _write(root / "Dockerfile", "FROM python:3.12\n")
            facts = scan_repo(root, product_id="svc", github_url="https://github.com/o/r")
            r = classify_product_shape(facts)
            self.assertEqual(r.label, "python_service")
            self.assertTrue(any("Dockerfile" in e or "docker" in e.lower() for e in r.evidence))

    def test_shape_dependency_description_tuned(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root / "package.json", "{}")
            facts = scan_repo(root, product_id="j", github_url="https://github.com/o/r")
            sh = classify_product_shape(facts)
            d = shape_dependency_description(facts, sh)
            self.assertIn("js_frontend", d.lower())
            self.assertIn("heuristic", d.lower())


if __name__ == "__main__":
    unittest.main()
