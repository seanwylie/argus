"""Tests for execution sandbox (working directory + command policy)."""

from __future__ import annotations

import textwrap
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from argus.actions.executor import dry_run
from argus.actions.models import ActionContract
from argus.execution.engine import ExecutionBlocked, ensure_execution_allowed
from argus.execution.sandbox import (
    sandbox_command_errors,
    validate_execution_sandbox,
)
from argus.products.inventory import build_inventory
from argus.project_permissions.schema import PHASE1_KEYS


def _write(p: Path, content: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(textwrap.dedent(content).strip() + "\n", encoding="utf-8")


def _product(root: Path, pid: str) -> None:
    pr = root / "products" / pid
    _write(
        pr / "product.yaml",
        f"""
        id: {pid}
        name: {pid}
        owner:
          team: test
        lifecycle:
          stage: validate
        metrics:
          local_paths: []
          primary: []
        cost:
          monthly_usd: 0
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
        """,
    )
    (pr / "scripts").mkdir(parents=True)
    (pr / "scripts" / "s.sh").write_text("#!/bin/sh\necho ok\n", encoding="utf-8")


def _phase1_execution_fixtures(root: Path, pid: str) -> None:
    """All-yes policy + containment grants so Phase 1 + env alignment passes in tests."""
    try:
        import yaml  # type: ignore[import-untyped]
    except ImportError:  # pragma: no cover
        return
    pol = root / "products" / pid / "argus.policy.yaml"
    pol.write_text(
        yaml.safe_dump(
            {"schema": "argus.project_permission_policy.v1", **{k: "yes" for k in PHASE1_KEYS}},
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    cfg = root / "config"
    cfg.mkdir(parents=True)
    (cfg / "argus_containment.yaml").write_text(
        "schema: argus.credential_containment_policy.v0\n"
        "grants:\n"
        "  aws_write_staging: true\n"
        "  aws_read_staging: true\n"
        "  git_push: true\n"
        "  deploy: true\n",
        encoding="utf-8",
    )
    sig = root / "runs" / "signals" / "latest"
    sig.mkdir(parents=True)
    (sig / f"{pid}.json").write_text('{"ok": true}\n', encoding="utf-8")
    exp = root / "runs" / "experiments"
    exp.mkdir(parents=True)
    (root / ".git").mkdir()


class TestSandboxWorkingDirectory(unittest.TestCase):
    def test_allows_products_subpath(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _product(root, "p1")
            c = ActionContract(
                action_id="a",
                product_id="p1",
                action_type="analyze",
                command="echo ok",
                working_directory="products/p1",
            )
            self.assertEqual(validate_execution_sandbox(c, repo_root=root), [])

    def test_blocks_repo_root_cwd(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _product(root, "p1")
            c = ActionContract(
                action_id="a",
                product_id="p1",
                action_type="analyze",
                command="true",
                working_directory=".",
            )
            err = validate_execution_sandbox(c, repo_root=root)
            self.assertTrue(any("sandbox:" in e and "products" in e for e in err))

    def test_blocks_other_product_path(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _product(root, "p1")
            _product(root, "p2")
            c = ActionContract(
                action_id="a",
                product_id="p1",
                action_type="analyze",
                command="true",
                working_directory="products/p2",
            )
            err = validate_execution_sandbox(c, repo_root=root)
            self.assertTrue(err)


class TestSandboxCommands(unittest.TestCase):
    def test_sudo_blocked(self) -> None:
        root = Path("/tmp")
        err = sandbox_command_errors("sudo echo hi", repo_root=root)
        self.assertTrue(any("sudo" in e.lower() for e in err))

    def test_git_reset_hard_flagged_by_dry_run(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _product(root, "p1")
            c = ActionContract(
                action_id="a",
                product_id="p1",
                action_type="analyze",
                command="git reset --hard",
                working_directory="products/p1",
            )
            inv = build_inventory(root)
            dr = dry_run(c, repo_root=root, inventory=inv)
            self.assertTrue(dr.dangerous_flags)
            with self.assertRaises(ExecutionBlocked):
                ensure_execution_allowed(c, root, dr)

    def test_safe_echo_allowed(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _product(root, "p1")
            _phase1_execution_fixtures(root, "p1")
            c = ActionContract(
                action_id="a",
                product_id="p1",
                action_type="analyze",
                command="echo safe",
                working_directory="products/p1",
            )
            inv = build_inventory(root)
            dr = dry_run(c, repo_root=root, inventory=inv)
            _, _ = ensure_execution_allowed(c, root, dr)


if __name__ == "__main__":
    unittest.main()
