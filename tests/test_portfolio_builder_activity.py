"""Portfolio Builder activity rollup (coordination artifact only)."""

from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path

import pytest

from argus.builder.history_rollup import BUILDER_HISTORY_ROW_SCHEMA
from argus.builder.status import compute_builder_status
from argus.cli.portfolio_cmd import run_portfolio_subcommand
from argus.portfolio.builder_activity import (
    PORTFOLIO_BUILDER_ACTIVITY_SCHEMA,
    build_operator_summary_builder_snapshot,
    build_portfolio_builder_activity_payload,
    group_attention_products_by_category,
    portfolio_builder_activity_dir,
    product_row_from_builder_status,
    write_portfolio_builder_activity_artifacts,
)
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


def test_operator_summary_snapshot_graceful_when_missing_artifact(tmp_path: Path) -> None:
    snap = build_operator_summary_builder_snapshot(tmp_path)
    assert snap.get("artifact_present") is False
    assert "operator_hint" in snap


def test_operator_summary_snapshot_attention_from_scope_breach(tmp_path: Path) -> None:
    _write_product(tmp_path, "bad")
    pl = build_portfolio_builder_activity_payload(tmp_path)
    assert pl.get("product_count") == 0
    # Synthetic row: would normally come from status rollup
    prods = [
        {
            "product_id": "bad",
            "updated_at_utc": "2026-04-16T12:00:00Z",
            "invocation_status": "ok",
            "merge_readiness": "review_required",
            "execution_outcome": "completed",
            "scope_breach": True,
            "review_status": "review_required",
            "trust_posture": "degraded",
        }
    ]
    pl2 = {**pl, "products": prods, "product_count": 1}
    write_portfolio_builder_activity_artifacts(tmp_path, payload=pl2)
    snap = build_operator_summary_builder_snapshot(tmp_path)
    assert snap.get("artifact_present") is True
    assert snap.get("attention_count", 0) >= 1
    grp = snap.get("attention_products_grouped") or {}
    assert isinstance(grp, dict)
    assert grp.get("scope_safety") and len(grp["scope_safety"]) >= 1
    assert snap.get("attention_products_grouped") == group_attention_products_by_category(
        snap.get("attention_products") or []
    )


def test_operator_snapshot_includes_builder_outcome_compact_fields(tmp_path: Path) -> None:
    """Operator snapshot surfaces Phase 3 outcome one-liners on recent rows and preview."""
    _write_product(tmp_path, "pa1")
    pid = "pa1"
    _write_next_expansion(tmp_path, pid)
    _write_prepared(tmp_path, pid)
    _write_invoke(tmp_path, pid)
    _write_reconcile(tmp_path, pid)
    write_portfolio_builder_activity_artifacts(tmp_path)
    snap = build_operator_summary_builder_snapshot(tmp_path)
    assert snap.get("artifact_present") is True
    prev = snap.get("builder_outcome_lines_preview") or []
    assert prev and "Outcome:" in prev[0]
    rr = snap.get("recent_runs") or []
    assert rr and rr[0].get("builder_outcome_compact")
    assert rr[0].get("comparison_evidence_strength") in ("none", "weak", "moderate")


def test_payload_includes_product_with_builder_records(tmp_path: Path) -> None:
    _write_product(tmp_path, "pa1")
    _write_product(tmp_path, "quiet")
    pid = "pa1"
    _write_next_expansion(tmp_path, pid)
    _write_prepared(tmp_path, pid)
    _write_invoke(tmp_path, pid)
    _write_reconcile(tmp_path, pid)
    pl = build_portfolio_builder_activity_payload(tmp_path)
    assert pl.get("schema") == PORTFOLIO_BUILDER_ACTIVITY_SCHEMA
    assert pl.get("product_count") == 1
    assert pl.get("inventory_products_scanned") == 2
    prods = pl.get("products") or []
    assert len(prods) == 1
    assert prods[0].get("product_id") == "pa1"
    assert prods[0].get("latest_invoke_path")
    assert prods[0].get("latest_reconcile_path")
    assert "runs/builder/" in (prods[0].get("latest_invoke_path") or "")


def test_payload_omits_inventory_without_builder_artifacts(tmp_path: Path) -> None:
    _write_product(tmp_path, "only_here")
    _write_product(tmp_path, "no_builder")
    pid = "only_here"
    _write_next_expansion(tmp_path, pid)
    _write_prepared(tmp_path, pid)
    _write_invoke(tmp_path, pid)
    _write_reconcile(tmp_path, pid)
    pl = build_portfolio_builder_activity_payload(tmp_path)
    ids = {p.get("product_id") for p in (pl.get("products") or [])}
    assert ids == {"only_here"}


def test_escalation_only_product_included(tmp_path: Path) -> None:
    _write_product(tmp_path, "esc_only")
    d = tmp_path / "runs" / "escalations" / "latest"
    d.mkdir(parents=True, exist_ok=True)
    pkt = {
        "packet_id": "esc_z",
        "product_id": "esc_only",
        "risk_level": "medium",
        "metadata": {"builder_escalation": True},
    }
    (d / "esc_z.json").write_text(json.dumps(pkt), encoding="utf-8")
    pl = build_portfolio_builder_activity_payload(tmp_path)
    assert any(p.get("product_id") == "esc_only" for p in (pl.get("products") or []))


def test_product_row_matches_status_truth(tmp_path: Path) -> None:
    pid = "truth"
    _write_next_expansion(tmp_path, pid)
    _write_prepared(tmp_path, pid)
    _write_invoke(tmp_path, pid)
    inv_path = tmp_path / "runs" / "builder" / "invoke" / pid / "latest.json"
    raw = json.loads(inv_path.read_text(encoding="utf-8"))
    raw["execution_contract_kind"] = "content_slot"
    inv_path.write_text(json.dumps(raw), encoding="utf-8")
    _write_reconcile(tmp_path, pid)
    st = compute_builder_status(tmp_path, pid)
    row = product_row_from_builder_status(st)
    assert row.get("rollup_schema") == BUILDER_HISTORY_ROW_SCHEMA
    assert row.get("review_status") == st["latest_reconcile"].get("review_status")
    assert row.get("execution_outcome") == st["latest_reconcile"].get("execution_outcome")
    assert row.get("execution_contract_kind") == "content_slot"
    assert row.get("declared_target_type") == "content_slot"
    assert row.get("next_expansion_path") and "next_expansion.json" in str(
        row.get("next_expansion_path")
    )
    assert row.get("derived_from_schema") == "argus.builder_status.v1"


def test_write_artifacts_latest_and_stamped(tmp_path: Path) -> None:
    _write_product(tmp_path, "w1")
    pid = "w1"
    _write_next_expansion(tmp_path, pid)
    _write_prepared(tmp_path, pid)
    _write_invoke(tmp_path, pid)
    _write_reconcile(tmp_path, pid)
    pl = build_portfolio_builder_activity_payload(tmp_path)
    rid = pl.get("run_id")
    assert isinstance(rid, str) and rid
    stamped, latest, latest_md = write_portfolio_builder_activity_artifacts(
        tmp_path,
        payload=pl,
    )
    assert latest.name == "latest.json"
    assert stamped.name == f"{rid}.json"
    assert latest_md.name == "latest.md"
    raw = json.loads(latest.read_text(encoding="utf-8"))
    assert raw.get("schema") == PORTFOLIO_BUILDER_ACTIVITY_SCHEMA
    assert (portfolio_builder_activity_dir(tmp_path) / f"{rid}.json").is_file()


def test_cli_no_save_json(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_product(tmp_path, "cli_ba")
    pid = "cli_ba"
    _write_next_expansion(tmp_path, pid)
    _write_prepared(tmp_path, pid)
    _write_invoke(tmp_path, pid)
    _write_reconcile(tmp_path, pid)
    monkeypatch.setattr("argus.cli.portfolio_cmd.repo_root", lambda: tmp_path)
    args = Namespace(
        portfolio_command="builder-activity",
        json=True,
        no_save=True,
        products_dir=None,
    )
    assert run_portfolio_subcommand(args) == 0
    out = json.loads(capsys.readouterr().out)
    assert out.get("schema") == PORTFOLIO_BUILDER_ACTIVITY_SCHEMA
