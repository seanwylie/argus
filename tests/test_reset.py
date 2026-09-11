"""Tests for ``argus reset`` (plan + executor + CLI guardrails)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from argus.cli.reset_cmd import CONFIRM_ALL, CONFIRM_PORTFOLIO, run_reset_command
from argus.reset.execute import execute_reset
from argus.reset.plan import build_reset_plan, format_plan_report, plan_summary_counts


def _repo(tmp: Path) -> Path:
    r = tmp / "repo"
    r.mkdir()
    return r


def test_build_soft_targets_runs_children_not_readme(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    runs = repo / "runs"
    runs.mkdir()
    (runs / "README.md").write_text("keep\n", encoding="utf-8")
    (runs / "noise.txt").write_text("x", encoding="utf-8")
    d = runs / "sub"
    d.mkdir()
    (d / "a.json").write_text("{}", encoding="utf-8")

    plan = build_reset_plan(repo, "soft")
    assert "runs/noise.txt" in plan.paths_targets
    assert "runs/sub" in plan.paths_targets
    assert "runs/README.md" not in plan.paths_targets
    assert "products" not in "".join(plan.paths_targets)


def test_build_portfolio_includes_products_children(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    (repo / "runs").mkdir()
    (repo / "runs" / "README.md").write_text("k\n", encoding="utf-8")
    p = repo / "products" / "demo"
    p.mkdir(parents=True)
    (p / "product.yaml").write_text("id: demo\n", encoding="utf-8")

    plan = build_reset_plan(repo, "portfolio")
    assert any(x.startswith("products/") for x in plan.paths_targets)
    assert "Portfolio" in "".join(plan.notes) or "portfolio" in "".join(plan.notes).lower()


def test_build_all_adds_import_cache_when_present(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    (repo / "runs").mkdir()
    (repo / "runs" / "README.md").write_text("k\n", encoding="utf-8")
    ic = repo / ".import_cache"
    ic.mkdir()
    (ic / "x").write_text("c", encoding="utf-8")

    plan = build_reset_plan(repo, "all")
    assert ".import_cache" in plan.paths_targets


def test_execute_dry_run_does_not_delete(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    runs = repo / "runs"
    runs.mkdir()
    (runs / "README.md").write_text("k\n", encoding="utf-8")
    (runs / "z.txt").write_text("z", encoding="utf-8")

    plan = build_reset_plan(repo, "soft")
    execute_reset(plan, dry_run=True)
    assert (runs / "z.txt").is_file()
    assert (runs / "README.md").is_file()


def test_execute_soft_removes_noise_keeps_readme_and_writes_log(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    runs = repo / "runs"
    runs.mkdir()
    (runs / "README.md").write_text("k\n", encoding="utf-8")
    (runs / "z.txt").write_text("z", encoding="utf-8")

    plan = build_reset_plan(repo, "soft")
    result = execute_reset(plan, dry_run=False)
    assert (runs / "README.md").is_file()
    assert not (runs / "z.txt").exists()
    assert result.log_written is not None
    assert result.log_written.name == "latest.json"
    assert "paths_removed" in result.log_written.read_text(encoding="utf-8")


def test_plan_summary_counts(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    runs = repo / "runs"
    runs.mkdir()
    (runs / "a").write_text("1", encoding="utf-8")
    plan = build_reset_plan(repo, "soft")
    c = plan_summary_counts(plan)
    assert c["files"] >= 1


def test_format_plan_report_nonempty(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    (repo / "runs").mkdir()
    (repo / "runs" / "README.md").write_text("k\n", encoding="utf-8")
    text = format_plan_report(build_reset_plan(repo, "soft"))
    assert "Reset mode: soft" in text
    assert "Preserved" in text


@pytest.mark.parametrize(
    ("portfolio", "all_", "confirm", "dry_run", "expect"),
    [
        (True, False, None, False, 2),
        (False, True, None, False, 2),
        (True, False, CONFIRM_PORTFOLIO, False, 0),
        (False, True, CONFIRM_ALL, False, 0),
        (True, False, None, True, 0),
    ],
)
def test_cli_confirm_guards(
    tmp_path: Path,
    portfolio: bool,
    all_: bool,
    confirm: str | None,
    dry_run: bool,
    expect: int,
) -> None:
    repo = _repo(tmp_path)
    (repo / "runs").mkdir()
    (repo / "runs" / "README.md").write_text("k\n", encoding="utf-8")
    if portfolio or all_:
        prod = repo / "products" / "p1"
        prod.mkdir(parents=True)
        (prod / "product.yaml").write_text("id: p1\n", encoding="utf-8")
    if all_:
        (repo / ".import_cache").mkdir()
        (repo / ".import_cache" / "c").write_text("x", encoding="utf-8")

    args = SimpleNamespace(
        soft=False,
        portfolio=portfolio,
        all=all_,
        dry_run=dry_run,
        confirm=confirm,
    )
    code = run_reset_command(args, repo)
    assert code == expect


def test_cli_soft_no_confirm_ok(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    (repo / "runs").mkdir()
    (repo / "runs" / "README.md").write_text("k\n", encoding="utf-8")
    (repo / "runs" / "x.txt").write_text("x", encoding="utf-8")
    args = SimpleNamespace(soft=True, portfolio=False, all=False, dry_run=False, confirm=None)
    assert run_reset_command(args, repo) == 0
    assert not (repo / "runs" / "x.txt").exists()
