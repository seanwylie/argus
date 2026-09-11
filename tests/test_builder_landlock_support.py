"""Landlock write-allowlist helpers (no in-process apply_landlock — subprocess-only)."""

from __future__ import annotations

import json
from pathlib import Path

from argus.builder.landlock_support import (
    derive_landlock_write_paths,
    merge_landlock_status_into_meta,
    should_attempt_landlock_for_fs_mode,
)


def test_should_attempt_skips_legacy_repo_rw() -> None:
    assert should_attempt_landlock_for_fs_mode("legacy_repo_rw") is False
    assert should_attempt_landlock_for_fs_mode("product_scoped") is True
    assert should_attempt_landlock_for_fs_mode("argus_root_worktree_scoped") is True


def test_derive_write_paths_product_scoped(tmp_path) -> None:
    (tmp_path / "products" / "p").mkdir(parents=True)
    pd = tmp_path / "products" / "p"
    extra = tmp_path / "runs" / "builder" / "prepare" / "p"
    extra.mkdir(parents=True)
    wl = derive_landlock_write_paths(
        repo_root=tmp_path,
        product_id="p",
        scratch_root=tmp_path / "runs" / "builder" / ".sandbox",
        filesystem_scope_mode="product_scoped",
        products_dir=None,
        extra_rw_paths=[extra],
        worktree_host_path=None,
    )
    paths = {str(p) for p in wl}
    assert str((tmp_path / "runs" / "builder" / ".sandbox" / "home" / "p").resolve()) in paths
    assert str(Path("/tmp").resolve()) in paths
    assert str(pd.resolve()) in paths
    assert str(extra.resolve()) in paths


def test_derive_write_paths_argus_root_worktree(tmp_path) -> None:
    (tmp_path / ".git").mkdir()
    wt = tmp_path / "runs" / "builder" / "worktrees" / "p" / "wt"
    wt.mkdir(parents=True)
    wl = derive_landlock_write_paths(
        repo_root=tmp_path,
        product_id="p",
        scratch_root=tmp_path / "runs" / "builder" / ".sandbox",
        filesystem_scope_mode="argus_root_worktree_scoped",
        products_dir=None,
        extra_rw_paths=None,
        worktree_host_path=wt,
    )
    paths = {str(p) for p in wl}
    assert str(wt.resolve()) in paths
    assert str((tmp_path / ".git").resolve()) in paths


def test_merge_status_honest_on_success(tmp_path) -> None:
    sp = tmp_path / "st.json"
    sp.write_text(
        json.dumps({"landlock_applied": True, "reason": None, "abi_version": 1}),
        encoding="utf-8",
    )
    meta: dict = {"landlock_requested": True, "landlock_applied": None}
    merge_landlock_status_into_meta(meta, sp)
    assert meta["landlock_applied"] is True
    assert meta.get("trust_degraded_missing_landlock") is not True
    assert not sp.exists()


def test_merge_status_degrades_when_apply_failed(tmp_path) -> None:
    sp = tmp_path / "st.json"
    sp.write_text(
        json.dumps({"landlock_applied": False, "reason": "landlock_abi_too_old:0"}),
        encoding="utf-8",
    )
    meta: dict = {"landlock_requested": True}
    merge_landlock_status_into_meta(meta, sp)
    assert meta["landlock_applied"] is False
    assert meta["trust_degraded_missing_landlock"] is True


def test_merge_status_missing_file_degrades(tmp_path) -> None:
    meta: dict = {"landlock_requested": True}
    merge_landlock_status_into_meta(meta, tmp_path / "nope.json")
    assert meta["landlock_applied"] is False
    assert meta.get("landlock_reason") == "status_file_missing"
    assert meta["trust_degraded_missing_landlock"] is True
