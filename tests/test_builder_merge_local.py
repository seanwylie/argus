"""Local Builder merge helper (nested product git only, merge_candidate only)."""

from __future__ import annotations

import json
import shutil
import subprocess
from argparse import Namespace
from pathlib import Path

import pytest

from argus.builder.merge_local import (
    BUILDER_MERGE_RECORD_SCHEMA,
    merge_record_dir,
    run_builder_merge_local,
)
from argus.cli.builder_cmd import run_builder_subcommand


def _require_git() -> None:
    if not shutil.which("git"):
        pytest.skip("git not on PATH")


def _init_product_git(product_root: Path) -> None:
    subprocess.run(["git", "init"], cwd=product_root, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "t@e"],
        cwd=product_root,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "t"],
        cwd=product_root,
        check=True,
        capture_output=True,
    )


def test_merge_refused_not_merge_candidate(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def _fake_status(*_a: object, **_k: object) -> dict:
        return {
            "latest_reconcile": {
                "present": True,
                "review_status": "blocked",
                "review_builder_branch": "builder/x-1",
            }
        }

    monkeypatch.setattr(
        "argus.builder.merge_local.compute_builder_status",
        _fake_status,
    )
    rec, code = run_builder_merge_local(
        tmp_path,
        "p1",
        products_dir=None,
        into_branch="main",
        dry_run=True,
        no_record=True,
    )
    assert code == 2
    assert rec["merge_status"] == "refused"
    assert "merge_candidate" in (rec.get("refusal_reason") or "")


def test_merge_refused_unsafe(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def _fake_status(*_a: object, **_k: object) -> dict:
        return {
            "latest_reconcile": {
                "review_status": "unsafe",
                "review_builder_branch": "builder/x-1",
            }
        }

    monkeypatch.setattr(
        "argus.builder.merge_local.compute_builder_status",
        _fake_status,
    )
    rec, code = run_builder_merge_local(
        tmp_path,
        "p1",
        products_dir=None,
        into_branch="main",
        dry_run=True,
        no_record=True,
    )
    assert code == 2
    assert rec["merge_status"] == "refused"


def test_merge_dry_run_ok_writes_record(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _require_git()
    pid = "mgdry"
    pr = tmp_path / "products" / pid
    pr.mkdir(parents=True)
    _init_product_git(pr)
    (pr / "a.txt").write_text("1\n", encoding="utf-8")
    subprocess.run(["git", "add", "a.txt"], cwd=pr, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "i"],
        cwd=pr,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "branch", "-M", "main"],
        cwd=pr,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "checkout", "-b", "builder/mg-abc1234567"],
        cwd=pr,
        check=True,
        capture_output=True,
    )
    (pr / "a.txt").write_text("2\n", encoding="utf-8")
    subprocess.run(
        ["git", "commit", "-am", "b"],
        cwd=pr,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "checkout", "main"],
        cwd=pr,
        check=True,
        capture_output=True,
    )

    bb = "builder/mg-abc1234567"

    def _fake_status(*_a: object, **_k: object) -> dict:
        return {
            "latest_reconcile": {
                "review_status": "merge_candidate",
                "review_builder_branch": bb,
                "review_baseline_commit": "deadbeef",
                "review_changed_file_count": 1,
            }
        }

    monkeypatch.setattr(
        "argus.builder.merge_local.compute_builder_status",
        _fake_status,
    )

    inv = tmp_path / "runs" / "builder" / "invoke" / pid
    inv.mkdir(parents=True)
    (inv / "latest.json").write_text(
        json.dumps(
            {
                "git_baseline": {
                    "git_workspace_kind": "nested_product",
                    "git_cwd": str(pr.resolve()),
                }
            }
        ),
        encoding="utf-8",
    )

    rec, code = run_builder_merge_local(
        tmp_path,
        pid,
        products_dir=None,
        into_branch="main",
        dry_run=True,
        no_record=False,
    )
    assert code == 0
    assert rec["merge_status"] == "dry_run_ok"
    latest = merge_record_dir(tmp_path, pid) / "latest.json"
    assert latest.is_file()
    raw = json.loads(latest.read_text(encoding="utf-8"))
    assert raw["schema"] == BUILDER_MERGE_RECORD_SCHEMA
    assert raw["review_status_at_merge_time"] == "merge_candidate"


def test_merge_git_ok(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _require_git()
    pid = "mgok"
    pr = tmp_path / "products" / pid
    pr.mkdir(parents=True)
    _init_product_git(pr)
    (pr / "a.txt").write_text("1\n", encoding="utf-8")
    subprocess.run(["git", "add", "a.txt"], cwd=pr, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "i"],
        cwd=pr,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "branch", "-M", "main"],
        cwd=pr,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "checkout", "-b", "builder/mg-abc1234567"],
        cwd=pr,
        check=True,
        capture_output=True,
    )
    (pr / "a.txt").write_text("2\n", encoding="utf-8")
    subprocess.run(
        ["git", "commit", "-am", "b"],
        cwd=pr,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "checkout", "main"],
        cwd=pr,
        check=True,
        capture_output=True,
    )

    bb = "builder/mg-abc1234567"

    def _fake_status(*_a: object, **_k: object) -> dict:
        return {
            "latest_reconcile": {
                "review_status": "merge_candidate",
                "review_builder_branch": bb,
            }
        }

    monkeypatch.setattr(
        "argus.builder.merge_local.compute_builder_status",
        _fake_status,
    )

    inv = tmp_path / "runs" / "builder" / "invoke" / pid
    inv.mkdir(parents=True)
    (inv / "latest.json").write_text(
        json.dumps(
            {
                "git_baseline": {
                    "git_workspace_kind": "nested_product",
                    "git_cwd": str(pr.resolve()),
                }
            }
        ),
        encoding="utf-8",
    )

    rec, code = run_builder_merge_local(
        tmp_path,
        pid,
        products_dir=None,
        into_branch="main",
        dry_run=False,
        no_record=False,
    )
    assert code == 0, rec
    assert rec["merge_status"] == "ok"
    assert (pr / "a.txt").read_text(encoding="utf-8") == "2\n"


def test_merge_refused_dirty_tree(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _require_git()
    pid = "mgdirty"
    pr = tmp_path / "products" / pid
    pr.mkdir(parents=True)
    _init_product_git(pr)
    (pr / "a.txt").write_text("1\n", encoding="utf-8")
    subprocess.run(["git", "add", "a.txt"], cwd=pr, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "i"],
        cwd=pr,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "branch", "-M", "main"],
        cwd=pr,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "checkout", "-b", "builder/mg-abc1234567"],
        cwd=pr,
        check=True,
        capture_output=True,
    )
    (pr / "a.txt").write_text("2\n", encoding="utf-8")
    subprocess.run(
        ["git", "commit", "-am", "b"],
        cwd=pr,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "checkout", "main"],
        cwd=pr,
        check=True,
        capture_output=True,
    )
    (pr / "dirty.txt").write_text("x\n", encoding="utf-8")

    bb = "builder/mg-abc1234567"

    def _fake_status(*_a: object, **_k: object) -> dict:
        return {
            "latest_reconcile": {
                "review_status": "merge_candidate",
                "review_builder_branch": bb,
            }
        }

    monkeypatch.setattr(
        "argus.builder.merge_local.compute_builder_status",
        _fake_status,
    )

    inv = tmp_path / "runs" / "builder" / "invoke" / pid
    inv.mkdir(parents=True)
    (inv / "latest.json").write_text(
        json.dumps(
            {
                "git_baseline": {
                    "git_workspace_kind": "nested_product",
                    "git_cwd": str(pr.resolve()),
                }
            }
        ),
        encoding="utf-8",
    )

    rec, code = run_builder_merge_local(
        tmp_path,
        pid,
        products_dir=None,
        into_branch="main",
        dry_run=False,
        no_record=True,
    )
    assert code == 2
    assert rec["merge_status"] == "refused"
    assert "clean" in (rec.get("refusal_reason") or "").lower()


def test_cli_merge_json(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def _fake_merge(*_a: object, **_k: object) -> tuple:
        return (
            {
                "schema": BUILDER_MERGE_RECORD_SCHEMA,
                "merge_status": "refused",
                "review_status_at_merge_time": "blocked",
                "refusal_reason": "x",
            },
            2,
        )

    monkeypatch.setattr("argus.cli.builder_cmd.run_builder_merge_local", _fake_merge)
    monkeypatch.setattr("argus.cli.builder_cmd.repo_root", lambda: tmp_path)
    args = Namespace(
        builder_command="merge",
        product_id="p",
        json=True,
        products_dir=None,
        merge_into=None,
        merge_dry_run=False,
        merge_no_record=False,
    )
    assert run_builder_subcommand(args) == 2


def test_format_review_suggested_next_merge_candidate() -> None:
    from argus.builder.branch_review import (
        compute_builder_branch_review,
        format_builder_branch_review_human,
    )

    inv = {
        "execution_backend": "agent",
        "mode": "execute",
        "invocation_status": "ok",
        "git_branch_isolation": {
            "branch_isolation_status": "ok",
            "git_builder_branch": "builder/z-1",
            "trust_degraded_dirty_tree": False,
        },
        "git_baseline": {"baseline_commit": "abc"},
        "builder_containment": {
            "containment_applied": "bwrap",
            "containment_fallback_used": False,
            "trust_degraded_unsandboxed": False,
        },
    }
    sc = {
        "schema": "argus.builder_scope_check.v2",
        "scope_breach": False,
        "path_scope": {},
    }
    eo = {"outcome": "completed"}
    bds = {"changed_file_count": 1, "fallback_used": False}
    br = compute_builder_branch_review(
        invoke_data=inv,
        scope_check=sc,
        execution_outcome=eo,
        builder_diff_summary=bds,
    )
    block = {
        "review_status": br["review_status"],
        "review_reasons": br["review_reasons"],
        "review_builder_branch": br["builder_branch"],
        "review_baseline_commit": br["baseline_commit"],
        "review_changed_file_count": br["changed_file_count"],
        "review_trust_degraded_dirty_tree": br["trust_degraded_dirty_tree"],
    }
    text = format_builder_branch_review_human(block, product_id="demo-product")
    assert "suggested_next" in text
    assert "argus builder merge demo-product" in text
