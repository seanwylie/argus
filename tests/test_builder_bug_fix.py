"""Bug-fix execution contract (first non–content_slot Builder contract)."""

from __future__ import annotations

import json

import pytest

from argus.builder.bug_fix_outcome import derive_bug_fix_execution_outcome
from argus.builder.execution_contract import (
    build_bug_fix_execution_contract,
    build_builder_scope_check,
    path_allowed_by_contract,
)
from argus.builder.next_expansion_prepare import (
    NextExpansionPrepareError,
    build_prepare_result,
    load_next_expansion,
)
from argus.builder.reconcile import _generate_next_expansion_result


def test_build_bug_fix_execution_contract_paths(tmp_path) -> None:
    (tmp_path / "products" / "p").mkdir(parents=True)
    raw: dict = {"schema": "argus.next_expansion.v1"}
    pt = {
        "target_type": "bug_fix",
        "id": "fix-404",
        "allowed_paths_exact": ["app/x.ts", "content/y.md"],
        "bug_statement": "404 on /foo",
        "success_condition": "Route returns 200",
        "forbidden_changes": ["unrelated modules"],
    }
    ec = build_bug_fix_execution_contract(
        repo_root=tmp_path,
        product_id="p",
        products_dir=None,
        raw=raw,
        pt=pt,
    )
    assert ec["contract_kind"] == "bug_fix"
    assert ec["bug_id"] == "fix-404"
    assert "app/x.ts" in ec["allowed_paths_exact"]
    assert ec["next_expansion_policy"] == "do_not_change_primary_target"
    assert path_allowed_by_contract("app/x.ts", ec)
    assert not path_allowed_by_contract("other/z.ts", ec)


def test_build_bug_fix_execution_contract_requires_paths(tmp_path) -> None:
    (tmp_path / "products" / "p").mkdir(parents=True)
    with pytest.raises(ValueError, match="allowed_paths_exact"):
        build_bug_fix_execution_contract(
            repo_root=tmp_path,
            product_id="p",
            products_dir=None,
            raw={},
            pt={"target_type": "bug_fix", "id": "x", "allowed_paths_exact": []},
        )


def test_load_next_expansion_bug_fix_requires_allowed_paths(tmp_path) -> None:
    ne = tmp_path / "products" / "p" / "content" / "next_expansion.json"
    ne.parent.mkdir(parents=True)
    ne.write_text(
        json.dumps(
            {
                "schema": "argus.next_expansion.v1",
                "primary_target": {
                    "target_type": "bug_fix",
                    "id": "b1",
                    "allowed_paths_exact": [],
                },
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(NextExpansionPrepareError, match="allowed_paths_exact"):
        load_next_expansion(tmp_path, "p", products_dir=None)


def test_prepare_bug_fix_embeds_contract(tmp_path) -> None:
    ne = tmp_path / "products" / "demo-product" / "content" / "next_expansion.json"
    ne.parent.mkdir(parents=True)
    ne.write_text(
        json.dumps(
            {
                "schema": "argus.next_expansion.v1",
                "primary_target": {
                    "target_type": "bug_fix",
                    "id": "login-redirect",
                    "group_id": None,
                    "allowed_paths_exact": ["app/site/index.html"],
                    "bug_statement": "Redirect drops query",
                    "rationale": "User-reported",
                },
            }
        ),
        encoding="utf-8",
    )
    res = build_prepare_result(tmp_path, "demo-product", under="product", products_dir=None)
    assert "bug fix" in res.markdown.lower()
    assert "login-redirect" in res.markdown
    assert res.task.get("execution_contract", {}).get("contract_kind") == "bug_fix"
    assert "temporary law" not in res.markdown.lower()  # prompt is bounded, not generic advice


def test_bug_fix_outcome_completed_on_invoke_and_diff() -> None:
    task = {
        "resolved_target": {"id": "x", "target_type": "bug_fix"},
        "execution_contract": {"contract_kind": "bug_fix", "bug_id": "x"},
    }
    r = derive_bug_fix_execution_outcome(
        scope_check={"scope_breach": False},
        invoke_data={"invocation_status": "ok", "mode": "execute", "exit_code": 0},
        task_data=task,
        builder_diff_summary={"changed_file_count": 2, "source": "invoke_baseline"},
    )
    assert r["outcome"] == "completed"


def test_bug_fix_outcome_partial_no_changes() -> None:
    task = {
        "resolved_target": {"id": "x", "target_type": "bug_fix"},
        "execution_contract": {"contract_kind": "bug_fix"},
    }
    r = derive_bug_fix_execution_outcome(
        scope_check={"scope_breach": False},
        invoke_data={"invocation_status": "ok"},
        task_data=task,
        builder_diff_summary={"changed_file_count": 0},
    )
    assert r["outcome"] == "partial"


def test_bug_fix_outcome_breached_on_scope() -> None:
    task = {
        "resolved_target": {"id": "x", "target_type": "bug_fix"},
        "execution_contract": {"contract_kind": "bug_fix"},
    }
    r = derive_bug_fix_execution_outcome(
        scope_check={"scope_breach": True},
        invoke_data={"invocation_status": "ok"},
        task_data=task,
        builder_diff_summary={"changed_file_count": 1},
    )
    assert r["outcome"] == "breached"


def test_bug_fix_scope_check_respects_allowed_paths(tmp_path) -> None:
    (tmp_path / "products" / "p" / "content").mkdir(parents=True)
    ne = tmp_path / "products" / "p" / "content" / "next_expansion.json"
    ne.write_text(
        json.dumps(
            {
                "schema": "argus.next_expansion.v1",
                "primary_target": {
                    "target_type": "bug_fix",
                    "id": "z",
                    "allowed_paths_exact": ["ok.txt"],
                },
            }
        ),
        encoding="utf-8",
    )
    ec = build_bug_fix_execution_contract(
        repo_root=tmp_path,
        product_id="p",
        products_dir=None,
        raw={},
        pt={
            "target_type": "bug_fix",
            "id": "z",
            "allowed_paths_exact": ["ok.txt"],
        },
    )
    task = {"execution_contract": ec, "resolved_target": {"id": "z", "target_type": "bug_fix"}}
    sc = build_builder_scope_check(
        tmp_path,
        product_id="p",
        products_dir=None,
        task_data=task,
        changed_paths_repo_relative=["products/p/ok.txt"],
    )
    assert not sc.get("scope_breach")
    sc2 = build_builder_scope_check(
        tmp_path,
        product_id="p",
        products_dir=None,
        task_data=task,
        changed_paths_repo_relative=["products/p/bad.txt"],
    )
    assert sc2.get("scope_breach")


def test_generate_next_expansion_skips_bug_fix_target(tmp_path) -> None:
    (tmp_path / "products" / "demo-product" / "content").mkdir(parents=True)
    ne = tmp_path / "products" / "demo-product" / "content" / "next_expansion.json"
    ne.write_text(
        json.dumps(
            {
                "schema": "argus.next_expansion.v1",
                "primary_target": {
                    "target_type": "bug_fix",
                    "id": "b",
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
    assert r["reason"] == "generate_next_expansion_skipped_for_bug_fix_manual_target"
