"""Builder work orders (signal contract → durable artifacts; no execution)."""

from __future__ import annotations

from argparse import Namespace
from pathlib import Path

import pytest

from argus.builder.work_orders import (
    BUILDER_WORK_ORDER_SCHEMA,
    BUILDER_WORK_ORDERS_BUNDLE_SCHEMA,
    build_work_orders_bundle_from_signal_contract,
    build_work_orders_from_candidates,
    load_latest_work_order_bundle,
    render_cursor_implementation_brief,
    write_work_order_artifacts,
)
from argus.cli.builder_cmd import run_builder_subcommand
from argus.observability.signal_contract import evaluate_signal_contract


def _minimal_product(root: Path, pid: str) -> None:
    pr = root / "products" / pid
    pr.mkdir(parents=True)
    (pr / "scripts").mkdir(exist_ok=True)
    (pr / "scripts" / "s.sh").write_text("#!/bin/sh\necho\n", encoding="utf-8")
    (pr / "product.yaml").write_text(
        f"""
id: {pid}
name: T
type: micro_saas
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
""",
        encoding="utf-8",
    )


def test_work_orders_from_signal_contract_golden_are_cursor_high(tmp_path: Path) -> None:
    _minimal_product(tmp_path, "p1")
    evaluate_signal_contract(tmp_path, "p1")
    bundle = build_work_orders_bundle_from_signal_contract(tmp_path, "p1")
    assert bundle["schema"] == BUILDER_WORK_ORDERS_BUNDLE_SCHEMA
    wos = bundle["work_orders"]
    assert wos
    golden = [w for w in wos if w.get("work_type") in ("golden_signal_gap", "golden_signal_stale")]
    assert golden
    assert all(w["suggested_backend"] == "cursor" for w in golden)
    assert all(w["priority"] == "high" for w in golden)
    assert all(w["schema"] == BUILDER_WORK_ORDER_SCHEMA for w in wos)
    assert all("created_at_utc" in w for w in wos)


def test_mission_gap_filesystem_medium(tmp_path: Path) -> None:
    _minimal_product(tmp_path, "p2")
    sc = evaluate_signal_contract(tmp_path, "p2")
    mission_wos = [
        w
        for w in build_work_orders_from_candidates(
            product_id="p2",
            repo_root=tmp_path,
            signal_contract_eval=sc,
        )
        if w.get("work_type") == "mission_signal_gap"
    ]
    assert mission_wos
    assert all(w["suggested_backend"] == "filesystem" for w in mission_wos)
    assert all(w["priority"] == "medium" for w in mission_wos)


def test_backend_filter_cursor_only(tmp_path: Path) -> None:
    _minimal_product(tmp_path, "p3")
    evaluate_signal_contract(tmp_path, "p3")
    bundle = build_work_orders_bundle_from_signal_contract(tmp_path, "p3", backend_filter="cursor")
    for w in bundle["work_orders"]:
        assert w["suggested_backend"] == "cursor"


def test_write_artifacts_and_load(tmp_path: Path) -> None:
    _minimal_product(tmp_path, "p4")
    bundle = build_work_orders_bundle_from_signal_contract(tmp_path, "p4")
    j, md, st = write_work_order_artifacts(tmp_path, bundle)
    assert j.is_file() and md.is_file() and st.is_file()
    loaded = load_latest_work_order_bundle(tmp_path, "p4")
    assert loaded and loaded.get("schema") == BUILDER_WORK_ORDERS_BUNDLE_SCHEMA


def test_render_brief_contains_validate_and_acceptance(tmp_path: Path) -> None:
    _minimal_product(tmp_path, "p5")
    bundle = build_work_orders_bundle_from_signal_contract(tmp_path, "p5")
    wo = bundle["work_orders"][0]
    text = render_cursor_implementation_brief(wo)
    assert "Acceptance criteria" in text
    assert "Preserve behavior" in text
    assert "argus portfolio signal-contract p5" in text


def test_cli_work_orders_and_render_brief(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _minimal_product(tmp_path, "cliwo")
    monkeypatch.setattr("argus.cli.builder_cmd.repo_root", lambda: tmp_path)

    a1 = Namespace(
        builder_command="work-orders",
        product_id="cliwo",
        json=False,
        no_save=False,
        backend=None,
    )
    assert run_builder_subcommand(a1) == 0
    assert (tmp_path / "runs" / "builder" / "work_orders" / "cliwo" / "latest.json").is_file()

    a2 = Namespace(
        builder_command="render-brief",
        product_id="cliwo",
        work_order_id=None,
        json=False,
        no_save=True,
    )
    assert run_builder_subcommand(a2) == 0
