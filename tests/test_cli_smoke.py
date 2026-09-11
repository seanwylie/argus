"""CLI entrypoints respond without crashing (no network)."""

from __future__ import annotations

import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

_REPO = Path(__file__).resolve().parents[1]
_FIXTURE_PRODUCT = _REPO / "tests/fixtures/products/fixture_action_product"


def _run(argv: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "argus.cli.main", *argv],
        cwd=_REPO,
        capture_output=True,
        text=True,
        timeout=60,
    )


class TestCliSmoke(unittest.TestCase):
    def test_top_level_help_exits_zero(self) -> None:
        r = _run(["--help"])
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        self.assertIn("products", r.stdout)
        self.assertIn("strategy", r.stdout)
        self.assertIn("doctor", r.stdout)
        self.assertIn("simulate", r.stdout)
        self.assertIn("ideas", r.stdout)

    def test_ideas_help_exits_zero(self) -> None:
        r = _run(["ideas", "--help"])
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        self.assertIn("generate", r.stdout.lower())

    def test_simulate_help_exits_zero(self) -> None:
        r = _run(["simulate", "--help"])
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        self.assertIn("experiment", r.stdout.lower())

    def test_products_list_exits_zero(self) -> None:
        r = _run(["products", "list"])
        self.assertEqual(r.returncode, 0, msg=r.stderr)

    def test_products_list_empty_inventory_prints_next_step_hint(self) -> None:
        from tempfile import TemporaryDirectory

        # products_dir must live under the repo root for inventory path resolution.
        with TemporaryDirectory(dir=str(_REPO)) as tmp:
            root = Path(tmp)
            pdir = root / "products"
            pdir.mkdir(parents=True)
            r = subprocess.run(
                [sys.executable, "-m", "argus.cli.main", "products", "list", "--products-dir", str(pdir)],
                cwd=_REPO,
                capture_output=True,
                text=True,
                timeout=60,
            )
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            self.assertIn("No product.yaml manifests found", r.stderr)
            self.assertIn("argus products create", r.stderr)

    def test_strategy_show_exits_zero(self) -> None:
        r = _run(["strategy", "show"])
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        self.assertIn("strategy", r.stdout.lower())

    def test_doctor_json_exits_zero(self) -> None:
        r = _run(["doctor", "--json"])
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        self.assertIn("inventory", r.stdout)

    def test_orchestration_portfolio_priority_trends_json_exits_zero(self) -> None:
        r = _run(["orchestration", "portfolio-priority-trends", "--json", "--no-write"])
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        self.assertIn("argus.portfolio_priority_trends.v1", r.stdout)

    def test_portfolio_allocate_empty_inventory_exits_nonzero(self) -> None:
        """Empty ``products/`` is valid; allocate has nothing to distribute."""
        with TemporaryDirectory(dir=str(_REPO)) as tmp:
            pdir = Path(tmp) / "products"
            pdir.mkdir(parents=True)
            r = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "argus.cli.main",
                    "portfolio",
                    "allocate",
                    "--products-dir",
                    str(pdir),
                ],
                cwd=_REPO,
                capture_output=True,
                text=True,
                timeout=60,
            )
            self.assertEqual(r.returncode, 1, msg=r.stderr)
            self.assertIn("No valid products in inventory", r.stderr)

    def test_portfolio_allocate_exits_zero_when_product_present_in_override_dir(self) -> None:
        with TemporaryDirectory(dir=str(_REPO)) as tmp:
            pdir = Path(tmp) / "products"
            pdir.mkdir(parents=True)
            shutil.copytree(_FIXTURE_PRODUCT, pdir / "fixture_action_product")
            r = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "argus.cli.main",
                    "portfolio",
                    "allocate",
                    "--products-dir",
                    str(pdir),
                ],
                cwd=_REPO,
                capture_output=True,
                text=True,
                timeout=60,
            )
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            self.assertIn("Portfolio attention allocation", r.stdout)

    def test_planning_actions_exits_zero(self) -> None:
        r = _run(["planning", "actions"])
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        self.assertIn("ActionContracts", r.stdout)

    def test_self_audit_exits_zero(self) -> None:
        r = _run(["self", "audit", "--json"])
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        self.assertIn("argus.self_audit.v1", r.stdout)

    def test_autonomy_shutdown_dry_run_json(self) -> None:
        r = _run(["autonomy", "shutdown", "--all-candidates", "--json"])
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        self.assertIn("argus.autonomy.shutdown_batch.v1", r.stdout)

    def test_autonomy_spawn_json_exits_zero(self) -> None:
        r = _run(["autonomy", "spawn", "--json"])
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        self.assertIn('"requires_approval_to_apply": true', r.stdout)
        self.assertIn('"proposed_slug":', r.stdout)
        self.assertIn('"signals":', r.stdout)


if __name__ == "__main__":
    unittest.main()
