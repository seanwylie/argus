"""Signal instrumentation execution contract (second non–content_slot Builder contract)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from argus.builder.execution_contract import (
    build_builder_scope_check,
    build_signal_instrumentation_execution_contract,
    path_allowed_by_contract,
)
from argus.builder.next_expansion_prepare import (
    NextExpansionPrepareError,
    build_prepare_result,
    load_next_expansion,
)
from argus.builder.reconcile import _generate_next_expansion_result
from argus.builder.signal_instrumentation_outcome import (
    derive_signal_instrumentation_execution_outcome,
)


def test_build_signal_instrumentation_contract(tmp_path: Path) -> None:
    (tmp_path / "products" / "p").mkdir(parents=True)
    pt = {
        "target_type": "signal_instrumentation",
        "id": "sig-hooks-1",
        "allowed_paths_exact": ["lib/signals.ts", "config/x.json"],
        "signal_statement": "Emit foo_bar on retry",
        "expected_product_paths_exist": ["config/x.json"],
        "instrumentation_touch_paths": ["lib/*.ts"],
    }
    ec = build_signal_instrumentation_execution_contract(
        repo_root=tmp_path,
        product_id="p",
        products_dir=None,
        raw={},
        pt=pt,
    )
    assert ec["contract_kind"] == "signal_instrumentation"
    assert ec["signal_id"] == "sig-hooks-1"
    assert ec["expected_product_paths_exist"] == ["config/x.json"]
    assert ec["instrumentation_touch_paths"] == ["lib/*.ts"]
    assert path_allowed_by_contract("lib/signals.ts", ec)


def test_prepare_signal_instrumentation_embeds_contract(tmp_path: Path) -> None:
    ne = tmp_path / "products" / "demo-product" / "content" / "next_expansion.json"
    ne.parent.mkdir(parents=True)
    ne.write_text(
        json.dumps(
            {
                "schema": "argus.next_expansion.v1",
                "primary_target": {
                    "target_type": "signal_instrumentation",
                    "id": "emit-retry-v1",
                    "group_id": None,
                    "allowed_paths_exact": ["app/instrumentation.ts"],
                    "signal_statement": "Hook retry counter",
                    "instrumentation_touch_paths": ["app/instrumentation.ts"],
                },
            }
        ),
        encoding="utf-8",
    )
    res = build_prepare_result(tmp_path, "demo-product", under="product", products_dir=None)
    assert "signal instrumentation" in res.markdown.lower()
    assert "emit-retry-v1" in res.markdown
    assert "broad observability" in res.markdown.lower()
    assert res.task.get("execution_contract", {}).get("contract_kind") == "signal_instrumentation"


def test_signal_instrumentation_outcome_completed_with_postconditions(tmp_path: Path) -> None:
    (tmp_path / "products" / "p").mkdir(parents=True)
    (tmp_path / "products" / "p" / "config").mkdir(parents=True)
    (tmp_path / "products" / "p" / "config" / "x.json").write_text("{}", encoding="utf-8")
    task = {
        "resolved_target": {"id": "s1", "target_type": "signal_instrumentation"},
        "execution_contract": {
            "contract_kind": "signal_instrumentation",
            "signal_id": "s1",
            "expected_product_paths_exist": ["config/x.json"],
            "instrumentation_touch_paths": ["config/*.json"],
        },
    }
    r = derive_signal_instrumentation_execution_outcome(
        repo_root=tmp_path,
        product_id="p",
        products_dir=None,
        scope_check={"scope_breach": False},
        invoke_data={"invocation_status": "ok", "mode": "execute", "exit_code": 0},
        task_data=task,
        builder_diff_summary={
            "changed_file_count": 1,
            "source": "invoke_baseline",
            "changed_files_argus_relative": ["products/p/config/x.json"],
        },
    )
    assert r["outcome"] == "completed"
    fe = r.get("file_evidence") or {}
    assert fe.get("postcondition_paths_missing") == []


def test_signal_instrumentation_outcome_partial_missing_postcondition(tmp_path: Path) -> None:
    (tmp_path / "products" / "p").mkdir(parents=True)
    task = {
        "resolved_target": {"id": "s1", "target_type": "signal_instrumentation"},
        "execution_contract": {
            "contract_kind": "signal_instrumentation",
            "expected_product_paths_exist": ["missing.json"],
        },
    }
    r = derive_signal_instrumentation_execution_outcome(
        repo_root=tmp_path,
        product_id="p",
        products_dir=None,
        scope_check={"scope_breach": False},
        invoke_data={"invocation_status": "ok"},
        task_data=task,
        builder_diff_summary={
            "changed_file_count": 1,
            "changed_files_argus_relative": ["products/p/a.ts"],
        },
    )
    assert r["outcome"] == "partial"
    assert "missing.json" in str(r.get("reasons"))


def test_signal_instrumentation_outcome_partial_touch_mismatch(tmp_path: Path) -> None:
    (tmp_path / "products" / "p").mkdir(parents=True)
    task = {
        "resolved_target": {"id": "s1", "target_type": "signal_instrumentation"},
        "execution_contract": {
            "contract_kind": "signal_instrumentation",
            "instrumentation_touch_paths": ["lib/*.ts"],
        },
    }
    r = derive_signal_instrumentation_execution_outcome(
        repo_root=tmp_path,
        product_id="p",
        products_dir=None,
        scope_check={"scope_breach": False},
        invoke_data={"invocation_status": "ok"},
        task_data=task,
        builder_diff_summary={
            "changed_file_count": 1,
            "changed_files_argus_relative": ["products/p/other/z.md"],
        },
    )
    assert r["outcome"] == "partial"


def test_signal_instrumentation_scope_check(tmp_path: Path) -> None:
    (tmp_path / "products" / "p" / "content").mkdir(parents=True)
    ne = tmp_path / "products" / "p" / "content" / "next_expansion.json"
    ne.write_text(
        json.dumps(
            {
                "schema": "argus.next_expansion.v1",
                "primary_target": {
                    "target_type": "signal_instrumentation",
                    "id": "z",
                    "allowed_paths_exact": ["ok.txt"],
                },
            }
        ),
        encoding="utf-8",
    )
    ec = build_signal_instrumentation_execution_contract(
        repo_root=tmp_path,
        product_id="p",
        products_dir=None,
        raw={},
        pt={"target_type": "signal_instrumentation", "id": "z", "allowed_paths_exact": ["ok.txt"]},
    )
    task = {"execution_contract": ec, "resolved_target": {"id": "z", "target_type": "signal_instrumentation"}}
    sc = build_builder_scope_check(
        tmp_path,
        product_id="p",
        products_dir=None,
        task_data=task,
        changed_paths_repo_relative=["products/p/ok.txt"],
    )
    assert not sc.get("scope_breach")


def test_generate_next_expansion_skips_signal_instrumentation_target(tmp_path: Path) -> None:
    (tmp_path / "products" / "demo-product" / "content").mkdir(parents=True)
    ne = tmp_path / "products" / "demo-product" / "content" / "next_expansion.json"
    ne.write_text(
        json.dumps(
            {
                "schema": "argus.next_expansion.v1",
                "primary_target": {
                    "target_type": "signal_instrumentation",
                    "id": "s",
                    "allowed_paths_exact": ["app/x.ts"],
                },
            }
        ),
        encoding="utf-8",
    )
    r = _generate_next_expansion_result(
        repo_root=tmp_path,
        product_id="demo-product",
        products_dir=None,
        generate_next_expansion=True,
    )
    assert r["status"] == "skipped"
    assert r["reason"] == "generate_next_expansion_skipped_for_signal_instrumentation_manual_target"


def test_load_next_expansion_signal_requires_paths(tmp_path: Path) -> None:
    ne = tmp_path / "products" / "p" / "content" / "next_expansion.json"
    ne.parent.mkdir(parents=True)
    ne.write_text(
        json.dumps(
            {
                "schema": "argus.next_expansion.v1",
                "primary_target": {
                    "target_type": "signal_instrumentation",
                    "id": "b1",
                    "allowed_paths_exact": [],
                },
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(NextExpansionPrepareError, match="allowed_paths_exact"):
        load_next_expansion(tmp_path, "p", products_dir=None)


def test_status_payload_accepts_signal_instrumentation_reconcile(tmp_path: Path) -> None:
    """Operator/status paths are contract-kind agnostic; smoke-test signal_instrumentation record."""
    from argus.builder.status import compute_builder_status

    pid = "sigprod"
    (tmp_path / "products" / pid / "content").mkdir(parents=True)
    (tmp_path / "products" / pid / "content" / "next_expansion.json").write_text(
        json.dumps(
            {
                "schema": "argus.next_expansion.v1",
                "primary_target": {
                    "target_type": "signal_instrumentation",
                    "id": "hook-a",
                    "allowed_paths_exact": ["x.ts"],
                },
            }
        ),
        encoding="utf-8",
    )
    gen = tmp_path / "products" / pid / "generated"
    gen.mkdir(parents=True)
    (gen / "builder_next_prompt.md").write_text("# x\n", encoding="utf-8")
    (gen / "builder_task.json").write_text(
        json.dumps(
            {
                "schema": "argus.builder_prepare_task.v1",
                "resolved_target": {"id": "hook-a", "target_type": "signal_instrumentation"},
                "execution_contract": {"contract_kind": "signal_instrumentation"},
            }
        ),
        encoding="utf-8",
    )
    inv = tmp_path / "runs" / "builder" / "invoke" / pid
    inv.mkdir(parents=True)
    (inv / "latest.json").write_text(
        json.dumps(
            {
                "schema": "argus.builder_invoke_record.v1",
                "product_id": pid,
                "mode": "review",
                "invocation_status": "not_executed",
                "resolved_target": {"id": "hook-a", "target_type": "signal_instrumentation"},
            }
        ),
        encoding="utf-8",
    )
    recd = tmp_path / "runs" / "builder" / "reconcile" / pid
    recd.mkdir(parents=True)
    (recd / "latest.json").write_text(
        json.dumps(
            {
                "schema": "argus.builder_reconcile_record.v1",
                "product_id": pid,
                "current_target": {"id": "hook-a", "target_type": "signal_instrumentation"},
                "execution_outcome": {
                    "schema": "argus.builder.execution_outcome.v1",
                    "outcome": "partial",
                    "reasons": ["smoke"],
                },
            }
        ),
        encoding="utf-8",
    )
    out = compute_builder_status(tmp_path, pid)
    assert out.get("latest_reconcile", {}).get("execution_outcome") == "partial"
