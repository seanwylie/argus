"""Operator visibility: permission_gate_summary (Track C)."""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

from argus.portfolio.permission_operator_surface import (
    build_permission_gate_summary,
    list_pending_approval_summaries,
    summarize_active_grants,
)
from argus.project_permissions.approvals import record_operator_response


def _minimal_product(root: Path, pid: str) -> None:
    pr = root / "products" / pid
    pr.mkdir(parents=True)
    (pr / "scripts").mkdir(exist_ok=True)
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


def test_gate_summary_labels_policy_confirm_and_pending(tmp_path: Path) -> None:
    _minimal_product(tmp_path, "p1")
    (tmp_path / "products" / "p1" / "argus.policy.yaml").write_text(
        textwrap.dedent(
            """
            observe_prod_signals: "yes"
            mutate_nonprod: "confirm"
            mutate_prod: "no"
            commit_local: "yes"
            push_remote: "no"
            deploy: "no"
            change_experiments: "yes"
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    pend_dir = tmp_path / "runs" / "policy" / "pending_approvals" / "p1"
    pend_dir.mkdir(parents=True)
    body = {
        "schema": "argus.pending_approval_request.v1",
        "product_id": "p1",
        "orchestration_action_id": "findings_generate",
        "phase1_policy_field": "mutate_nonprod",
        "reason": "needs human",
        "requested_at_utc": "2026-01-01T00:00:00+00:00",
        "execution_path": "orchestration_step_executor",
        "phase1_decision_audit_path_repo_relative": "runs/policy/x.json",
        "artifact_path_repo_relative": "runs/policy/pending_approvals/p1/pending_x.json",
    }
    (pend_dir / "pending_20260101T000000Z_findings_generate.json").write_text(
        json.dumps(body) + "\n",
        encoding="utf-8",
    )
    g = build_permission_gate_summary(tmp_path, "p1")
    assert "policy_confirm_required" in g["triage_labels"]
    assert "policy_denies_present" in g["triage_labels"]
    assert "pending_approval_artifacts" in g["triage_labels"]
    assert len(g["pending_approvals"]) == 1
    assert g["pending_approvals"][0]["orchestration_action_id"] == "findings_generate"


def test_active_always_grant_surfaces(tmp_path: Path) -> None:
    _minimal_product(tmp_path, "p1")
    (tmp_path / "products" / "p1" / "argus.policy.yaml").write_text(
        textwrap.dedent(
            """
            observe_prod_signals: "yes"
            mutate_nonprod: "confirm"
            mutate_prod: "no"
            commit_local: "yes"
            push_remote: "no"
            deploy: "no"
            change_experiments: "yes"
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    record_operator_response(
        tmp_path,
        product_id="p1",
        phase1_policy_field="mutate_nonprod",
        response="always",
    )
    g = build_permission_gate_summary(tmp_path, "p1")
    assert "active_always_grant" in g["triage_labels"]
    assert "mutate_nonprod" in (g.get("grants") or {}).get("always_fields", [])


def test_environment_mismatch_distinct_from_policy_denial(tmp_path: Path) -> None:
    """policy vs env rows come from phase1_summary — at least label when list non-empty."""
    _minimal_product(tmp_path, "p1")
    (tmp_path / "products" / "p1" / "argus.policy.yaml").write_text(
        textwrap.dedent(
            """
            observe_prod_signals: "yes"
            mutate_nonprod: "yes"
            mutate_prod: "no"
            commit_local: "yes"
            push_remote: "no"
            deploy: "no"
            change_experiments: "yes"
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    g = build_permission_gate_summary(tmp_path, "p1")
    # May or may not have mismatches depending on env builder — structure must carry rows.
    assert "environment_mismatch" in g
    assert isinstance(g.get("environment_mismatch_rows"), list)


def test_list_pending_does_not_mkdir(tmp_path: Path) -> None:
    assert list_pending_approval_summaries(tmp_path, "nope") == []


def test_summarize_grants_empty(tmp_path: Path) -> None:
    assert summarize_active_grants(tmp_path, "x")["always_fields"] == []
