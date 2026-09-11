"""Governed subprocess attempts always persist runs/execution/<run_id>/run.json (including Phase 1 blocks)."""

from __future__ import annotations

import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest

from argus.execution.engine import load_run
from argus.execution.models import ExecutionStatus
from argus.execution.runner import run_action_file
from argus.orchestrator.phase1_execution_detail import PHASE1_EMBED_SCHEMA_VERSION
from argus.project_permissions.schema import PHASE1_KEYS


def _minimal_product(root: Path, pid: str) -> None:
    pr = root / "products" / pid
    pr.mkdir(parents=True)
    (pr / "scripts").mkdir(parents=True)
    (pr / "scripts" / "s.sh").write_text("#!/bin/sh\necho\n", encoding="utf-8")
    (pr / "product.yaml").write_text(
        f"""
id: {pid}
name: T
owner:
  team: test
lifecycle:
  stage: idea
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
  max_monthly_cost_usd: 1
  min_activity_threshold: 0
""",
        encoding="utf-8",
    )


def _policy_all_yes(root: Path, pid: str) -> None:
    try:
        import yaml  # type: ignore[import-untyped]
    except ImportError:  # pragma: no cover
        pytest.skip("yaml required")
    pol = root / "products" / pid / "argus.policy.yaml"
    pol.write_text(
        yaml.safe_dump(
            {"schema": "argus.project_permission_policy.v1", **{k: "yes" for k in PHASE1_KEYS}},
            sort_keys=True,
        ),
        encoding="utf-8",
    )


def _env_ok(root: Path, pid: str) -> None:
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
    (root / "runs" / "experiments").mkdir(parents=True)
    (root / ".git").mkdir()


def _action_file(root: Path, pid: str) -> Path:
    p = root / "act.yaml"
    p.write_text(
        textwrap.dedent(
            f"""
            schema: argus.action_contract.v1
            action_id: gov_act
            product_id: {pid}
            action_type: analyze
            command: echo governed_ok
            working_directory: products/{pid}
            requires_approval: false
            safe_to_auto_execute: true
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    return p


def _assert_embed(ed: dict) -> None:
    assert ed.get("phase1_embed_schema_version") == PHASE1_EMBED_SCHEMA_VERSION
    assert "phase1_evaluated" in ed
    assert "phase1_not_evaluated_reason" in ed
    assert isinstance(ed.get("phase1_permission_decision"), (dict, type(None)))
    assert "phase1_decision_audit_path" in ed


@patch("argus.execution.engine.subprocess.run", side_effect=AssertionError("subprocess must not run"))
def test_refused_writes_run_json_with_embed(mock_run: object, tmp_path: Path) -> None:
    pid = "g1"
    _minimal_product(tmp_path, pid)
    _policy_all_yes(tmp_path, pid)
    (tmp_path / "products" / pid / "argus.policy.yaml").write_text(
        textwrap.dedent(
            """
            schema: argus.project_permission_policy.v1
            observe_prod_signals: "yes"
            mutate_nonprod: "no"
            mutate_prod: "no"
            commit_local: "yes"
            push_remote: "no"
            deploy: "no"
            change_experiments: "no"
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    af = _action_file(tmp_path, pid)
    run = run_action_file(tmp_path, af, enable_execution=True)
    assert run.status == ExecutionStatus.BLOCKED
    assert run.subprocess_launched is False
    rpath = tmp_path / "runs" / "execution" / run.run_id / "run.json"
    assert rpath.is_file()
    loaded = load_run(tmp_path, run.run_id)
    assert loaded.execution_detail is not None
    _assert_embed(loaded.execution_detail)
    pd = loaded.execution_detail["phase1_permission_decision"]
    assert pd.get("aggregate_decision") == "refused"


@patch("argus.execution.engine.subprocess.run", side_effect=AssertionError("subprocess must not run"))
def test_blocked_pending_writes_run_json_and_pending(mock_run: object, tmp_path: Path) -> None:
    pid = "g2"
    _minimal_product(tmp_path, pid)
    _env_ok(tmp_path, pid)
    (tmp_path / "products" / pid / "argus.policy.yaml").write_text(
        textwrap.dedent(
            """
            schema: argus.project_permission_policy.v1
            observe_prod_signals: "yes"
            mutate_nonprod: "confirm"
            mutate_prod: "no"
            commit_local: "yes"
            push_remote: "no"
            deploy: "no"
            change_experiments: "no"
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    af = _action_file(tmp_path, pid)
    run = run_action_file(tmp_path, af, enable_execution=True)
    assert run.status == ExecutionStatus.BLOCKED
    rpath = tmp_path / "runs" / "execution" / run.run_id / "run.json"
    assert rpath.is_file()
    loaded = load_run(tmp_path, run.run_id)
    _assert_embed(loaded.execution_detail)
    pd = loaded.execution_detail["phase1_permission_decision"]
    assert pd.get("aggregate_decision") == "blocked_pending_confirmation"
    pend = loaded.execution_detail.get("phase1_pending_approval_request_path")
    assert isinstance(pend, str) and pend
    assert (tmp_path / pend).is_file()


@patch("argus.execution.engine.subprocess.run", side_effect=AssertionError("subprocess must not run"))
def test_capability_mismatch_writes_run_json(mock_run: object, tmp_path: Path) -> None:
    pid = "g3"
    _minimal_product(tmp_path, pid)
    _policy_all_yes(tmp_path, pid)
    # no aws_write_staging — mutate_nonprod env check fails
    cfg = tmp_path / "config"
    cfg.mkdir(parents=True)
    (cfg / "argus_containment.yaml").write_text(
        "schema: argus.credential_containment_policy.v0\ngrants:\n  git_push: true\n",
        encoding="utf-8",
    )
    sig = tmp_path / "runs" / "signals" / "latest"
    sig.mkdir(parents=True)
    (sig / f"{pid}.json").write_text("{}", encoding="utf-8")
    (tmp_path / "runs" / "experiments").mkdir(parents=True)
    (tmp_path / ".git").mkdir()
    af = _action_file(tmp_path, pid)
    run = run_action_file(tmp_path, af, enable_execution=True)
    assert run.status == ExecutionStatus.BLOCKED
    loaded = load_run(tmp_path, run.run_id)
    _assert_embed(loaded.execution_detail)
    assert loaded.execution_detail["phase1_permission_decision"].get("aggregate_decision") == "capability_mismatch"


def test_allowed_still_writes_run_json_with_embed(tmp_path: Path) -> None:
    pid = "g4"
    _minimal_product(tmp_path, pid)
    _policy_all_yes(tmp_path, pid)
    _env_ok(tmp_path, pid)
    af = _action_file(tmp_path, pid)
    run = run_action_file(tmp_path, af, enable_execution=True)
    assert run.status == ExecutionStatus.SUCCESS
    loaded = load_run(tmp_path, run.run_id)
    _assert_embed(loaded.execution_detail or {})
    assert loaded.execution_detail["phase1_permission_decision"].get("aggregate_decision") == "allowed"
