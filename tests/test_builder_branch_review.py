"""Builder branch merge-readiness classification (local, conservative)."""

from __future__ import annotations

from argparse import Namespace

import pytest

import tests.test_builder_status as builder_status_fixtures
from argus.builder.branch_review import (
    REVIEW_BLOCKED,
    REVIEW_MERGE_CANDIDATE,
    REVIEW_REVIEW_REQUIRED,
    REVIEW_UNSAFE,
    compute_builder_branch_review,
)


def _inv_execute_ok(*, branch_ok: bool = True, dirty: bool = False) -> dict:
    gbi: dict = {
        "schema": "argus.builder.git_branch_isolation.v1",
        "branch_isolation_status": "ok" if branch_ok else "degraded_dirty_tree",
        "git_builder_branch": "builder/s1-abc1234567",
        "git_branch_before": "main",
        "git_branch_created": True,
        "trust_degraded_dirty_tree": dirty,
        "branch_isolation_error": None,
    }
    return {
        "schema": "argus.builder_invoke_record.v1",
        "execution_backend": "agent",
        "mode": "execute",
        "invocation_status": "ok",
        "git_branch_isolation": gbi,
        "git_baseline": {
            "schema": "argus.builder.git_baseline.v1",
            "baseline_commit": "deadbeef",
            "git_cwd": "/tmp/p",
        },
        "builder_containment": {
            "schema": "argus.builder.containment.v1",
            "containment_requested": "auto",
            "containment_applied": "bwrap",
            "containment_fallback_used": False,
            "trust_degraded_unsandboxed": False,
            "no_new_privs_requested": False,
            "no_new_privs_applied": False,
            "no_new_privs_reason": "non_linux_skip",
        },
    }


def _scope_clean() -> dict:
    return {
        "schema": "argus.builder_scope_check.v2",
        "status": "ok",
        "scope_breach": False,
        "path_scope_breach": False,
        "semantic_scope_breach": False,
        "path_scope": {},
    }


def _scope_unsafe_breach() -> dict:
    return {
        "schema": "argus.builder_scope_check.v2",
        "status": "breach",
        "scope_breach": True,
        "path_scope_breach": True,
        "semantic_scope_breach": False,
        "breach_reasons": ["path_scope:forbidden_path"],
        "path_scope": {"argus_core_breach": True},
    }


def _eo(outcome: str) -> dict:
    return {"schema": "argus.builder.execution_outcome.v1", "outcome": outcome, "reasons": []}


def _bds(*, fallback: bool = False, count: int = 2) -> dict:
    return {
        "schema": "argus.builder.git_diff_summary.v1",
        "changed_file_count": count,
        "fallback_used": fallback,
        "source": "invoke_baseline",
    }


def test_merge_candidate_requires_no_new_privs_when_requested() -> None:
    inv = _inv_execute_ok()
    inv["builder_containment"] = {
        "schema": "argus.builder.containment.v1",
        "containment_applied": "bwrap",
        "trust_degraded_unsandboxed": False,
        "containment_fallback_used": False,
        "no_new_privs_requested": True,
        "no_new_privs_applied": False,
        "no_new_privs_reason": "setpriv_not_found_on_path",
    }
    r = compute_builder_branch_review(
        invoke_data=inv,
        scope_check=_scope_clean(),
        execution_outcome=_eo("completed"),
        builder_diff_summary=_bds(),
    )
    assert r["review_status"] == REVIEW_REVIEW_REQUIRED
    assert any("no_new_privs" in x for x in r["review_reasons"])


def test_merge_candidate_requires_agent_bwrap() -> None:
    inv = _inv_execute_ok()
    inv["builder_containment"] = {
        "schema": "argus.builder.containment.v1",
        "containment_applied": "none",
        "trust_degraded_unsandboxed": True,
    }
    r = compute_builder_branch_review(
        invoke_data=inv,
        scope_check=_scope_clean(),
        execution_outcome=_eo("completed"),
        builder_diff_summary=_bds(),
    )
    assert r["review_status"] == REVIEW_REVIEW_REQUIRED


def test_merge_candidate_requires_product_scoped_fs_not_legacy_repo_rw() -> None:
    inv = _inv_execute_ok()
    inv["builder_containment"] = {
        "schema": "argus.builder.containment.v1",
        "containment_applied": "bwrap",
        "trust_degraded_unsandboxed": False,
        "containment_fallback_used": False,
        "filesystem_scope_mode": "legacy_repo_rw",
        "no_new_privs_requested": False,
        "no_new_privs_applied": False,
    }
    r = compute_builder_branch_review(
        invoke_data=inv,
        scope_check=_scope_clean(),
        execution_outcome=_eo("completed"),
        builder_diff_summary=_bds(),
    )
    assert r["review_status"] == REVIEW_REVIEW_REQUIRED
    assert any("legacy_repo_rw" in x for x in r["review_reasons"])


def test_merge_candidate_completed_clean_isolated() -> None:
    r = compute_builder_branch_review(
        invoke_data=_inv_execute_ok(),
        scope_check=_scope_clean(),
        execution_outcome=_eo("completed"),
        builder_diff_summary=_bds(),
    )
    assert r["review_status"] == REVIEW_MERGE_CANDIDATE
    assert r["builder_branch"] == "builder/s1-abc1234567"
    assert r["baseline_commit"] == "deadbeef"
    assert r["changed_file_count"] == 2
    assert r["trust_degraded_dirty_tree"] is False


def test_review_required_dirty_tree() -> None:
    r = compute_builder_branch_review(
        invoke_data=_inv_execute_ok(branch_ok=False, dirty=True),
        scope_check=_scope_clean(),
        execution_outcome=_eo("completed"),
        builder_diff_summary=_bds(),
    )
    assert r["review_status"] == REVIEW_REVIEW_REQUIRED
    assert any("dirty" in x for x in r["review_reasons"])


def test_blocked_partial() -> None:
    r = compute_builder_branch_review(
        invoke_data=_inv_execute_ok(),
        scope_check=_scope_clean(),
        execution_outcome=_eo("partial"),
        builder_diff_summary=_bds(),
    )
    assert r["review_status"] == REVIEW_BLOCKED
    assert any("partial" in x for x in r["review_reasons"])


def test_blocked_invoke_failed() -> None:
    inv = _inv_execute_ok()
    inv["invocation_status"] = "failed"
    r = compute_builder_branch_review(
        invoke_data=inv,
        scope_check=_scope_clean(),
        execution_outcome=_eo("completed"),
        builder_diff_summary=_bds(),
    )
    assert r["review_status"] == REVIEW_BLOCKED


def test_unsafe_scope_breach() -> None:
    r = compute_builder_branch_review(
        invoke_data=_inv_execute_ok(),
        scope_check=_scope_unsafe_breach(),
        execution_outcome=_eo("completed"),
        builder_diff_summary=_bds(),
    )
    assert r["review_status"] == REVIEW_UNSAFE


def test_unsafe_execution_outcome_breached() -> None:
    r = compute_builder_branch_review(
        invoke_data=_inv_execute_ok(),
        scope_check=_scope_clean(),
        execution_outcome=_eo("breached"),
        builder_diff_summary=_bds(),
    )
    assert r["review_status"] == REVIEW_UNSAFE


def test_review_required_unknown_outcome() -> None:
    r = compute_builder_branch_review(
        invoke_data=_inv_execute_ok(),
        scope_check=_scope_clean(),
        execution_outcome=_eo("unknown"),
        builder_diff_summary=_bds(),
    )
    assert r["review_status"] == REVIEW_REVIEW_REQUIRED


def test_review_required_diff_fallback() -> None:
    r = compute_builder_branch_review(
        invoke_data=_inv_execute_ok(),
        scope_check=_scope_clean(),
        execution_outcome=_eo("completed"),
        builder_diff_summary=_bds(fallback=True),
    )
    assert r["review_status"] == REVIEW_REVIEW_REQUIRED
    assert any("fallback" in x for x in r["review_reasons"])


def test_argus_root_skipped_branch_isolation_is_review_required() -> None:
    inv = _inv_execute_ok()
    inv["git_branch_isolation"] = {
        "branch_isolation_status": "skipped_not_nested_product_repo",
        "git_builder_branch": None,
        "trust_degraded_dirty_tree": False,
    }
    inv["git_baseline"] = {
        "schema": "argus.builder.git_baseline.v1",
        "git_workspace_kind": "argus_root",
        "baseline_commit": "deadbeef",
        "git_cwd": "/tmp/p",
    }
    inv["builder_containment"] = {
        **inv["builder_containment"],
        "trust_degraded_workspace_scope": True,
        "filesystem_scope_mode": "legacy_repo_rw",
    }
    r = compute_builder_branch_review(
        invoke_data=inv,
        scope_check=_scope_clean(),
        execution_outcome=_eo("completed"),
        builder_diff_summary=_bds(),
    )
    assert r["review_status"] == REVIEW_REVIEW_REQUIRED
    assert any("argus_root" in x for x in r["review_reasons"])
    assert r.get("git_workspace_kind") == "argus_root"
    assert r.get("trust_degraded_workspace_scope") is True


def test_merge_candidate_worktree_review_required_when_landlock_requested_not_applied() -> None:
    inv = _inv_execute_ok()
    inv["git_baseline"] = {
        "schema": "argus.builder.git_baseline.v1",
        "git_workspace_kind": "argus_root_worktree",
        "baseline_commit": "deadbeef",
        "git_cwd": "/tmp/wt",
    }
    inv["builder_containment"] = {
        "schema": "argus.builder.containment.v1",
        "containment_requested": "auto",
        "containment_applied": "bwrap",
        "containment_fallback_used": False,
        "trust_degraded_unsandboxed": False,
        "trust_degraded_workspace_scope": False,
        "filesystem_scope_mode": "argus_root_worktree_scoped",
        "no_new_privs_requested": False,
        "no_new_privs_applied": False,
        "no_new_privs_reason": "non_linux_skip",
        "landlock_requested": True,
        "landlock_applied": False,
    }
    r = compute_builder_branch_review(
        invoke_data=inv,
        scope_check=_scope_clean(),
        execution_outcome=_eo("completed"),
        builder_diff_summary=_bds(),
    )
    assert r["review_status"] == REVIEW_REVIEW_REQUIRED
    assert any("landlock_requested_but_not_applied" in x for x in r["review_reasons"])


def test_merge_candidate_product_scoped_review_required_when_landlock_requested_not_applied() -> None:
    """Symmetric with worktree: strong FS modes deny merge_candidate if Landlock was requested but failed."""
    inv = _inv_execute_ok()
    inv["builder_containment"] = {
        **inv["builder_containment"],
        "filesystem_scope_mode": "product_scoped",
        "landlock_requested": True,
        "landlock_applied": False,
    }
    r = compute_builder_branch_review(
        invoke_data=inv,
        scope_check=_scope_clean(),
        execution_outcome=_eo("completed"),
        builder_diff_summary=_bds(),
    )
    assert r["review_status"] == REVIEW_REVIEW_REQUIRED
    assert any("landlock_requested_but_not_applied" in x for x in r["review_reasons"])


def test_merge_candidate_product_scoped_ok_when_landlock_not_requested() -> None:
    """Non-Linux (or Landlock disabled): landlock_requested false — no Landlock merge gate."""
    inv = _inv_execute_ok()
    inv["builder_containment"] = {
        **inv["builder_containment"],
        "filesystem_scope_mode": "product_scoped",
        "landlock_requested": False,
        "landlock_skipped_reason": "non_linux_skip",
        "landlock_applied": None,
    }
    r = compute_builder_branch_review(
        invoke_data=inv,
        scope_check=_scope_clean(),
        execution_outcome=_eo("completed"),
        builder_diff_summary=_bds(),
    )
    assert r["review_status"] == REVIEW_MERGE_CANDIDATE


def test_merge_candidate_argus_root_worktree_requires_worktree_scoped_fs() -> None:
    """Argus-root worktree merge_candidate uses tightened fs mode, not full-repo legacy mounts."""
    inv = _inv_execute_ok()
    inv["git_baseline"] = {
        "schema": "argus.builder.git_baseline.v1",
        "git_workspace_kind": "argus_root_worktree",
        "baseline_commit": "deadbeef",
        "git_cwd": "/tmp/wt",
    }
    inv["builder_containment"] = {
        "schema": "argus.builder.containment.v1",
        "containment_requested": "auto",
        "containment_applied": "bwrap",
        "containment_fallback_used": False,
        "trust_degraded_unsandboxed": False,
        "trust_degraded_workspace_scope": False,
        "filesystem_scope_mode": "argus_root_worktree_scoped",
        "no_new_privs_requested": False,
        "no_new_privs_applied": False,
        "no_new_privs_reason": "non_linux_skip",
        "landlock_requested": True,
        "landlock_applied": True,
    }
    r = compute_builder_branch_review(
        invoke_data=inv,
        scope_check=_scope_clean(),
        execution_outcome=_eo("completed"),
        builder_diff_summary=_bds(),
    )
    assert r["review_status"] == REVIEW_MERGE_CANDIDATE


def test_argus_root_worktree_legacy_repo_rw_is_review_required_honest_fallback() -> None:
    """If infer falls back to legacy_repo_rw on a worktree run, do not claim merge_candidate."""
    inv = _inv_execute_ok()
    inv["git_baseline"] = {
        "schema": "argus.builder.git_baseline.v1",
        "git_workspace_kind": "argus_root_worktree",
        "baseline_commit": "deadbeef",
        "git_cwd": "/tmp/wt",
    }
    inv["builder_containment"] = {
        "schema": "argus.builder.containment.v1",
        "containment_requested": "auto",
        "containment_applied": "bwrap",
        "containment_fallback_used": False,
        "trust_degraded_unsandboxed": False,
        "trust_degraded_workspace_scope": False,
        "filesystem_scope_mode": "legacy_repo_rw",
        "no_new_privs_requested": False,
        "no_new_privs_applied": False,
        "no_new_privs_reason": "non_linux_skip",
    }
    r = compute_builder_branch_review(
        invoke_data=inv,
        scope_check=_scope_clean(),
        execution_outcome=_eo("completed"),
        builder_diff_summary=_bds(),
    )
    assert r["review_status"] == REVIEW_REVIEW_REQUIRED
    assert any("legacy_repo_rw_fallback" in x for x in r["review_reasons"])


def test_merge_candidate_denied_when_git_baseline_argus_root_despite_isolation_ok() -> None:
    """Synthetic defense-in-depth: argus_root must never yield merge_candidate for agent runs."""
    inv = _inv_execute_ok()
    inv["git_baseline"] = {
        "schema": "argus.builder.git_baseline.v1",
        "git_workspace_kind": "argus_root",
        "baseline_commit": "deadbeef",
        "git_cwd": "/tmp/p",
    }
    inv["builder_containment"] = {
        "schema": "argus.builder.containment.v1",
        "containment_requested": "auto",
        "containment_applied": "bwrap",
        "containment_fallback_used": False,
        "trust_degraded_unsandboxed": False,
        "trust_degraded_workspace_scope": True,
        "filesystem_scope_mode": "legacy_repo_rw",
        "no_new_privs_requested": False,
        "no_new_privs_applied": False,
        "no_new_privs_reason": "non_linux_skip",
    }
    r = compute_builder_branch_review(
        invoke_data=inv,
        scope_check=_scope_clean(),
        execution_outcome=_eo("completed"),
        builder_diff_summary=_bds(),
    )
    assert r["review_status"] == REVIEW_REVIEW_REQUIRED
    assert any(x == "builder_workspace:argus_root_not_isolated" for x in r["review_reasons"])


def test_blocked_other_skipped_branch_isolation() -> None:
    inv = _inv_execute_ok()
    inv["git_branch_isolation"] = {
        "branch_isolation_status": "skipped_not_executing",
        "git_builder_branch": None,
        "trust_degraded_dirty_tree": False,
    }
    r = compute_builder_branch_review(
        invoke_data=inv,
        scope_check=_scope_clean(),
        execution_outcome=_eo("completed"),
        builder_diff_summary=_bds(),
    )
    assert r["review_status"] == REVIEW_BLOCKED


def test_cli_review_json(
    monkeypatch: pytest.MonkeyPatch, tmp_path, capsys: pytest.CaptureFixture[str]
) -> None:
    from argus.cli.builder_cmd import run_builder_subcommand

    pid = "rvcli"
    builder_status_fixtures._write_next_expansion(tmp_path, pid)
    builder_status_fixtures._write_prepared(tmp_path, pid)
    builder_status_fixtures._write_invoke(tmp_path, pid)
    builder_status_fixtures._write_reconcile(tmp_path, pid)
    monkeypatch.setattr("argus.cli.builder_cmd.repo_root", lambda: tmp_path)
    args = Namespace(
        builder_command="review",
        product_id=pid,
        json=True,
        products_dir=None,
    )
    assert run_builder_subcommand(args) == 0
    assert "blocked" in capsys.readouterr().out
