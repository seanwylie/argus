"""Multi-product Builder view (Phase 2B) — inventory × compute_builder_status."""

from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path

import pytest

from argus.builder.multi_product_view import (
    build_builder_multi_product_view,
    builder_row_has_visibility,
    format_builder_multi_product_view_human,
    row_from_builder_status,
)
from argus.builder.status import compute_builder_status
from argus.cli.builder_cmd import run_builder_subcommand
from tests.test_builder_status import (
    _write_invoke,
    _write_next_expansion,
    _write_prepared,
    _write_reconcile,
)
from tests.test_products_inventory import _minimal_valid_yaml, _write


def _write_product(root: Path, product_id: str) -> None:
    pr = root / "products" / product_id
    _write(pr / "product.yaml", _minimal_valid_yaml(product_id))
    (pr / "scripts").mkdir(parents=True, exist_ok=True)
    (pr / "scripts" / "s.sh").write_text("#!/bin/sh\necho\n", encoding="utf-8")


def test_multi_product_empty_inventory_no_rows(tmp_path: Path) -> None:
    view = build_builder_multi_product_view(tmp_path)
    assert view.get("schema") == "argus.builder_multi_product_view.v1"
    assert view.get("products_scanned") == 0
    assert view.get("row_count") == 0
    assert view.get("empty_message")
    human = format_builder_multi_product_view_human(view)
    assert "No Builder" in human or "invoke" in human.lower()


def test_multi_product_includes_product_with_invoke_only(tmp_path: Path) -> None:
    _write_product(tmp_path, "p1")
    _write_product(tmp_path, "p2")
    pid = "p1"
    _write_next_expansion(tmp_path, pid)
    _write_prepared(tmp_path, pid)
    _write_invoke(tmp_path, pid)
    _write_reconcile(tmp_path, pid)
    view = build_builder_multi_product_view(tmp_path)
    assert view.get("products_scanned") == 2
    assert view.get("candidates_considered") == 1
    assert view.get("row_count") == 1
    rows = view.get("rows") or []
    assert rows[0].get("product_id") == "p1"
    assert rows[0].get("merge_readiness") == "blocked"
    assert rows[0].get("contract_or_target_type") == "content_slot"
    assert rows[0].get("invocation_status") == "not_executed"
    assert rows[0].get("updated_at_utc") == "2026-04-16T22:05:00Z"
    assert rows[0].get("latest_invoke_path")
    assert rows[0].get("compact_line") and "p1 |" in rows[0]["compact_line"]
    assert rows[0].get("cleanup_hint") == (
        "Merge is blocked until failed invoke/reconcile or blocked review is resolved."
    )


def test_multi_product_omits_product_without_artifacts(tmp_path: Path) -> None:
    _write_product(tmp_path, "only_inv")
    _write_product(tmp_path, "silent")
    _write_next_expansion(tmp_path, "only_inv")
    _write_prepared(tmp_path, "only_inv")
    _write_invoke(tmp_path, "only_inv")
    _write_reconcile(tmp_path, "only_inv")
    view = build_builder_multi_product_view(tmp_path)
    assert view.get("row_count") == 1
    assert {r.get("product_id") for r in (view.get("rows") or [])} == {"only_inv"}


def test_multi_product_escalation_packet_includes_row(tmp_path: Path) -> None:
    _write_product(tmp_path, "esc1")
    d = tmp_path / "runs" / "escalations" / "latest"
    d.mkdir(parents=True, exist_ok=True)
    pkt = {
        "packet_id": "esc_x",
        "product_id": "esc1",
        "risk_level": "high",
        "title": "Builder anomaly",
        "why_stopped": "x",
        "metadata": {"builder_escalation": True},
    }
    (d / "esc_x.json").write_text(json.dumps(pkt), encoding="utf-8")
    view = build_builder_multi_product_view(tmp_path)
    assert view.get("candidates_considered") >= 1
    rows = view.get("rows") or []
    assert any(r.get("product_id") == "esc1" for r in rows)
    esc_row = next(r for r in rows if r.get("product_id") == "esc1")
    assert "packet" in (esc_row.get("escalation") or "").lower() or "high" in (
        esc_row.get("escalation") or ""
    ).lower()


def test_row_from_builder_status_escalation_and_next(tmp_path: Path) -> None:
    pid = "r1"
    _write_next_expansion(tmp_path, pid)
    _write_prepared(tmp_path, pid)
    _write_invoke(tmp_path, pid)
    _write_reconcile(tmp_path, pid)
    d = tmp_path / "runs" / "escalations" / "latest"
    d.mkdir(parents=True, exist_ok=True)
    pkt = {
        "packet_id": "esc_r1",
        "product_id": pid,
        "risk_level": "high",
        "why_stopped": "scope",
        "metadata": {"builder_escalation": True},
    }
    (d / "esc_r1.json").write_text(json.dumps(pkt), encoding="utf-8")
    payload = compute_builder_status(tmp_path, pid)
    row = row_from_builder_status(payload)
    assert row["product_id"] == pid
    assert row["escalation"]
    assert "runs/escalations" in row["next"].lower() or "Review" in row["next"]


def test_builder_row_has_visibility_requires_signal(tmp_path: Path) -> None:
    pid = "ghost"
    payload = compute_builder_status(tmp_path, pid)
    assert builder_row_has_visibility(payload) is False


def test_portfolio_view_cli_json(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_product(tmp_path, "cli_pv")
    monkeypatch.setattr("argus.cli.builder_cmd.repo_root", lambda: tmp_path)
    args = Namespace(
        builder_command="portfolio-view",
        json=True,
        products_dir=None,
    )
    assert run_builder_subcommand(args) == 0
    raw = json.loads(capsys.readouterr().out)
    assert raw.get("schema") == "argus.builder_multi_product_view.v1"
