"""Deterministic world-context interpretation (advisory, no LLM)."""

from __future__ import annotations

import json
from pathlib import Path

from argus.world_context.interpretation import (
    build_interpretation_payload,
    operator_advisory_from_world_context,
)
from argus.world_context.persist import WORLD_CONTEXT_INTERPRETATION_SCHEMA
from argus.world_context.service import write_interpretation_from_world_payload


def _sample_world_context() -> dict:
    return {
        "schema": "argus.world_context.v1",
        "generated_at_utc": "2026-04-16T00:00:00Z",
        "advisory_only": True,
        "disclaimer": "x",
        "summary": {"signal_count": 2, "sources": ["google_analytics"], "fresh_count": 2, "stale_count": 0},
        "signals": [
            {
                "source": "google_analytics",
                "signal_type": "traffic",
                "entity": "a",
                "value": 10,
                "unit": "users_last_28d",
                "observed_at_utc": "2026-04-16T00:00:00Z",
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
                "observed_at_utc": "2026-04-16T00:00:00Z",
                "freshness_status": "fresh",
                "confidence": "high",
                "provenance": "p",
            },
        ],
    }


def test_build_interpretation_payload_schema_and_comparison() -> None:
    wc = _sample_world_context()
    interp = build_interpretation_payload(wc)
    assert interp is not None
    assert interp["schema"] == WORLD_CONTEXT_INTERPRETATION_SCHEMA
    assert interp["advisory_only"] is True
    assert "a" in interp["per_entity"]
    assert "b" in interp["per_entity"]
    comp = interp.get("comparison")
    assert isinstance(comp, dict)
    assert comp.get("summary_lines")
    assert "traffic_contrast" in (comp.get("patterns") or [])
    assert "Advisory only" in str(interp.get("operator_narrative") or "")


def test_operator_advisory_prefers_interpretation_narrative() -> None:
    wc = _sample_world_context()
    adv = operator_advisory_from_world_context(wc)
    assert adv
    assert "Traffic contrast" in adv or "materially higher" in adv
    assert "14 advisory signal" not in adv  # not the legacy aggregate-only line


def test_write_interpretation_artifact_roundtrip(tmp_path: Path) -> None:
    wc = _sample_world_context()
    p = write_interpretation_from_world_payload(tmp_path, wc)
    assert p and p.is_file()
    raw = json.loads(p.read_text(encoding="utf-8"))
    assert raw.get("schema") == WORLD_CONTEXT_INTERPRETATION_SCHEMA
    assert raw.get("entities_ordered") == ["a", "b"]
