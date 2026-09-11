"""Track D: STEP_EXECUTION_REGISTRY ↔ ORCHESTRATION_ACTION_PHASE1_KEYS governance."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from argus.orchestrator.orchestration_phase1 import (
    ACTION_SIGNALS_COLLECT,
    ORCHESTRATION_ACTION_PHASE1_KEYS,
    OrchestrationPhase1MappingError,
    orchestration_phase1_mapping_report,
    validate_orchestration_registry_phase1_mapping,
)
from argus.orchestrator.state_models import ACTION_FINDINGS_GENERATE
from argus.orchestrator.step_executor import STEP_EXECUTION_REGISTRY
from argus.project_permissions.gate import evaluate_phase1_for_keys


def test_registry_and_mapping_are_consistent_at_import() -> None:
    """Import-time validate already ran; double-check explicitly."""
    validate_orchestration_registry_phase1_mapping(STEP_EXECUTION_REGISTRY.keys())


def test_all_registry_actions_are_mapped() -> None:
    for aid in STEP_EXECUTION_REGISTRY:
        assert aid in ORCHESTRATION_ACTION_PHASE1_KEYS, aid


def test_validate_raises_on_unmapped_registry_action() -> None:
    with pytest.raises(OrchestrationPhase1MappingError, match="Unmapped orchestration"):
        validate_orchestration_registry_phase1_mapping(["__not_a_registered_action__"])


def test_validate_raises_on_extra_mapping_entry() -> None:
    from argus.orchestrator import orchestration_phase1 as mod

    orig = dict(ORCHESTRATION_ACTION_PHASE1_KEYS)
    try:
        mod.ORCHESTRATION_ACTION_PHASE1_KEYS = {
            **orig,
            "__ghost_action__": ("mutate_nonprod",),
        }
        with pytest.raises(OrchestrationPhase1MappingError, match="Extra mapping"):
            validate_orchestration_registry_phase1_mapping(STEP_EXECUTION_REGISTRY.keys())
    finally:
        mod.ORCHESTRATION_ACTION_PHASE1_KEYS = orig


def test_mapping_report_lists_all_actions() -> None:
    rep = orchestration_phase1_mapping_report()
    assert rep.get("schema") == "argus.orchestration_phase1_mapping_report.v1"
    assert rep.get("action_count") == len(ORCHESTRATION_ACTION_PHASE1_KEYS)
    ids = {r["action_id"] for r in (rep.get("actions") or [])}
    assert ids == set(ORCHESTRATION_ACTION_PHASE1_KEYS.keys())


def test_single_key_mapping_enforced(tmp_path: Path) -> None:
    pr = tmp_path / "products" / "p1"
    pr.mkdir(parents=True)
    (pr / "scripts").mkdir(exist_ok=True)
    (pr / "scripts" / "s.sh").write_text("#!/bin/sh\necho\n", encoding="utf-8")
    (pr / "product.yaml").write_text(
        """
id: p1
name: T
owner: { team: test }
lifecycle: { stage: idea }
metrics: { local_paths: [], primary: [] }
cost: { monthly_usd: 0, notes: "" }
signals: [ { type: filesystem, enabled: true } ]
actions: { start: "./scripts/s.sh", stop: "./scripts/s.sh", analyze: "./scripts/s.sh" }
constraints: { max_monthly_cost_usd: 1, min_activity_threshold: 0 }
""",
        encoding="utf-8",
    )
    (pr / "argus.policy.yaml").write_text(
        textwrap.dedent(
            """
            observe_prod_signals: "yes"
            mutate_nonprod: "no"
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
    ev = evaluate_phase1_for_keys(
        tmp_path,
        "p1",
        ORCHESTRATION_ACTION_PHASE1_KEYS[ACTION_FINDINGS_GENERATE],
        check_environment=False,
        execution_path="orchestration_step_executor",
        action_description="t",
        orchestration_action_id=ACTION_FINDINGS_GENERATE,
    )
    assert ev.get("aggregate_decision") == "refused"


def test_multi_key_action_requires_all_keys(tmp_path: Path) -> None:
    """signals_collect maps to observe_prod_signals + change_experiments — both must allow."""
    pr = tmp_path / "products" / "p1"
    pr.mkdir(parents=True)
    (pr / "scripts").mkdir(exist_ok=True)
    (pr / "scripts" / "s.sh").write_text("#!/bin/sh\necho\n", encoding="utf-8")
    (pr / "product.yaml").write_text(
        """
id: p1
name: T
owner: { team: test }
lifecycle: { stage: idea }
metrics: { local_paths: [], primary: [] }
cost: { monthly_usd: 0, notes: "" }
signals: [ { type: filesystem, enabled: true } ]
actions: { start: "./scripts/s.sh", stop: "./scripts/s.sh", analyze: "./scripts/s.sh" }
constraints: { max_monthly_cost_usd: 1, min_activity_threshold: 0 }
""",
        encoding="utf-8",
    )
    (pr / "argus.policy.yaml").write_text(
        textwrap.dedent(
            """
            observe_prod_signals: "yes"
            mutate_nonprod: "yes"
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
    keys = ORCHESTRATION_ACTION_PHASE1_KEYS[ACTION_SIGNALS_COLLECT]
    assert len(keys) == 2
    ev = evaluate_phase1_for_keys(
        tmp_path,
        "p1",
        keys,
        check_environment=False,
        execution_path="orchestration_step_executor",
        action_description="t",
        orchestration_action_id=ACTION_SIGNALS_COLLECT,
    )
    assert ev.get("aggregate_decision") == "refused"
    assert "change_experiments" in str(ev.get("reason") or "").lower() or "denies" in str(ev.get("reason") or "")
