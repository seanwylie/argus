"""Static Builder contract registry (prepare + reconcile dispatch)."""

from __future__ import annotations

from pathlib import Path

from argus.builder.bug_fix_outcome import derive_bug_fix_execution_outcome
from argus.builder.content_slot_outcome import derive_content_slot_execution_outcome
from argus.builder.contract_registry import (
    BUILDER_CONTRACT_KIND_SPECS,
    OutcomeDeriveContext,
    build_execution_contract_for_kind,
    derive_execution_outcome_for_kind,
    get_kind_spec,
    manual_set_target_kinds,
    resolve_reconcile_outcome_kind,
    supported_target_types,
)
from argus.builder.signal_instrumentation_outcome import (
    derive_signal_instrumentation_execution_outcome,
)


def test_supported_kinds_include_all_contract_types() -> None:
    assert {"content_slot", "bug_fix", "signal_instrumentation"} <= supported_target_types()


def test_manual_set_target_kinds_matches_spec_flags() -> None:
    m = manual_set_target_kinds()
    for k, spec in BUILDER_CONTRACT_KIND_SPECS.items():
        assert (k in m) == spec.manual_set_target_eligible
    assert "content_slot" not in m
    assert m == frozenset({"bug_fix", "signal_instrumentation"})


def test_resolve_reconcile_outcome_kind_precedence() -> None:
    assert resolve_reconcile_outcome_kind("bug_fix", "content_slot") == "bug_fix"
    assert resolve_reconcile_outcome_kind("", "bug_fix") == "bug_fix"
    assert resolve_reconcile_outcome_kind("signal_instrumentation", "") == "signal_instrumentation"
    assert resolve_reconcile_outcome_kind("content_slot", "content_slot") == "content_slot"
    assert resolve_reconcile_outcome_kind("", "") == "content_slot"


def test_bug_fix_spec_flags() -> None:
    s = get_kind_spec("bug_fix")
    assert s is not None
    assert s.requires_allowed_paths_exact is True
    assert s.skip_generate_next_expansion_heuristic is True
    assert s.wrap_valueerror_as_prepare_error is True
    assert s.manual_set_target_eligible is True


def test_outcome_derive_matches_direct_bug_fix() -> None:
    task = {
        "resolved_target": {"id": "x", "target_type": "bug_fix"},
        "execution_contract": {"contract_kind": "bug_fix"},
    }
    ctx = OutcomeDeriveContext(
        repo_root=Path("/tmp"),
        product_id="p",
        products_dir=None,
        scope_check={"scope_breach": False},
        invoke_data={"invocation_status": "ok"},
        task_data=task,
        builder_diff_summary={"changed_file_count": 1},
        prior_resolved_target=None,
    )
    a = derive_execution_outcome_for_kind("bug_fix", ctx)
    b = derive_bug_fix_execution_outcome(
        scope_check=ctx.scope_check,
        invoke_data=ctx.invoke_data,
        task_data=ctx.task_data,
        builder_diff_summary=ctx.builder_diff_summary,
    )
    assert a == b


def test_outcome_derive_matches_direct_signal_instrumentation(tmp_path: Path) -> None:
    (tmp_path / "products" / "p").mkdir(parents=True)
    task = {
        "resolved_target": {"id": "s", "target_type": "signal_instrumentation"},
        "execution_contract": {"contract_kind": "signal_instrumentation"},
    }
    ctx = OutcomeDeriveContext(
        repo_root=tmp_path,
        product_id="p",
        products_dir=None,
        scope_check={"scope_breach": False},
        invoke_data={"invocation_status": "ok"},
        task_data=task,
        builder_diff_summary={"changed_file_count": 1, "changed_files_argus_relative": ["products/p/a"]},
        prior_resolved_target=None,
    )
    a = derive_execution_outcome_for_kind("signal_instrumentation", ctx)
    b = derive_signal_instrumentation_execution_outcome(
        repo_root=tmp_path,
        product_id="p",
        products_dir=None,
        scope_check=ctx.scope_check,
        invoke_data=ctx.invoke_data,
        task_data=ctx.task_data,
        builder_diff_summary=ctx.builder_diff_summary,
    )
    assert a == b


def test_outcome_derive_matches_direct_content_slot(tmp_path: Path) -> None:
    (tmp_path / "products" / "p").mkdir(parents=True)
    task = {
        "resolved_target": {"id": "group_01_slot_01", "target_type": "content_slot", "group_id": "b"},
        "execution_contract": {"contract_kind": "content_slot"},
    }
    ctx = OutcomeDeriveContext(
        repo_root=tmp_path,
        product_id="p",
        products_dir=None,
        scope_check={"scope_breach": True},
        invoke_data=None,
        task_data=task,
        builder_diff_summary=None,
        prior_resolved_target=task["resolved_target"],
    )
    a = derive_execution_outcome_for_kind("content_slot", ctx)
    b = derive_content_slot_execution_outcome(
        tmp_path,
        "p",
        products_dir=None,
        scope_check=ctx.scope_check,
        invoke_data=ctx.invoke_data,
        prior_resolved_target=ctx.prior_resolved_target,
        task_data=ctx.task_data,
    )
    assert a == b


def test_build_execution_contract_for_kind_content_slot(tmp_path: Path) -> None:
    (tmp_path / "products" / "wk").mkdir(parents=True)
    raw = {"schema": "argus.next_expansion.v1"}
    pt = {
        "target_type": "content_slot",
        "id": "group_01_slot_01",
        "group_id": "group_01_x",
    }
    ec = build_execution_contract_for_kind(
        "content_slot",
        repo_root=tmp_path,
        product_id="wk",
        products_dir=None,
        raw=raw,
        pt=pt,
    )
    assert ec.get("contract_kind") == "content_slot"
