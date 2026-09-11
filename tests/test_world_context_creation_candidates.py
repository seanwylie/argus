"""Advisory creation candidates derived from interpretation (deterministic)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from argus.world_context.creation_candidates import (
    build_creation_candidates_payload,
    summarize_candidates_for_operator,
)
from argus.world_context.interpretation import build_interpretation_payload
from argus.world_context.persist import WORLD_CONTEXT_CREATION_CANDIDATES_SCHEMA
from argus.world_context.service import write_creation_candidates_from_world_payload


def _fixture_wc_interp() -> tuple[dict, dict]:
    wc = {
        "schema": "argus.world_context.v1",
        "generated_at_utc": "t",
        "advisory_only": True,
        "disclaimer": "d",
        "summary": {"signal_count": 2},
        "signals": [
            {
                "source": "google_analytics",
                "signal_type": "traffic",
                "entity": "a",
                "value": 10,
                "unit": "users_last_28d",
                "observed_at_utc": "t",
                "freshness_status": "fresh",
                "confidence": "high",
                "provenance": "p",
            },
            {
                "source": "google_analytics",
                "signal_type": "traffic",
                "entity": "b",
                "value": 100,
                "unit": "users_last_28d",
                "observed_at_utc": "t",
                "freshness_status": "fresh",
                "confidence": "high",
                "provenance": "p",
            },
        ],
    }
    interp = build_interpretation_payload(wc)
    assert interp is not None
    return wc, interp


def test_build_creation_candidates_payload_empty_when_no_rules() -> None:
    wc, interp = _fixture_wc_interp()
    out = build_creation_candidates_payload(wc, interp)
    assert out is None


def test_build_and_summarize_from_combined_fixture() -> None:
    fix = Path(__file__).resolve().parents[1] / "tmp" / "combined_world_context_signals.json"
    if not fix.is_file():
        pytest.skip("tmp/combined_world_context_signals.json not present")
    signals = json.loads(fix.read_text(encoding="utf-8"))
    from argus.world_context.service import build_world_context_payload

    wc, errors = build_world_context_payload(signals)
    assert not errors
    interp = build_interpretation_payload(wc)
    assert interp is not None
    out = build_creation_candidates_payload(wc, interp)
    assert out is not None
    assert out["schema"] == WORLD_CONTEXT_CREATION_CANDIDATES_SCHEMA
    assert len(out["candidates"]) >= 1
    assert str(out.get("primary_candidate_id") or "").strip()
    assert str(out.get("situation_summary") or "").strip()
    assert summarize_candidates_for_operator(out)


def test_write_creation_candidates_artifact(tmp_path: Path) -> None:
    fix = Path(__file__).resolve().parents[1] / "tmp" / "combined_world_context_signals.json"
    if not fix.is_file():
        pytest.skip("tmp/combined_world_context_signals.json not present")
    signals = json.loads(fix.read_text(encoding="utf-8"))
    from argus.world_context.service import build_world_context_payload

    wc, errors = build_world_context_payload(signals)
    assert not errors
    interp = build_interpretation_payload(wc)
    p = write_creation_candidates_from_world_payload(tmp_path, wc, interp)
    assert p and p.is_file()
    raw = json.loads(p.read_text(encoding="utf-8"))
    assert raw.get("schema") == WORLD_CONTEXT_CREATION_CANDIDATES_SCHEMA
    assert raw.get("candidates")
