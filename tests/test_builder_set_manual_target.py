"""Manual Builder target CLI (bug_fix / signal_instrumentation)."""

from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path

import pytest

from argus.builder.contract_registry import manual_set_target_kinds
from argus.builder.next_expansion_prepare import (
    NextExpansionPrepareError,
    load_next_expansion,
    validate_next_expansion_payload,
)
from argus.builder.set_manual_target import (
    MANUAL_TARGET_KINDS,
    apply_manual_builder_target,
    build_primary_target_bug_fix,
    build_primary_target_signal_instrumentation,
)
from argus.cli.builder_cmd import run_builder_subcommand


def test_manual_target_kinds_alias_matches_registry() -> None:
    assert MANUAL_TARGET_KINDS == manual_set_target_kinds()


def test_validate_next_expansion_payload_matches_load(tmp_path: Path) -> None:
    p = tmp_path / "products" / "p" / "content" / "next_expansion.json"
    p.parent.mkdir(parents=True)
    raw = {
        "schema": "argus.next_expansion.v1",
        "primary_target": {
            "target_type": "bug_fix",
            "id": "b1",
            "allowed_paths_exact": ["x.ts"],
            "bug_statement": "x",
        },
    }
    p.write_text(json.dumps(raw), encoding="utf-8")
    a = validate_next_expansion_payload(raw)
    b = load_next_expansion(tmp_path, "p")
    assert a["primary_target"]["id"] == b["primary_target"]["id"]


def test_apply_manual_preserves_explicit_non_targets(tmp_path: Path) -> None:
    p = tmp_path / "products" / "wk" / "content" / "next_expansion.json"
    p.parent.mkdir(parents=True)
    p.write_text(
        json.dumps(
            {
                "schema": "argus.next_expansion.v1",
                "explicit_non_targets": [{"id": "other", "reason": "later"}],
                "primary_target": {
                    "target_type": "content_slot",
                    "id": "group_01_slot_01",
                    "group_id": "b",
                },
            }
        ),
        encoding="utf-8",
    )
    pt = build_primary_target_bug_fix(
        target_id="fix-1",
        allowed_paths_exact=["app/a.ts"],
        bug_statement="crash",
    )
    res = apply_manual_builder_target(
        tmp_path,
        "wk",
        products_dir=None,
        primary_target=pt,
        dry_run=False,
    )
    assert res.wrote
    out = json.loads(res.path.read_text(encoding="utf-8"))
    assert out["explicit_non_targets"][0]["id"] == "other"
    assert out["primary_target"]["target_type"] == "bug_fix"


def test_build_primary_target_signal_optional_lists(tmp_path: Path) -> None:
    (tmp_path / "products" / "wk" / "content").mkdir(parents=True)
    pt = build_primary_target_signal_instrumentation(
        target_id="sig-1",
        allowed_paths_exact=["lib/h.ts"],
        signal_statement="add counter",
        expected_product_paths_exist=["config/x.json"],
        instrumentation_touch_paths=["lib/*.ts"],
    )
    res = apply_manual_builder_target(
        tmp_path,
        "wk",
        products_dir=None,
        primary_target=pt,
        dry_run=True,
    )
    assert not res.wrote
    assert res.payload["primary_target"]["instrumentation_touch_paths"] == ["lib/*.ts"]


def test_cli_set_target_bug_fix_writes_next_expansion(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "products" / "cli_bf" / "content").mkdir(parents=True)
    monkeypatch.setattr("argus.cli.builder_cmd.repo_root", lambda: tmp_path)
    args = Namespace(
        builder_command="set-target",
        product_id="cli_bf",
        kind="bug_fix",
        target_id="login-bug",
        allow_paths=["app/site/index.html"],
        bug_statement="wrong redirect",
        signal_statement=None,
        path_patterns=None,
        success_condition=None,
        stop_condition=None,
        expect_paths=None,
        touch_paths=None,
        no_stamp_time=False,
        dry_run=False,
        json=False,
        products_dir=None,
        prepare=False,
        prepare_output="product",
    )
    assert run_builder_subcommand(args) == 0
    ne = tmp_path / "products" / "cli_bf" / "content" / "next_expansion.json"
    assert ne.is_file()
    raw = json.loads(ne.read_text(encoding="utf-8"))
    assert raw["primary_target"]["target_type"] == "bug_fix"
    assert raw["primary_target"]["bug_statement"] == "wrong redirect"


def test_cli_set_target_requires_bug_statement(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "products" / "cli_bf2" / "content").mkdir(parents=True)
    monkeypatch.setattr("argus.cli.builder_cmd.repo_root", lambda: tmp_path)
    args = Namespace(
        builder_command="set-target",
        product_id="cli_bf2",
        kind="bug_fix",
        target_id="x",
        allow_paths=["a.ts"],
        bug_statement=None,
        signal_statement=None,
        path_patterns=None,
        success_condition=None,
        stop_condition=None,
        expect_paths=None,
        touch_paths=None,
        no_stamp_time=False,
        dry_run=False,
        json=False,
        products_dir=None,
        prepare=False,
        prepare_output="product",
    )
    assert run_builder_subcommand(args) == 2


def test_cli_set_target_signal_instrumentation_dry_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "products" / "cli_sig" / "content").mkdir(parents=True)
    monkeypatch.setattr("argus.cli.builder_cmd.repo_root", lambda: tmp_path)
    args = Namespace(
        builder_command="set-target",
        product_id="cli_sig",
        kind="signal_instrumentation",
        target_id="hook-1",
        allow_paths=["app/instrument.ts"],
        bug_statement=None,
        signal_statement="emit retry metric",
        path_patterns=None,
        success_condition=None,
        stop_condition=None,
        expect_paths=None,
        touch_paths=None,
        no_stamp_time=False,
        dry_run=True,
        json=True,
        products_dir=None,
        prepare=False,
        prepare_output="product",
    )
    assert run_builder_subcommand(args) == 0
    ne = tmp_path / "products" / "cli_sig" / "content" / "next_expansion.json"
    assert not ne.is_file()


def test_cli_set_target_prepare_writes_prompt_and_task(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "products" / "prep_x" / "content").mkdir(parents=True)
    monkeypatch.setattr("argus.cli.builder_cmd.repo_root", lambda: tmp_path)
    args = Namespace(
        builder_command="set-target",
        product_id="prep_x",
        kind="bug_fix",
        target_id="b-1",
        allow_paths=["app/x.ts"],
        bug_statement="crash",
        signal_statement=None,
        path_patterns=None,
        success_condition=None,
        stop_condition=None,
        expect_paths=None,
        touch_paths=None,
        no_stamp_time=False,
        dry_run=False,
        json=False,
        products_dir=None,
        prepare=True,
        prepare_output="product",
    )
    assert run_builder_subcommand(args) == 0
    gen = tmp_path / "products" / "prep_x" / "generated"
    assert (gen / "builder_next_prompt.md").is_file()
    assert (gen / "builder_task.json").is_file()


def test_cli_set_target_prepare_failure_returns_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "products" / "prep_fail" / "content").mkdir(parents=True)
    monkeypatch.setattr("argus.cli.builder_cmd.repo_root", lambda: tmp_path)

    def _raise(*_a: object, **_k: object) -> None:
        raise NextExpansionPrepareError("simulated prepare failure")

    monkeypatch.setattr("argus.cli.builder_cmd.write_prepare_artifacts", _raise)
    args = Namespace(
        builder_command="set-target",
        product_id="prep_fail",
        kind="bug_fix",
        target_id="b-1",
        allow_paths=["app/x.ts"],
        bug_statement="crash",
        signal_statement=None,
        path_patterns=None,
        success_condition=None,
        stop_condition=None,
        expect_paths=None,
        touch_paths=None,
        no_stamp_time=False,
        dry_run=False,
        json=False,
        products_dir=None,
        prepare=True,
        prepare_output="product",
    )
    assert run_builder_subcommand(args) == 1
    err = capsys.readouterr().err
    assert "prepare failed after target write succeeded" in err
    assert "next_expansion.json is at" in err
    ne = tmp_path / "products" / "prep_fail" / "content" / "next_expansion.json"
    assert ne.is_file()


def test_cli_set_target_prepare_ignored_with_dry_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "products" / "prep_dr" / "content").mkdir(parents=True)
    monkeypatch.setattr("argus.cli.builder_cmd.repo_root", lambda: tmp_path)
    args = Namespace(
        builder_command="set-target",
        product_id="prep_dr",
        kind="bug_fix",
        target_id="b-1",
        allow_paths=["app/x.ts"],
        bug_statement="crash",
        signal_statement=None,
        path_patterns=None,
        success_condition=None,
        stop_condition=None,
        expect_paths=None,
        touch_paths=None,
        no_stamp_time=False,
        dry_run=True,
        json=False,
        products_dir=None,
        prepare=True,
        prepare_output="product",
    )
    assert run_builder_subcommand(args) == 0
    assert "--prepare ignored" in capsys.readouterr().err
    assert not (tmp_path / "products" / "prep_dr" / "content" / "next_expansion.json").is_file()


def test_apply_invalid_contract_raises(tmp_path: Path) -> None:
    pt = build_primary_target_bug_fix(
        target_id="",
        allowed_paths_exact=["x.ts"],
        bug_statement="n",
    )
    with pytest.raises(NextExpansionPrepareError):
        apply_manual_builder_target(
            tmp_path,
            "p",
            products_dir=None,
            primary_target=pt,
            dry_run=True,
        )
