"""Runner quality debug report — aggregates, streaks, artifact write."""

from __future__ import annotations

import json
from pathlib import Path

from argus.portfolio.autonomous_runner import PORTFOLIO_AUTONOMOUS_RUNNER_SCHEMA
from argus.portfolio.runner_quality_report import (
    RUNNER_QUALITY_REPORT_SCHEMA,
    build_runner_quality_report_payload,
    render_runner_quality_report_markdown,
    run_runner_quality_report,
    runner_quality_report_dir,
    write_runner_quality_report_artifacts,
)


def _session(
    *,
    sid: str,
    finished: str,
    stop_reason: str,
    codes: list[str],
    cycles_run: int = 1,
    per_cycle_outcomes: list | None = None,
    lifecycle: dict | None = None,
) -> dict:
    lsi = lifecycle or {
        "primary_signal": "neutral",
        "inputs_snapshot": {"products_under_retirement_pressure": ["p1", "p1"]},
    }
    return {
        "schema": PORTFOLIO_AUTONOMOUS_RUNNER_SCHEMA,
        "session_id": sid,
        "finished_at_utc": finished,
        "cycles_run": cycles_run,
        "stop_reason": stop_reason,
        "stop_reason_codes": codes,
        "per_cycle_outcomes": per_cycle_outcomes or [],
        "lifecycle_session_influence": lsi,
        "promotion_execution": {"steps": []},
    }


def test_sparse_history_empty(tmp_path: Path) -> None:
    pl = build_runner_quality_report_payload(tmp_path, limit_history=10)
    assert pl["schema"] == RUNNER_QUALITY_REPORT_SCHEMA
    assert pl["summary"]["sessions_in_window"] == 0
    assert "No stamped autonomous session" in pl["notes"][0]


def test_stop_reason_aggregation_and_streak(tmp_path: Path) -> None:
    d = tmp_path / "runs" / "portfolio" / "autonomous_runner"
    d.mkdir(parents=True)
    for sid, fin, sr in [
        ("20260101T000001Z", "2026-01-01T00:00:01Z", "quiescence_recommendation"),
        ("20260101T000002Z", "2026-01-01T00:00:02Z", "quiescence_recommendation"),
        ("20260101T000003Z", "2026-01-01T00:00:03Z", "max_cycles_reached"),
    ]:
        (d / f"{sid}.json").write_text(
            json.dumps(
                _session(
                    sid=sid,
                    finished=fin,
                    stop_reason=sr,
                    codes=[f"autonomous_runner.stop.{sr}.x"],
                )
            ),
            encoding="utf-8",
        )
    pl = build_runner_quality_report_payload(tmp_path, limit_history=10)
    assert pl["summary"]["sessions_in_window"] == 3
    assert pl["summary"]["stop_reason_counts"]["quiescence_recommendation"] == 2
    assert pl["summary"]["max_consecutive_same_stop_reason"]["max_consecutive_same_stop_reason"] == 2
    assert pl["summary"]["consecutive_same_condition_pairs"] >= 1


def test_repeated_same_condition_pairs(tmp_path: Path) -> None:
    d = tmp_path / "runs" / "portfolio" / "autonomous_runner"
    d.mkdir(parents=True)
    code = "autonomous_runner.stop.cycle_overall.inspect_specific_products"
    for i in range(4):
        sid = f"2026010{i}T000000Z"
        fin = f"2026-01-0{i+1}T00:00:00Z"
        (d / f"{sid}.json").write_text(
            json.dumps(
                _session(
                    sid=sid,
                    finished=fin,
                    stop_reason="cycle_overall_recommendation",
                    codes=[code],
                )
            ),
            encoding="utf-8",
        )
    pl = build_runner_quality_report_payload(tmp_path, limit_history=10)
    assert pl["summary"]["consecutive_same_condition_pairs"] == 3


def test_productive_hint_multi_iteration(tmp_path: Path) -> None:
    d = tmp_path / "runs" / "portfolio" / "autonomous_runner"
    d.mkdir(parents=True)
    (d / "20260101T000000Z.json").write_text(
        json.dumps(
            _session(
                sid="20260101T000000Z",
                finished="2026-01-01T00:00:00Z",
                stop_reason="max_cycles_reached",
                codes=["autonomous_runner.stop.max_cycles=3"],
                cycles_run=2,
            )
        ),
        encoding="utf-8",
    )
    pl = build_runner_quality_report_payload(tmp_path, limit_history=5)
    assert pl["non_productive_estimate"]["sessions_with_productive_hint"] == 1


def test_write_artifacts(tmp_path: Path) -> None:
    d = tmp_path / "runs" / "portfolio" / "autonomous_runner"
    d.mkdir(parents=True)
    (d / "20260101T000000Z.json").write_text(
        json.dumps(
            _session(
                sid="20260101T000000Z",
                finished="2026-01-01T00:00:00Z",
                stop_reason="max_cycles_reached",
                codes=["x"],
            )
        ),
        encoding="utf-8",
    )
    pl = run_runner_quality_report(tmp_path, write_artifacts=True, limit_history=5)
    out = runner_quality_report_dir(tmp_path)
    assert (out / "latest.json").is_file()
    assert (out / "latest.md").is_file()
    raw = json.loads((out / "latest.json").read_text(encoding="utf-8"))
    assert raw["schema"] == RUNNER_QUALITY_REPORT_SCHEMA
    md = (out / "latest.md").read_text(encoding="utf-8")
    assert "Runner quality" in md
    assert str(pl["summary"]["sessions_in_window"]) in md


def test_render_markdown_empty(tmp_path: Path) -> None:
    pl = build_runner_quality_report_payload(tmp_path, limit_history=3)
    md = render_runner_quality_report_markdown(pl)
    assert "Sessions in window: **0**" in md


def test_write_runner_quality_without_run(tmp_path: Path) -> None:
    pl = build_runner_quality_report_payload(tmp_path, limit_history=3)
    write_runner_quality_report_artifacts(tmp_path, pl)
    assert (runner_quality_report_dir(tmp_path) / "latest.json").is_file()
