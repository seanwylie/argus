"""Zero-state / empty portfolio behavior for portfolio refresh and autonomous runner."""

from __future__ import annotations

from pathlib import Path

from argus.dashboard.console_data import autonomous_session_primary_status
from argus.dashboard.operator_summary import evaluate_operator_summary
from argus.portfolio.autonomous_runner import run_portfolio_autonomous_session
from argus.portfolio.refresh import run_portfolio_refresh


def _empty_repo(tmp: Path) -> Path:
    r = tmp / "repo"
    (r / "products").mkdir(parents=True)
    return r


def test_portfolio_refresh_empty_inventory_is_success(tmp_path: Path) -> None:
    repo = _empty_repo(tmp_path)
    code, payload = run_portfolio_refresh(repo)
    assert code == 0
    assert payload.get("ok") is True
    assert payload.get("portfolio_state") == "empty_portfolio"
    assert payload.get("zero_state") is True
    assert "operator_guidance" in payload
    rf = repo / "runs" / "portfolio" / "latest" / "refresh.json"
    assert rf.is_file()
    assert "empty_portfolio" in rf.read_text(encoding="utf-8")


def test_operator_summary_flags_zero_state(tmp_path: Path) -> None:
    repo = _empty_repo(tmp_path)
    pl = evaluate_operator_summary(repo)
    assert pl.get("zero_state") is True
    assert pl.get("portfolio_state") == "empty_portfolio"
    assert pl.get("headline_status") == "portfolio_empty"
    assert pl.get("confidence_level") == "low"
    assert "products" in (pl.get("recommended_next_step") or "").lower()


def test_autonomous_session_primary_status_empty_portfolio() -> None:
    assert autonomous_session_primary_status("empty_portfolio") == "stopped_empty_portfolio"


def test_autonomous_runner_stops_with_empty_portfolio_reason(tmp_path: Path) -> None:
    repo = _empty_repo(tmp_path)
    out = run_portfolio_autonomous_session(
        repo,
        max_cycles=1,
        write_stage_artifacts=False,
        write_session_artifacts=False,
    )
    assert out.get("stop_reason") == "empty_portfolio"
    assert "empty_portfolio" in " ".join(out.get("stop_reason_codes") or [])

