"""Tests for lifecycle-aware session influence (scheduler / autonomous)."""

from __future__ import annotations

from argus.portfolio.lifecycle import LIFECYCLE_STATUSES, PORTFOLIO_LIFECYCLE_SCHEMA
from argus.portfolio.lifecycle_session_influence import (
    build_lifecycle_session_influence,
    classify_lifecycle_pressure,
)


def _zero_counts() -> dict[str, int]:
    return {k: 0 for k in LIFECYCLE_STATUSES}


def _payload(
    *,
    counts: dict[str, int],
    entering: list[str] | None = None,
    exiting: list[str] | None = None,
    rrepair: list[str] | None = None,
    rret: list[str] | None = None,
    posture: str | None = None,
) -> dict:
    return {
        "schema": PORTFOLIO_LIFECYCLE_SCHEMA,
        "run_id": "t",
        "evaluated_at_utc": "t",
        "lifecycle_counts": counts,
        "products_entering": entering or [],
        "products_exiting": exiting or [],
        "products_under_repair_pressure": rrepair or [],
        "products_under_retirement_pressure": rret or [],
        "portfolio_strategy_posture": posture,
        "portfolio_lifecycle_summary": {"narrative": "x"},
    }


def test_classify_repair_heavy() -> None:
    c = _zero_counts()
    c["repairing"] = 2
    c["active"] = 5
    assert classify_lifecycle_pressure(_payload(counts=c)) == "repair_heavy"


def test_classify_retirement_heavy() -> None:
    c = _zero_counts()
    c["active"] = 6
    c["retiring"] = 0
    c["harvesting"] = 0
    pl = _payload(
        counts=c,
        rret=["a", "b"],
    )
    assert classify_lifecycle_pressure(pl) == "retirement_heavy"


def test_classify_create_heavy() -> None:
    c = _zero_counts()
    c["incubating"] = 2
    assert classify_lifecycle_pressure(_payload(counts=c)) == "create_heavy"


def test_classify_create_with_posture() -> None:
    c = _zero_counts()
    c["active"] = 4
    c["incubating"] = 1
    pl = _payload(counts=c, posture="create", entering=["p1"])
    assert classify_lifecycle_pressure(pl) == "create_heavy"


def test_classify_mixed_sparse_small_portfolio() -> None:
    c = _zero_counts()
    c["active"] = 2
    assert classify_lifecycle_pressure(_payload(counts=c)) == "mixed_sparse"


def test_classify_neutral() -> None:
    c = _zero_counts()
    c["active"] = 8
    c["incubating"] = 1
    assert classify_lifecycle_pressure(_payload(counts=c)) == "neutral"


def test_build_includes_session_notes_and_hints() -> None:
    c = _zero_counts()
    c["repairing"] = 2
    c["active"] = 4
    inf = build_lifecycle_session_influence(_payload(counts=c))
    assert inf["primary_signal"] == "repair_heavy"
    assert any("repair" in n.lower() for n in inf["session_notes"])
    assert inf["stop_continue_context"]["bias"] == "repair_first"
    assert inf["bounded"] is True


def test_build_create_heavy_notes() -> None:
    c = _zero_counts()
    c["incubating"] = 2
    inf = build_lifecycle_session_influence(_payload(counts=c, posture="create"))
    assert inf["primary_signal"] == "create_heavy"
    assert any("creation" in n.lower() for n in inf["session_notes"])


def test_strategy_alignment_expand_vs_repair() -> None:
    c = _zero_counts()
    c["repairing"] = 2
    c["active"] = 5
    inf = build_lifecycle_session_influence(_payload(counts=c, posture="expand"))
    notes = inf["strategy_alignment"].get("alignment_notes") or []
    assert any("expand" in n.lower() and "repair" in n.lower() for n in notes)


def test_empty_payload_neutral() -> None:
    inf = build_lifecycle_session_influence(None)
    assert inf["primary_signal"] == "neutral"
