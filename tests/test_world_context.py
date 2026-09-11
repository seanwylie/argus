"""World context: schema, persistence, CLI ingest (advisory only)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from argus.dashboard.console_data import world_context_view
from argus.world_context.persist import WORLD_CONTEXT_SCHEMA, write_world_context_artifact
from argus.world_context.service import (
    build_world_context_payload,
    load_world_context,
    summarize_for_operator,
    write_world_context_from_signals,
)
from argus.world_context.signal import normalize_signal

_REPO = Path(__file__).resolve().parents[1]


def _valid_signal() -> dict:
    return {
        "source": "manual_seed",
        "signal_type": "trend",
        "entity": "demo_entity",
        "value": 1200,
        "unit": "views",
        "observed_at_utc": "2026-01-15T12:00:00Z",
        "confidence": "medium",
        "provenance": "operator pasted weekly export",
    }


def test_normalize_signal_ok() -> None:
    n, err = normalize_signal(_valid_signal())
    assert err is None
    assert n is not None
    assert n["freshness_status"] in ("fresh", "stale", "unknown")
    assert n["source"] == "manual_seed"


def test_build_payload_and_roundtrip(tmp_path: Path) -> None:
    payload, errors = build_world_context_payload([_valid_signal()])
    assert not errors
    assert payload["schema"] == WORLD_CONTEXT_SCHEMA
    assert payload["advisory_only"] is True
    assert len(payload["signals"]) == 1
    out = write_world_context_artifact(tmp_path, payload)
    assert out.is_file()
    loaded = load_world_context(tmp_path)
    assert loaded is not None
    assert loaded["summary"]["signal_count"] == 1
    adv = summarize_for_operator(loaded)
    assert adv
    assert "Advisory only" in adv


def test_write_rejects_any_invalid_row(tmp_path: Path) -> None:
    bad = dict(_valid_signal())
    bad["source"] = "not_a_real_source"
    out, _payload, errors = write_world_context_from_signals(tmp_path, [_valid_signal(), bad])
    assert out is None
    assert errors
    assert not (tmp_path / "runs" / "world_context" / "latest.json").is_file()


def test_world_context_view_schema_mismatch() -> None:
    v = world_context_view({"schema": "other"})
    assert v.get("present") is False
    assert v.get("note")


def test_world_context_view_ok() -> None:
    payload, _ = build_world_context_payload([_valid_signal()])
    v = world_context_view(payload)
    assert v["present"] is True
    assert v["signals_preview_rows"]


def test_cli_ingest_and_show(tmp_path: Path) -> None:
    sig_file = tmp_path / "s.json"
    sig_file.write_text(json.dumps([_valid_signal()]), encoding="utf-8")
    env = {**os.environ, "ARGUS_REPO_ROOT": str(tmp_path)}
    r = subprocess.run(
        [sys.executable, "-m", "argus.cli.main", "world-context", "ingest", "--file", str(sig_file)],
        cwd=str(_REPO),
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert r.returncode == 0, (r.stdout, r.stderr)
    p = tmp_path / "runs" / "world_context" / "latest.json"
    assert p.is_file()
    interp = tmp_path / "runs" / "world_context" / "interpretation" / "latest.json"
    assert interp.is_file()
    r2 = subprocess.run(
        [sys.executable, "-m", "argus.cli.main", "world-context", "show", "--json"],
        cwd=str(_REPO),
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert r2.returncode == 0, r2.stderr
    raw = json.loads(r2.stdout)
    assert raw.get("schema") == WORLD_CONTEXT_SCHEMA
