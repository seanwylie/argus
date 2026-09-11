"""Unit tests for importer regression runner (no live GitHub imports)."""

from __future__ import annotations

from pathlib import Path

from argus.importer import regression_runner as rr


def test_merge_entry_and_argv() -> None:
    defaults = {"operator_team": "argus", "product_type": "application", "exclude_node_artifacts": True}
    repo = {
        "id": "x",
        "github_url": "https://github.com/o/r",
        "product_id": "myprod",
        "flags": {"product_type": "library"},
    }
    m = rr.merge_entry(defaults, repo)
    assert m["product_type"] == "library"
    assert m["github_url"] == "https://github.com/o/r"
    argv = rr.build_argv(m)
    assert "--repo-url" in argv
    assert "https://github.com/o/r" in argv
    assert "--product-id" in argv
    assert "myprod" in argv


def test_classify() -> None:
    assert (
        rr.classify(
            import_exit=0,
            import_state={"a": 1},
            first_pass_status="success",
            expect_ok=True,
            summary_len=500,
        )
        == "GOOD"
    )
    assert (
        rr.classify(
            import_exit=1,
            import_state={},
            first_pass_status="success",
            expect_ok=True,
            summary_len=500,
        )
        == "BROKEN"
    )
    assert (
        rr.classify(
            import_exit=0,
            import_state=None,
            first_pass_status="success",
            expect_ok=True,
            summary_len=500,
        )
        == "BROKEN"
    )


def test_load_config(tmp_path: Path) -> None:
    p = tmp_path / "cfg.yaml"
    p.write_text(
        "version: 1\ndefaults: {}\nrepos: []\n",
        encoding="utf-8",
    )
    c = rr.load_regression_config(p)
    assert c["repos"] == []


def test_key_path_inventory(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("x", encoding="utf-8")
    s = rr._key_path_inventory(tmp_path)
    assert "README.md:yes" in s
    assert "pyproject.toml:no" in s
