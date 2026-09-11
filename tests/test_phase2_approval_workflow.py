"""Phase 2: artifact-backed approvals for Phase 1 ``confirm`` policy (orchestration path)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from argus.orchestrator.state_models import ACTION_STATUS_EXECUTED, ACTION_STATUS_FAILED
from argus.orchestrator.step_executor import execute_orchestration_action
from argus.project_permissions.approvals import (
    consume_confirm_once_grant,
    find_grant_for_confirm,
    load_approval_grants,
    record_operator_response,
    write_pending_approval_request,
)
from argus.project_permissions.gate import evaluate_phase1_for_keys


def _minimal_product_confirm(root: Path, product_id: str) -> None:
    pr = root / "products" / product_id
    pr.mkdir(parents=True)
    (pr / "product.yaml").write_text(
        f"""
id: {product_id}
name: ApproveTest
owner:
  team: test
lifecycle:
  stage: idea
metrics:
  local_paths:
    - metrics/
  primary: []
cost:
  monthly_usd: 1
  notes: ""
signals:
  - type: filesystem
    enabled: true
actions:
  start: "./scripts/start.sh"
  stop: "./scripts/stop.sh"
  analyze: "./scripts/analyze.sh"
constraints:
  max_monthly_cost_usd: 10
  min_activity_threshold: 0
""",
        encoding="utf-8",
    )
    (pr / "scripts").mkdir(exist_ok=True)
    for name, body in (
        ("start.sh", "#!/bin/sh\necho start\n"),
        ("stop.sh", "#!/bin/sh\necho stop\n"),
        ("analyze.sh", "#!/bin/sh\necho analyze\n"),
    ):
        (pr / "scripts" / name).write_text(body, encoding="utf-8")
    (pr / "metrics").mkdir(exist_ok=True)
    (pr / "metrics" / "dummy.txt").write_text("1", encoding="utf-8")
    (pr / "argus.policy.yaml").write_text(
        """
schema: argus.project_permission_policy.v1
change_experiments: 'yes'
commit_local: 'yes'
deploy: 'no'
mutate_nonprod: 'confirm'
mutate_prod: 'no'
observe_prod_signals: 'yes'
push_remote: 'no'
""".strip()
        + "\n",
        encoding="utf-8",
    )


def test_evaluate_confirm_blocks_without_grant_or_orch_id(tmp_path: Path) -> None:
    _minimal_product_confirm(tmp_path, "p1")
    ev = evaluate_phase1_for_keys(
        tmp_path,
        "p1",
        ("mutate_nonprod",),
        check_environment=False,
        execution_path="orchestration_step_executor",
        action_description="test",
    )
    assert ev["aggregate_decision"] == "blocked_pending_confirmation"
    assert ev.get("phase1_approval_found") is False


def test_evaluate_confirm_blocks_without_grant_with_orch_id(tmp_path: Path) -> None:
    _minimal_product_confirm(tmp_path, "p1")
    ev = evaluate_phase1_for_keys(
        tmp_path,
        "p1",
        ("mutate_nonprod",),
        check_environment=False,
        execution_path="orchestration_step_executor",
        action_description="test",
        orchestration_action_id="findings_generate",
    )
    assert ev["aggregate_decision"] == "blocked_pending_confirmation"
    assert ev.get("phase1_approval_required") is True
    assert ev.get("phase1_approval_found") is False


def test_always_grant_allows_repeatedly(tmp_path: Path) -> None:
    _minimal_product_confirm(tmp_path, "p1")
    record_operator_response(
        tmp_path,
        product_id="p1",
        phase1_policy_field="mutate_nonprod",
        response="always",
    )
    for _ in range(2):
        ev = evaluate_phase1_for_keys(
            tmp_path,
            "p1",
            ("mutate_nonprod",),
            check_environment=False,
            execution_path="orchestration_step_executor",
            action_description="test",
            orchestration_action_id="findings_generate",
        )
        assert ev["execution_proceeds"] is True
        assert ev["aggregate_decision"] == "allowed"
        assert ev.get("phase1_approval_applied", {}).get("response") == "always"


def test_confirm_once_allows_once_then_blocks(tmp_path: Path) -> None:
    _minimal_product_confirm(tmp_path, "p1")
    record_operator_response(
        tmp_path,
        product_id="p1",
        phase1_policy_field="mutate_nonprod",
        response="confirm_once",
        orchestration_action_id="findings_generate",
    )
    ev1 = evaluate_phase1_for_keys(
        tmp_path,
        "p1",
        ("mutate_nonprod",),
        check_environment=False,
        execution_path="orchestration_step_executor",
        action_description="test",
        orchestration_action_id="findings_generate",
    )
    assert ev1["execution_proceeds"] is True
    pc = ev1.get("phase1_pending_consumption")
    assert isinstance(pc, dict) and pc.get("grant_id")
    assert consume_confirm_once_grant(tmp_path, "p1", str(pc["grant_id"])) is True

    ev2 = evaluate_phase1_for_keys(
        tmp_path,
        "p1",
        ("mutate_nonprod",),
        check_environment=False,
        execution_path="orchestration_step_executor",
        action_description="test",
        orchestration_action_id="findings_generate",
    )
    assert ev2["aggregate_decision"] == "blocked_pending_confirmation"


def test_no_response_records_audit_only_no_grant(tmp_path: Path) -> None:
    _minimal_product_confirm(tmp_path, "p1")
    out = record_operator_response(
        tmp_path,
        product_id="p1",
        phase1_policy_field="mutate_nonprod",
        response="no",
    )
    assert out.get("recorded") == "audit_only"
    assert find_grant_for_confirm(tmp_path, "p1", "mutate_nonprod", "findings_generate") is None
    ev = evaluate_phase1_for_keys(
        tmp_path,
        "p1",
        ("mutate_nonprod",),
        check_environment=False,
        execution_path="orchestration_step_executor",
        action_description="test",
        orchestration_action_id="findings_generate",
    )
    assert ev["aggregate_decision"] == "blocked_pending_confirmation"


def test_pending_approval_artifact_shape(tmp_path: Path) -> None:
    p = write_pending_approval_request(
        tmp_path,
        product_id="p1",
        orchestration_action_id="findings_generate",
        phase1_policy_field="mutate_nonprod",
        reason="r",
        execution_path="orchestration_step_executor",
        phase1_decision_audit_path_repo_relative="runs/policy/phase1_decisions/p1/x.json",
    )
    raw = json.loads(p.read_text(encoding="utf-8"))
    assert raw["schema"] == "argus.pending_approval_request.v1"
    assert raw["product_id"] == "p1"
    assert raw["orchestration_action_id"] == "findings_generate"
    assert raw["phase1_policy_field"] == "mutate_nonprod"
    assert raw["suggested_operator_choices"]


def test_execute_orchestration_blocked_confirm_writes_pending_path(tmp_path: Path) -> None:
    _minimal_product_confirm(tmp_path, "p1")
    r = execute_orchestration_action(tmp_path, "p1", "findings_generate")
    assert r["action_status"] == "blocked"
    ed = r.get("execution_detail") or {}
    assert ed.get("phase1_pending_approval_request_path")
    assert "pending_approvals" in str(ed.get("phase1_pending_approval_request_path"))


def test_execute_orchestration_confirm_once_consumes_after_success(tmp_path: Path) -> None:
    _minimal_product_confirm(tmp_path, "p1")
    record_operator_response(
        tmp_path,
        product_id="p1",
        phase1_policy_field="mutate_nonprod",
        response="confirm_once",
        orchestration_action_id="findings_generate",
    )
    data_before = load_approval_grants(tmp_path, "p1")
    gid = next(g["grant_id"] for g in data_before["grants"] if g.get("kind") == "confirm_once")

    with patch(
        "argus.orchestrator.step_executor._execute_orchestration_action_impl",
        return_value={"action_status": ACTION_STATUS_EXECUTED, "execution_detail": {"ok": True}, "execution_error": None},
    ):
        r = execute_orchestration_action(tmp_path, "p1", "findings_generate")
    assert r["action_status"] == "executed"
    data_after = load_approval_grants(tmp_path, "p1")
    consumed = next((g for g in data_after["grants"] if g.get("grant_id") == gid), None)
    assert consumed and consumed.get("consumed_at_utc")


def test_confirm_once_without_success_does_not_consume(tmp_path: Path) -> None:
    _minimal_product_confirm(tmp_path, "p1")
    record_operator_response(
        tmp_path,
        product_id="p1",
        phase1_policy_field="mutate_nonprod",
        response="confirm_once",
        orchestration_action_id="findings_generate",
    )
    data_before = load_approval_grants(tmp_path, "p1")
    gid = next(g["grant_id"] for g in data_before["grants"] if g.get("kind") == "confirm_once")

    with patch(
        "argus.orchestrator.step_executor._execute_orchestration_action_impl",
        return_value={"action_status": ACTION_STATUS_FAILED, "execution_detail": {}, "execution_error": "x"},
    ):
        r = execute_orchestration_action(tmp_path, "p1", "findings_generate")
    assert r["action_status"] == "failed"
    data_after = load_approval_grants(tmp_path, "p1")
    row = next(g for g in data_after["grants"] if g.get("grant_id") == gid)
    assert row.get("consumed_at_utc") is None


def test_record_confirm_once_requires_action_id() -> None:
    with pytest.raises(ValueError, match="confirm_once requires"):
        record_operator_response(
            Path("/tmp"),
            product_id="p",
            phase1_policy_field="mutate_nonprod",
            response="confirm_once",
            orchestration_action_id=None,
        )
