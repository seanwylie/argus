"""Unit tests for argus.products.git_lifecycle (local git init for product trees)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from argus.importer.sync import sync_tree
from argus.products.git_lifecycle import init_argus_product_git, write_non_git_import_marker


@pytest.mark.skipif(
    subprocess.run(["git", "--version"], capture_output=True).returncode != 0,
    reason="git not installed",
)
def test_init_argus_product_git_creates_initial_commit(tmp_path: Path) -> None:
    pr = tmp_path / "prod"
    pr.mkdir()
    (pr / "a.txt").write_text("x", encoding="utf-8")
    out = init_argus_product_git(pr)
    assert out.get("git_executable_found") is True
    assert out.get("git_init_ok") is True
    assert out.get("initial_commit_ok") is True
    r = subprocess.run(
        ["git", "-C", str(pr), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert len((r.stdout or "").strip()) >= 7


@pytest.mark.skipif(
    subprocess.run(["git", "--version"], capture_output=True).returncode != 0,
    reason="git not installed",
)
def test_init_argus_product_git_noop_when_git_exists(tmp_path: Path) -> None:
    pr = tmp_path / "prod2"
    pr.mkdir()
    subprocess.run(["git", "-C", str(pr), "init", "-b", "main"], check=True, capture_output=True)
    (pr / "f").write_text("1", encoding="utf-8")
    out = init_argus_product_git(pr)
    assert out.get("note") == "existing_git_repo"


@pytest.mark.skipif(
    subprocess.run(["git", "--version"], capture_output=True).returncode != 0,
    reason="git not installed",
)
def test_sync_tree_preserves_dot_git_when_not_excluded(tmp_path: Path) -> None:
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    src.mkdir()
    _ = _git_init_with_file(src)
    excludes = [".venv/"]  # no .git/
    sync_tree(src, dst, excludes=excludes, delete=True)
    assert (dst / ".git").is_dir()
    assert (dst / "README.md").is_file()


def _git_init_with_file(repo: Path) -> None:
    subprocess.run(["git", "-C", str(repo), "init", "-b", "main"], check=True, capture_output=True)
    (repo / "README.md").write_text("h\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "README.md"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.email", "t@t"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.name", "t"],
        check=True,
        capture_output=True,
    )
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "init"], check=True, capture_output=True)


def test_write_non_git_import_marker(tmp_path: Path) -> None:
    pr = tmp_path / "p"
    pr.mkdir()
    write_non_git_import_marker(pr, reason="test reason")
    md = pr / "notes" / "ARGUS_IMPORT_NON_GIT.md"
    assert md.is_file()
    assert "test reason" in md.read_text(encoding="utf-8")
