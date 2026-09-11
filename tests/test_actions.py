"""Tests for action contracts, validation, and dangerous-pattern detection."""

from __future__ import annotations

import shutil
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from argus.actions.executor import dry_run
from argus.actions.models import ActionContract, LifecycleTransitionSpec
from argus.actions.validate import (
    dangerous_patterns,
    load_action_file,
    resolve_working_directory,
    validate_action_contract,
)
from argus.products.inventory import build_inventory

_REPO = Path(__file__).resolve().parents[1]
_FIXTURE_ACTION_PRODUCT = _REPO / "tests/fixtures/products/fixture_action_product"
_ANALYZE_FIXTURE_YAML = _REPO / "tests/fixtures/actions/analyze_product.yaml"


def _install_fixture_action_product(tmp_root: Path) -> Path:
    """Copy bundled minimal product into ``tmp_root/products/fixture_action_product/``."""
    dest = tmp_root / "products" / "fixture_action_product"
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(_FIXTURE_ACTION_PRODUCT, dest)
    return dest


def _minimal_product_yaml(
    pid: str,
    *,
    analyze_cmd: str = "./scripts/analyze.sh",
) -> str:
    return f"""
id: {pid}
name: T
owner:
  team: test
lifecycle:
  stage: grow
metrics:
  local_paths: [metrics/]
  primary: []
cost:
  monthly_usd: 1
  notes: ""
signals:
  - type: filesystem
    enabled: true
actions:
  start: "./scripts/s.sh"
  stop: "./scripts/s.sh"
  analyze: "{analyze_cmd}"
constraints:
  max_monthly_cost_usd: 10
  min_activity_threshold: 0
"""


def _write_minimal_product(root: Path, pid: str, *, analyze_cmd: str = "./scripts/analyze.sh") -> Path:
    pr = root / "products" / pid
    pr.mkdir(parents=True)
    (pr / "product.yaml").write_text(_minimal_product_yaml(pid, analyze_cmd=analyze_cmd), encoding="utf-8")
    (pr / "scripts").mkdir(parents=True)
    (pr / "scripts" / "s.sh").write_text("#!/bin/sh\necho\n", encoding="utf-8")
    (pr / "metrics").mkdir(parents=True)
    (pr / "metrics" / "d.txt").write_text("1", encoding="utf-8")
    if analyze_cmd == "./scripts/analyze.sh":
        (pr / "scripts" / "analyze.sh").write_text("#!/bin/sh\necho\n", encoding="utf-8")
    return pr


class TestActionValidation(unittest.TestCase):
    def test_fixture_analyze_validates_in_temp_repo(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _install_fixture_action_product(root)
            c, err = load_action_file(_ANALYZE_FIXTURE_YAML)
            self.assertIsNone(err)
            assert c is not None
            inv = build_inventory(root)
            errors = validate_action_contract(c, repo_root=root, inventory=inv)
            self.assertEqual(errors, [])

    def test_unknown_action_type(self) -> None:
        root = Path(__file__).resolve().parents[1]
        c = ActionContract(
            action_id="x",
            product_id="fixture_action_product",
            action_type="not_a_real_type",
            command="true",
            working_directory=".",
        )
        errors = validate_action_contract(c, repo_root=root)
        self.assertTrue(any("unknown action_type" in e for e in errors))

    def test_missing_product(self) -> None:
        root = Path(__file__).resolve().parents[1]
        c = ActionContract(
            action_id="x",
            product_id="definitely_missing_product_zz",
            action_type="analyze",
            command="true",
            working_directory=".",
        )
        errors = validate_action_contract(c, repo_root=root)
        self.assertTrue(any("not found in inventory" in e for e in errors))

    def test_missing_script_path(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_minimal_product(root, "p_miss", analyze_cmd="./scripts/does_not_exist_ever.sh")
            inv = build_inventory(root)
            c = ActionContract(
                action_id="x",
                product_id="p_miss",
                action_type="analyze",
                command="./scripts/does_not_exist_ever.sh",
                working_directory="products/p_miss",
            )
            errors = validate_action_contract(c, repo_root=root, inventory=inv)
            self.assertTrue(any("does not exist" in e for e in errors))

    def test_working_directory_escape_rejected(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_minimal_product(root, "p_esc")
            inv = build_inventory(root)
            c = ActionContract(
                action_id="x",
                product_id="p_esc",
                action_type="analyze",
                command="true",
                working_directory="../..",
            )
            errors = validate_action_contract(c, repo_root=root, inventory=inv)
            self.assertTrue(any("working_directory" in e for e in errors))

    def test_resolve_working_directory(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _install_fixture_action_product(root)
            ok, err = resolve_working_directory(root, "products/fixture_action_product")
            self.assertIsNone(err)
            assert ok is not None
            self.assertTrue((ok / "product.yaml").is_file())


class TestDangerousPatterns(unittest.TestCase):
    def test_rm_rf_flagged(self) -> None:
        root = Path(__file__).resolve().parents[1]
        d = dangerous_patterns(
            "rm -rf /tmp/x",
            repo_root=root,
            product_id="p",
            lifecycle=None,
        )
        self.assertTrue(any("rm" in x and "rf" in x for x in d))

    def test_git_reset_hard_flagged(self) -> None:
        root = Path(__file__).resolve().parents[1]
        d = dangerous_patterns(
            "git reset --hard",
            repo_root=root,
            product_id="p",
            lifecycle=None,
        )
        self.assertTrue(any("git reset --hard" in x for x in d))

    def test_product_dir_removal_flagged(self) -> None:
        root = Path(__file__).resolve().parents[1]
        d = dangerous_patterns(
            "rm -rf products/fixture_action_product",
            repo_root=root,
            product_id="fixture_action_product",
            lifecycle=None,
        )
        self.assertTrue(any("products/fixture_action_product" in x for x in d))

    def test_redirect_outside_repo_flagged(self) -> None:
        root = Path(__file__).resolve().parents[1]
        d = dangerous_patterns(
            "echo x > /etc/cron.d/bad",
            repo_root=root,
            product_id="p",
            lifecycle=None,
        )
        self.assertTrue(any("redirection outside" in x for x in d))

    def test_unsafe_lifecycle_transition_flagged(self) -> None:
        root = Path(__file__).resolve().parents[1]
        lt = LifecycleTransitionSpec(from_stage="grow", to_stage="validate")
        d = dangerous_patterns(
            "true",
            repo_root=root,
            product_id="p",
            lifecycle=lt,
        )
        self.assertTrue(any("unsafe lifecycle" in x for x in d))

    def test_unsafe_fixture_has_multiple_flags(self) -> None:
        root = Path(__file__).resolve().parents[1]
        path = root / "tests" / "fixtures" / "actions" / "unsafe_intentional.yaml"
        c, err = load_action_file(path)
        self.assertIsNone(err)
        assert c is not None
        d = dangerous_patterns(
            c.command,
            repo_root=root,
            product_id=c.product_id,
            lifecycle=c.lifecycle_transition,
        )
        self.assertGreaterEqual(len(d), 2)


class TestDryRun(unittest.TestCase):
    def test_dry_run_ok_for_analyze_fixture(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _install_fixture_action_product(root)
            c, err = load_action_file(_ANALYZE_FIXTURE_YAML)
            self.assertIsNone(err)
            assert c is not None
            inv = build_inventory(root)
            r = dry_run(c, repo_root=root, inventory=inv)
            self.assertTrue(r.ok)
            self.assertFalse(r.dangerous_flags)


if __name__ == "__main__":
    unittest.main()
