"""Tests for import replay argv reconstruction, validation, and drift (local git fixtures)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from argus.importer.import_state import IMPORT_STATE_SCHEMA, build_import_state
from argus.importer.replay import (
    exclude_list_from_import_state,
    inspect_import_drift,
    reconstruct_import_argv,
    run_import_replay,
    validate_import_state_for_replay,
)


def _git_init_with_commit(repo: Path, message: str = "init") -> str:
    subprocess.run(["git", "-C", str(repo), "init", "-b", "main"], check=True, capture_output=True)
    (repo / "README.md").write_text("x\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "README.md"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", message], check=True, capture_output=True)
    p = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    )
    return (p.stdout or "").strip()


def _minimal_product_yaml(product_id: str, import_state: dict) -> str:
    """Minimal valid-ish product.yaml body for drift tests."""
    import yaml

    doc = {
        "id": product_id,
        "name": product_id.title(),
        "lifecycle": {"stage": "validate", "next_gate": "g"},
        "raw_extensions": {"import_state": import_state},
    }
    return yaml.safe_dump(doc, sort_keys=False, allow_unicode=True, default_flow_style=False)


def test_reconstruct_import_argv() -> None:
    ist = build_import_state(
        source_repo_url="https://github.com/o/r",
        cache_slug="o_r",
        sync_excludes=[".git/"],
        include_cursor=True,
        include_local_db_artifacts=False,
        exclude_node_artifacts=True,
        extra_excludes=["custom/"],
        imported_from_branch="main",
        imported_from_commit="abc",
        imported_at_utc="2026-01-01T00:00:00Z",
        first_pass_ran=True,
        first_pass_status="success",
        first_pass_summary_path="products/x/first_pass_argus_summary.md",
    )
    argv = reconstruct_import_argv(
        "myproduct",
        ist,
        cache_dir="/tmp/cache",
        no_delete=True,
        skip_first_pass=True,
        no_uv=True,
    )
    assert argv[:4] == ["--repo-url", "https://github.com/o/r", "--product-id", "myproduct"]
    assert "--cache-dir" in argv and "/tmp/cache" in argv
    assert "--no-delete" in argv
    assert "--include-cursor" in argv
    assert "--skip-first-pass" in argv
    assert "--no-uv" in argv
    assert "--extra-exclude" in argv and "custom/" in argv


def test_exclude_list_from_import_state_prefers_sync_excludes() -> None:
    ist = {
        "sync_excludes": [".git/", "foo/"],
        "include_cursor": False,
        "extra_excludes": ["ignored_if_sync"],
    }
    ex = exclude_list_from_import_state(ist)
    assert ex == [".git/", "foo/"]


def test_exclude_list_from_import_state_derived_when_sync_empty() -> None:
    ist = {
        "sync_excludes": [],
        "include_cursor": False,
        "include_local_db_artifacts": False,
        "exclude_node_artifacts": True,
        "extra_excludes": ["zz/"],
    }
    ex = exclude_list_from_import_state(ist)
    assert ".git/" not in ex
    assert "zz/" in ex
    assert "node_modules/" in ex


def test_exclude_list_from_import_state_respects_preserve_product_git_false() -> None:
    ist = {
        "sync_excludes": [],
        "include_cursor": False,
        "include_local_db_artifacts": False,
        "exclude_node_artifacts": False,
        "extra_excludes": [],
        "preserve_product_git": False,
    }
    ex = exclude_list_from_import_state(ist)
    assert ".git/" in ex


def test_validate_missing_import_state() -> None:
    assert validate_import_state_for_replay(None) == ["import_state missing"]


def test_validate_malformed_import_state() -> None:
    errs = validate_import_state_for_replay(
        {
            "schema": "wrong",
            "source_repo_url": "",
            "cache_slug": "",
        }
    )
    assert any("schema" in e for e in errs)
    assert any("source_repo_url" in e for e in errs)
    assert any("cache_slug" in e for e in errs)


def test_validate_extra_excludes_type() -> None:
    errs = validate_import_state_for_replay(
        {
            "schema": IMPORT_STATE_SCHEMA,
            "source_repo_url": "https://github.com/a/b",
            "cache_slug": "a_b",
            "sync_excludes": [".git/"],
            "extra_excludes": [1, "ok"],
        }
    )
    assert any("extra_excludes[0]" in e for e in errs)


@pytest.mark.skipif(
    subprocess.run(["git", "--version"], capture_output=True).returncode != 0,
    reason="git not installed",
)
def test_inspect_drift_cache_matches_stored_commit(tmp_path: Path) -> None:
    pid = "driftprod"
    slug = "o_r"
    cache = tmp_path / ".import_cache" / slug
    cache.mkdir(parents=True)
    sha = _git_init_with_commit(cache)

    prod = tmp_path / "products" / pid
    prod.mkdir(parents=True)
    ist = {
        "schema": IMPORT_STATE_SCHEMA,
        "import_mode": "cache_sync",
        "source_repo_url": "https://github.com/o/r",
        "cache_slug": slug,
        "sync_excludes": [".git/"],
        "include_cursor": False,
        "include_local_db_artifacts": False,
        "exclude_node_artifacts": True,
        "extra_excludes": [],
        "imported_from_branch": "main",
        "imported_from_commit": sha,
        "imported_at_utc": "2026-01-01T00:00:00Z",
        "first_pass_ran": False,
        "first_pass_status": "skipped",
        "first_pass_summary_path": None,
    }
    (prod / "product.yaml").write_text(_minimal_product_yaml(pid, ist), encoding="utf-8")

    out = inspect_import_drift(tmp_path, pid)
    assert out["import_state_complete"] is True
    assert out["cache_head_commit"] == sha
    assert out["cache_head_matches_stored_commit"] is True
    assert out["cache_has_stored_commit"] is True


@pytest.mark.skipif(
    subprocess.run(["git", "--version"], capture_output=True).returncode != 0,
    reason="git not installed",
)
def test_inspect_drift_head_differs_from_stored(tmp_path: Path) -> None:
    pid = "driftprod2"
    slug = "o_r2"
    cache = tmp_path / ".import_cache" / slug
    cache.mkdir(parents=True)
    sha1 = _git_init_with_commit(cache, "first")
    (cache / "README.md").write_text("y\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(cache), "add", "README.md"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(cache), "commit", "-m", "second"], check=True, capture_output=True)

    prod = tmp_path / "products" / pid
    prod.mkdir(parents=True)
    ist = {
        "schema": IMPORT_STATE_SCHEMA,
        "import_mode": "cache_sync",
        "source_repo_url": "https://github.com/o/r",
        "cache_slug": slug,
        "sync_excludes": [".git/"],
        "include_cursor": False,
        "include_local_db_artifacts": False,
        "exclude_node_artifacts": True,
        "extra_excludes": [],
        "imported_from_branch": "main",
        "imported_from_commit": sha1,
        "imported_at_utc": "2026-01-01T00:00:00Z",
        "first_pass_ran": False,
        "first_pass_status": "skipped",
        "first_pass_summary_path": None,
    }
    (prod / "product.yaml").write_text(_minimal_product_yaml(pid, ist), encoding="utf-8")

    out = inspect_import_drift(tmp_path, pid)
    assert out["cache_head_matches_stored_commit"] is False
    assert "differs" in (out.get("drift_summary") or "").lower()


def test_run_import_replay_dry_run_missing_file(tmp_path: Path) -> None:
    code, report = run_import_replay(tmp_path, "nope", dry_run=True)
    assert code == 2
    assert "error" in report


def test_run_import_replay_dry_run_invalid_state(tmp_path: Path) -> None:
    pid = "bad"
    prod = tmp_path / "products" / pid
    prod.mkdir(parents=True)
    (prod / "product.yaml").write_text(
        _minimal_product_yaml(
            pid,
            {"schema": IMPORT_STATE_SCHEMA, "source_repo_url": "https://github.com/a/b"},
        ),
        encoding="utf-8",
    )
    code, report = run_import_replay(tmp_path, pid, dry_run=True)
    assert code == 3
    assert report.get("import_state_errors")
