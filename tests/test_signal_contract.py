"""Signal contract evaluation (golden vs mission) — deterministic mapping and artifacts."""

from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path

import pytest

from argus.cli.portfolio_cmd import cmd_portfolio_signal_contract
from argus.observability.signal_contract import (
    SIGNAL_CONTRACT_EVALUATION_SCHEMA,
    compact_signal_contract_row_fields,
    evaluate_signal_contract,
    golden_signals_for_product_type,
    mission_signals_for_mission,
    render_signal_contract_markdown,
    resolve_product_type_bucket,
)
from argus.signals.persistence import latest_path


def _minimal_product_yaml(root: Path, pid: str, *, ptype: str, mission_block: str | None = None) -> None:
    pr = root / "products" / pid
    pr.mkdir(parents=True)
    mission_yaml = ""
    if mission_block:
        mission_yaml = f"\nmission:\n{mission_block}\n"
    (pr / "product.yaml").write_text(
        f"""
id: {pid}
name: T
type: {ptype}
owner:
  team: test
lifecycle:
  stage: idea
metrics:
  local_paths: []
  primary: []
cost:
  monthly_usd: 0
  notes: ""
signals:
  - type: filesystem
    enabled: true
actions:
  start: "./scripts/s.sh"
  stop: "./scripts/s.sh"
  analyze: "./scripts/s.sh"
constraints:
  max_monthly_cost_usd: 1
  min_activity_threshold: 0
{mission_yaml}
""",
        encoding="utf-8",
    )
    (pr / "scripts").mkdir(exist_ok=True)
    (pr / "scripts" / "s.sh").write_text("#!/bin/sh\necho\n", encoding="utf-8")


def _canon(**kwargs: object) -> dict[str, object]:
    base = {
        "signal_id": "sig",
        "product_id": "p1",
        "category": "operational",
        "value": 1,
        "value_type": "gauge",
        "unit": None,
        "source_type": "health",
        "source_ref": "t",
        "observed_at": "2026-01-01T00:00:00Z",
        "collected_at": "2026-01-01T00:00:01Z",
        "freshness_status": "fresh",
    }
    base.update(kwargs)
    return base


def _record(
    *,
    rid: str,
    pid: str,
    signal_type: str,
    tags: list[str],
    canon: dict[str, object] | None,
) -> dict[str, object]:
    c = dict(canon) if canon else None
    if c is not None:
        c.setdefault("product_id", pid)
        c.setdefault("signal_id", f"{rid}-canon")
    return {
        "id": rid,
        "product_id": pid,
        "signal_type": signal_type,
        "source": "test",
        "observed_at": "2026-01-01T00:00:00+00:00",
        "payload": {},
        "tags": tags,
        "canonical": c,
    }


def _write_bundle(root: Path, pid: str, records: list[dict[str, object]]) -> None:
    p = latest_path(root, pid)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": "argus.signal_collection.v1",
        "product_id": pid,
        "collected_at_utc": "2026-01-01T00:00:00+00:00",
        "repo_root": str(root),
        "records": records,
    }
    p.write_text(json.dumps(payload), encoding="utf-8")


def test_golden_signals_for_product_type_buckets() -> None:
    assert "latency" in golden_signals_for_product_type("saas_api")
    assert "page_performance" in golden_signals_for_product_type("website_content")
    assert "execution_success" in golden_signals_for_product_type("utility_cli")
    assert "crash_rate" in golden_signals_for_product_type("mobile_companion")


def test_mission_signals_mapping() -> None:
    assert "conversion_rate" in mission_signals_for_mission("revenue")
    assert "dau_wau" in mission_signals_for_mission("engagement")
    assert "completion_rate" in mission_signals_for_mission("education")


def test_resolve_product_type_bucket() -> None:
    b, d = resolve_product_type_bucket("micro_saas")
    assert b == "saas_api" and d is False
    b2, d2 = resolve_product_type_bucket(None)
    assert b2 == "saas_api" and d2 is True


def test_inputs_observed_signals_bundle_present_and_record_count(tmp_path: Path) -> None:
    """Distinguishes missing ``runs/signals/latest`` from present bundle with zero/heuristic mismatch."""
    _minimal_product_yaml(tmp_path, "io1", ptype="micro_saas")
    pl = evaluate_signal_contract(tmp_path, "io1")
    obs = pl["inputs_observed"]
    assert obs["signals_latest_bundle"]["path_relative"] == "runs/signals/latest/io1.json"
    assert obs["signals_latest_bundle"]["present"] is False
    assert obs["signals_latest_bundle"]["record_count"] == 0
    assert obs["signals_latest_bundle"]["surface_state"] == "missing_file"
    assert obs["signals_latest_bundle"]["collected_at_utc"] is None
    assert obs["temporal_latest_bundle"]["present"] is False
    _write_bundle(tmp_path, "io1", [])
    pl2 = evaluate_signal_contract(tmp_path, "io1")
    obs2 = pl2["inputs_observed"]
    assert obs2["signals_latest_bundle"]["present"] is True
    assert obs2["signals_latest_bundle"]["record_count"] == 0
    assert obs2["signals_latest_bundle"]["surface_state"] == "empty_file"
    assert obs2["signals_latest_bundle"]["collected_at_utc"]
    one = _record(
        rid="r1",
        pid="io1",
        signal_type="metrics",
        tags=["latency"],
        canon=_canon(signal_id="m1", freshness_status="fresh"),
    )
    _write_bundle(tmp_path, "io1", [one])
    pl3 = evaluate_signal_contract(tmp_path, "io1")
    assert pl3["inputs_observed"]["signals_latest_bundle"]["surface_state"] == "non_empty_file"


def test_compact_row_fields_exposes_signals_surface_state(tmp_path: Path) -> None:
    """Operator queue enrichment sees filesystem partition without opening full evaluation JSON."""
    _minimal_product_yaml(tmp_path, "qrow", ptype="micro_saas")
    row = compact_signal_contract_row_fields(tmp_path, "qrow")
    assert row["signal_contract_signals_surface_state"] == "missing_file"
    _write_bundle(tmp_path, "qrow", [])
    row2 = compact_signal_contract_row_fields(tmp_path, "qrow")
    assert row2["signal_contract_signals_surface_state"] == "empty_file"


def test_evaluation_sparse_repo_blocked_and_thin(tmp_path: Path) -> None:
    _minimal_product_yaml(tmp_path, "p1", ptype="micro_saas")
    pl = evaluate_signal_contract(tmp_path, "p1")
    assert pl["schema"] == SIGNAL_CONTRACT_EVALUATION_SCHEMA
    assert pl["operability_status"] == "blocked"
    assert pl["missing_golden_signals"]
    assert any(c.get("kind") == "golden_signal_gap" for c in pl["builder_task_candidates"])
    # Default mission revenue → mission signals recommended
    assert pl["optimization_status"] in ("thin", "partial")


def test_evaluation_richer_repo_sufficient_operability(tmp_path: Path) -> None:
    pid = "rich"
    _minimal_product_yaml(tmp_path, pid, ptype="saas")
    recs = [
        _record(
            rid="r1",
            pid=pid,
            signal_type="metrics",
            tags=["p95", "latency"],
            canon=_canon(signal_id="m1", freshness_status="fresh"),
        ),
        _record(
            rid="r2",
            pid=pid,
            signal_type="health",
            tags=["uptime", "availability"],
            canon=_canon(signal_id="h1", freshness_status="fresh"),
        ),
        _record(
            rid="r3",
            pid=pid,
            signal_type="execution",
            tags=["error", "rate"],
            canon=_canon(signal_id="e1", freshness_status="fresh"),
        ),
        _record(
            rid="r4",
            pid=pid,
            signal_type="metrics",
            tags=["throughput", "rps"],
            canon=_canon(signal_id="t1", freshness_status="fresh"),
        ),
    ]
    _write_bundle(tmp_path, pid, recs)
    pl = evaluate_signal_contract(tmp_path, pid)
    assert pl["operability_status"] == "sufficient"
    assert not pl["missing_golden_signals"]


def test_stale_golden_limits_operability(tmp_path: Path) -> None:
    pid = "stale"
    _minimal_product_yaml(tmp_path, pid, ptype="api")
    recs = [
        _record(
            rid="r1",
            pid=pid,
            signal_type="metrics",
            tags=["p95", "latency"],
            canon=_canon(signal_id="m1", freshness_status="stale"),
        ),
        _record(
            rid="r2",
            pid=pid,
            signal_type="health",
            tags=["uptime"],
            canon=_canon(signal_id="h1", freshness_status="stale"),
        ),
        _record(
            rid="r3",
            pid=pid,
            signal_type="execution",
            tags=["error"],
            canon=_canon(signal_id="e1", freshness_status="stale"),
        ),
        _record(
            rid="r4",
            pid=pid,
            signal_type="metrics",
            tags=["rps", "throughput"],
            canon=_canon(signal_id="t1", freshness_status="stale"),
        ),
    ]
    _write_bundle(tmp_path, pid, recs)
    pl = evaluate_signal_contract(tmp_path, pid)
    assert pl["operability_status"] == "limited"
    assert pl["stale_golden_signals"]


def test_mission_signal_candidates(tmp_path: Path) -> None:
    pid = "m1"
    _minimal_product_yaml(
        tmp_path,
        pid,
        ptype="website",
        mission_block="  objective: revenue\n  drivers: []\n  guardrails: []\n",
    )
    _write_bundle(tmp_path, pid, [])
    pl = evaluate_signal_contract(tmp_path, pid)
    miss_m = pl["missing_mission_signals"]
    assert "conversion_rate" in miss_m or "arppu" in miss_m
    assert any(
        c.get("kind") == "mission_signal_gap" and c.get("priority") == "medium"
        for c in pl["builder_task_candidates"]
    )


def test_markdown_includes_signal_coherence_section(tmp_path: Path) -> None:
    """Operator report reconciles hint vs filesystem vs temporal without collapsing scopes."""
    _minimal_product_yaml(tmp_path, "coh", ptype="micro_saas")
    pl = evaluate_signal_contract(tmp_path, "coh")
    md = render_signal_contract_markdown(pl)
    assert "## Signal coherence (how to read this report)" in md
    assert "compute_signal_contract_hint" in md
    assert "surface_state" in md


def test_cli_writes_artifact(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    pid = "cli_p"
    _minimal_product_yaml(tmp_path, pid, ptype="cli")
    args = Namespace(product_id=pid, json=False, no_save=False)
    code = cmd_portfolio_signal_contract(tmp_path, args)
    assert code == 0
    out = (tmp_path / "runs" / "debug" / "signal_contract" / pid / "latest.json").read_text(encoding="utf-8")
    data = json.loads(out)
    assert data["schema"] == SIGNAL_CONTRACT_EVALUATION_SCHEMA
    captured = capsys.readouterr()
    assert "Wrote signal contract" in captured.out
