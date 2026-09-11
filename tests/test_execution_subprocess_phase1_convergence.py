"""Subprocess execution aligns with Phase 1 + Phase 2 approval semantics (Phase 2.5)."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from argus.actions.executor import dry_run
from argus.actions.models import ActionContract
from argus.execution.engine import (
    ExecutionBlocked,
    ensure_execution_allowed,
    new_run_id,
    run_subprocess,
)
from argus.execution.models import ExecutionStatus
from argus.orchestrator.phase1_execution_detail import PHASE1_EMBED_SCHEMA_VERSION
from argus.products.inventory import build_inventory
from argus.project_permissions.approvals import consume_confirm_once_grant, record_operator_response
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


def _subprocess_env_support_files(root: Path, pid: str) -> None:
    """Containment + signals + git + experiments (subprocess env checks; does not write policy)."""
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


def _phase1_subprocess_ok_env(root: Path, pid: str) -> None:
    """Policy all-yes + containment + signals + git + experiments (subprocess env checks)."""
    try:
        import yaml  # type: ignore[import-untyped]
    except ImportError:  # pragma: no cover
        pytest.skip("yaml not available")
    pol = root / "products" / pid / "argus.policy.yaml"
    pol.write_text(
        yaml.safe_dump(
            {"schema": "argus.project_permission_policy.v1", **{k: "yes" for k in PHASE1_KEYS}},
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    _subprocess_env_support_files(root, pid)


def _policy_text(root: Path, pid: str, body: str) -> None:
    (root / "products" / pid / "argus.policy.yaml").write_text(
        textwrap.dedent(body).strip() + "\n",
        encoding="utf-8",
    )


def _contract(pid: str, *, aid: str = "a1", command: str = "echo ok") -> ActionContract:
    return ActionContract(
        action_id=aid,
        product_id=pid,
        action_type="analyze",
        command=command,
        working_directory=f"products/{pid}",
    )


def _dry(c: ActionContract, root: Path):
    inv = build_inventory(root)
    return dry_run(c, repo_root=root, inventory=inv)


def _assert_embed_keys(d: dict) -> None:
    assert d.get("phase1_embed_schema_version") == PHASE1_EMBED_SCHEMA_VERSION
    assert d.get("phase1_evaluated") is True
    assert d.get("phase1_not_evaluated_reason") is None
    assert isinstance(d.get("phase1_permission_decision"), dict)
    assert isinstance(d.get("phase1_decision_audit_path"), str) and d["phase1_decision_audit_path"]


def test_subprocess_allowed_echo_with_full_env(tmp_path: Path) -> None:
    pid = "p1"
    _minimal_product(tmp_path, pid)
    _phase1_subprocess_ok_env(tmp_path, pid)
    c = _contract(pid)
    ev, rel = ensure_execution_allowed(c, tmp_path, _dry(c, tmp_path))
    assert ev.get("execution_proceeds") is True
    run = run_subprocess(
        c,
        repo_root=tmp_path,
        run_id=new_run_id(),
        phase1_ev=ev,
        phase1_audit_rel=rel,
    )
    assert run.status == ExecutionStatus.SUCCESS
    assert run.execution_detail is not None
    _assert_embed_keys(run.execution_detail)


def test_subprocess_refused_policy_no(tmp_path: Path) -> None:
    pid = "p1"
    _minimal_product(tmp_path, pid)
    _policy_text(
        tmp_path,
        pid,
        """
        schema: argus.project_permission_policy.v1
        observe_prod_signals: "yes"
        mutate_nonprod: "no"
        mutate_prod: "no"
        commit_local: "yes"
        push_remote: "no"
        deploy: "no"
        change_experiments: "no"
        """,
    )
    c = _contract(pid)
    with pytest.raises(ExecutionBlocked) as ei:
        ensure_execution_allowed(c, tmp_path, _dry(c, tmp_path))
    ex = ei.value
    assert ex.phase1_execution_detail is not None
    _assert_embed_keys(ex.phase1_execution_detail)
    pd = ex.phase1_execution_detail["phase1_permission_decision"]
    assert pd.get("aggregate_decision") == "refused"


def test_subprocess_blocked_pending_creates_pending_artifact(tmp_path: Path) -> None:
    pid = "p1"
    _minimal_product(tmp_path, pid)
    _policy_text(
        tmp_path,
        pid,
        """
        schema: argus.project_permission_policy.v1
        observe_prod_signals: "yes"
        mutate_nonprod: "confirm"
        mutate_prod: "no"
        commit_local: "yes"
        push_remote: "no"
        deploy: "no"
        change_experiments: "no"
        """,
    )
    c = _contract(pid, aid="subact")
    with pytest.raises(ExecutionBlocked) as ei:
        ensure_execution_allowed(c, tmp_path, _dry(c, tmp_path))
    ex = ei.value
    assert ex.phase1_execution_detail is not None
    pend = ex.phase1_execution_detail.get("phase1_pending_approval_request_path")
    assert isinstance(pend, str) and pend
    assert (tmp_path / pend).is_file()
    pd = ex.phase1_execution_detail["phase1_permission_decision"]
    assert pd.get("aggregate_decision") == "blocked_pending_confirmation"


def test_confirm_once_consumed_after_successful_subprocess(tmp_path: Path) -> None:
    pid = "p1"
    _minimal_product(tmp_path, pid)
    _policy_text(
        tmp_path,
        pid,
        """
        schema: argus.project_permission_policy.v1
        observe_prod_signals: "yes"
        mutate_nonprod: "confirm"
        mutate_prod: "no"
        commit_local: "yes"
        push_remote: "no"
        deploy: "no"
        change_experiments: "no"
        """,
    )
    _subprocess_env_support_files(tmp_path, pid)
    aid = "scoped-action"
    c = _contract(pid, aid=aid)
    record_operator_response(
        tmp_path,
        product_id=pid,
        phase1_policy_field="mutate_nonprod",
        response="confirm_once",
        orchestration_action_id=aid,
        note="test",
    )

    ev1, rel1 = ensure_execution_allowed(c, tmp_path, _dry(c, tmp_path))
    assert ev1.get("phase1_pending_consumption")
    run = run_subprocess(
        c,
        repo_root=tmp_path,
        run_id=new_run_id(),
        phase1_ev=ev1,
        phase1_audit_rel=rel1,
    )
    assert run.status == ExecutionStatus.SUCCESS

    pc = ev1["phase1_pending_consumption"]
    consume_confirm_once_grant(tmp_path, pid, str(pc["grant_id"]))

    with pytest.raises(ExecutionBlocked):
        ensure_execution_allowed(c, tmp_path, _dry(c, tmp_path))


def test_always_grant_allows_repeated_subprocess(tmp_path: Path) -> None:
    pid = "p1"
    _minimal_product(tmp_path, pid)
    _policy_text(
        tmp_path,
        pid,
        """
        schema: argus.project_permission_policy.v1
        observe_prod_signals: "yes"
        mutate_nonprod: "confirm"
        mutate_prod: "no"
        commit_local: "yes"
        push_remote: "no"
        deploy: "no"
        change_experiments: "no"
        """,
    )
    _subprocess_env_support_files(tmp_path, pid)
    c = _contract(pid)
    record_operator_response(
        tmp_path,
        product_id=pid,
        phase1_policy_field="mutate_nonprod",
        response="always",
        orchestration_action_id=None,
        note="test",
    )
    for _ in range(2):
        ev, rel = ensure_execution_allowed(c, tmp_path, _dry(c, tmp_path))
        run = run_subprocess(
            c,
            repo_root=tmp_path,
            run_id=new_run_id(),
            phase1_ev=ev,
            phase1_audit_rel=rel,
        )
        assert run.status == ExecutionStatus.SUCCESS
        assert run.execution_detail is not None
        _assert_embed_keys(run.execution_detail)


def test_capability_mismatch_distinct_from_refusal(tmp_path: Path) -> None:
    pid = "p1"
    _minimal_product(tmp_path, pid)
    _policy_text(
        tmp_path,
        pid,
        """
        schema: argus.project_permission_policy.v1
        observe_prod_signals: "yes"
        mutate_nonprod: "yes"
        mutate_prod: "no"
        commit_local: "yes"
        push_remote: "no"
        deploy: "no"
        change_experiments: "no"
        """,
    )
    # No containment grants; policy says yes but aws_write_staging is declined.
    (tmp_path / ".git").mkdir()
    (tmp_path / "runs" / "signals" / "latest").mkdir(parents=True)
    (tmp_path / "runs" / "signals" / "latest" / f"{pid}.json").write_text("{}", encoding="utf-8")
    (tmp_path / "runs" / "experiments").mkdir(parents=True)

    c = _contract(pid)
    with pytest.raises(ExecutionBlocked) as ei:
        ensure_execution_allowed(c, tmp_path, _dry(c, tmp_path))
    pd = ei.value.phase1_execution_detail["phase1_permission_decision"]
    assert pd.get("aggregate_decision") == "capability_mismatch"
    assert "environment" in (ei.value.reasons[0].lower() + pd.get("reason", "").lower())


def test_embed_fields_always_present_on_subprocess_run_record(tmp_path: Path) -> None:
    pid = "p1"
    _minimal_product(tmp_path, pid)
    _phase1_subprocess_ok_env(tmp_path, pid)
    c = _contract(pid, command="false")
    ev, rel = ensure_execution_allowed(c, tmp_path, _dry(c, tmp_path))
    run = run_subprocess(
        c,
        repo_root=tmp_path,
        run_id=new_run_id(),
        phase1_ev=ev,
        phase1_audit_rel=rel,
    )
    assert run.status == ExecutionStatus.FAILED
    assert run.execution_detail is not None
    _assert_embed_keys(run.execution_detail)
