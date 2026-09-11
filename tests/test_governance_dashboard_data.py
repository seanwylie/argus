"""Tests for ``argus.dashboard.governance_data`` (operator console governance helpers)."""

from __future__ import annotations

import json
import time
from pathlib import Path

from argus.dashboard.governance_data import (
    classify_governed_run,
    list_recent_governed_execution_runs,
    scan_pending_approval_rows,
)


def test_scan_pending_rows(tmp_path: Path) -> None:
    d = tmp_path / "runs" / "policy" / "pending_approvals" / "p1"
    d.mkdir(parents=True)
    (d / "pending_x.json").write_text(
        json.dumps(
            {
                "schema": "argus.pending_approval_request.v1",
                "product_id": "p1",
                "phase1_policy_field": "mutate_nonprod",
                "orchestration_action_id": "a1",
                "execution_path": "subprocess_execution",
                "reason": "need confirm",
            },
        ),
        encoding="utf-8",
    )
    rows = scan_pending_approval_rows(tmp_path)
    assert len(rows) == 1
    assert rows[0]["product_id"] == "p1"
    assert rows[0]["phase1_policy_field"] == "mutate_nonprod"


def test_list_recent_execution_runs_sort(tmp_path: Path) -> None:
    ex = tmp_path / "runs" / "execution"
    r1 = ex / "exec_old"
    r2 = ex / "exec_new"
    r1.mkdir(parents=True)
    r2.mkdir(parents=True)
    old = {
        "run_id": "exec_old",
        "status": "blocked",
        "product_id": "p",
        "action_id": "a",
        "command": "echo",
        "working_directory": "products/p",
        "started_at": "t",
        "execution_detail": {
            "phase1_permission_decision": {"aggregate_decision": "refused"},
        },
    }
    new = {
        "run_id": "exec_new",
        "status": "success",
        "product_id": "p",
        "action_id": "a",
        "command": "echo",
        "working_directory": "products/p",
        "started_at": "t",
        "execution_detail": {
            "phase1_permission_decision": {"aggregate_decision": "allowed"},
        },
    }
    (r1 / "run.json").write_text(json.dumps(old), encoding="utf-8")
    time.sleep(0.05)
    (r2 / "run.json").write_text(json.dumps(new), encoding="utf-8")
    rows = list_recent_governed_execution_runs(tmp_path, limit=10)
    assert rows[0]["run_id"] == "exec_new"


def test_classify_governed_run() -> None:
    assert (
        classify_governed_run(
            {
                "status": "blocked",
                "execution_detail": {
                    "phase1_permission_decision": {"aggregate_decision": "capability_mismatch"},
                },
            },
        )
        == "capability_mismatch"
    )
    assert classify_governed_run({"status": "success"}) == "success"
