"""Chronic portfolio unblock map + intervention evidence_refresh action strings."""

from __future__ import annotations

import json
from pathlib import Path

from argus.portfolio.chronic_portfolio_unblock_report import (
    CHRONIC_PORTFOLIO_UNBLOCK_SCHEMA,
    build_chronic_portfolio_unblock_payload,
    chronic_portfolio_unblock_dir,
    run_chronic_portfolio_unblock_report,
)
from argus.portfolio.cycle import PORTFOLIO_CYCLE_SCHEMA
from argus.portfolio.intervention import (
    PORTFOLIO_INTERVENTION_SCHEMA,
    _recommended_operator_action_evidence_refresh,
)
from argus.portfolio.operator_queue import OPERATOR_QUEUE_SCHEMA


def _queue(entries: list[dict]) -> dict:
    return {
        "schema": OPERATOR_QUEUE_SCHEMA,
        "generated_at_utc": "t",
        "scoring_weights_version": "1",
        "weight_ledger": {},
        "entries": entries,
    }


def _intervention(rows: list[dict]) -> dict:
    return {
        "schema": PORTFOLIO_INTERVENTION_SCHEMA,
        "run_id": "r1",
        "evaluated_at_utc": "t",
        "inputs": {},
        "thresholds": {},
        "flagged_products": rows,
        "stable_benign_products": [],
    }


def _cycle(overall: str, codes: list[str]) -> dict:
    return {
        "schema": PORTFOLIO_CYCLE_SCHEMA,
        "run_id": "c1",
        "ok": True,
        "summary": {
            "overall_operator_recommendation": overall,
            "overall_rationale_codes": codes,
        },
    }


def test_chronic_unblock_merges_queue_and_intervention(tmp_path: Path) -> None:
    d = tmp_path / "runs" / "portfolio" / "operator_queue"
    d.mkdir(parents=True)
    (d / "latest.json").write_text(
        json.dumps(
            _queue(
                [
                    {
                        "product_id": "alpha",
                        "queue_rank": 1,
                        "next_action": "temporal_refresh",
                        "orchestration_status": "stale_refresh_needed",
                        "readiness_tier": "unprofiled",
                        "priority_score": 1.0,
                        "priority_reason": "x",
                        "recommendation": "r",
                    }
                ]
            )
        ),
        encoding="utf-8",
    )
    inv = tmp_path / "runs" / "portfolio" / "intervention"
    inv.mkdir(parents=True)
    (inv / "latest.json").write_text(
        json.dumps(
            _intervention(
                [
                    {
                        "product_id": "alpha",
                        "intervention_category": "evidence_refresh",
                        "severity": "medium",
                        "detection_reason_codes": ["intervention.readiness_tier_debt_stagnation"],
                        "evidence_summary": "e",
                        "recommended_operator_action": "x",
                        "chronicity": "chronic",
                    }
                ]
            )
        ),
        encoding="utf-8",
    )
    cdir = tmp_path / "runs" / "portfolio" / "cycle"
    cdir.mkdir(parents=True)
    (cdir / "latest.json").write_text(
        json.dumps(_cycle("inspect_specific_products", ["cycle.quiescence_inspect"])),
        encoding="utf-8",
    )
    pl = build_chronic_portfolio_unblock_payload(tmp_path, product_ids=["alpha"])
    assert pl["schema"] == CHRONIC_PORTFOLIO_UNBLOCK_SCHEMA
    assert pl["portfolio_cycle_summary"]["overall_operator_recommendation"] == "inspect_specific_products"
    prod = pl["products"][0]
    assert prod["blockage_themes"]
    assert "stale_observability" in prod["blockage_themes"]


def test_chronic_unblock_sparse_honest(tmp_path: Path) -> None:
    pl = build_chronic_portfolio_unblock_payload(tmp_path)
    assert pl["sources_present"]["operator_queue"] is False
    assert pl["products"] == []


def test_run_writes_debug_dir(tmp_path: Path) -> None:
    pl = run_chronic_portfolio_unblock_report(tmp_path, write_artifacts=True)
    assert (chronic_portfolio_unblock_dir(tmp_path) / "latest.json").is_file()
    assert pl["schema"] == CHRONIC_PORTFOLIO_UNBLOCK_SCHEMA


def test_evidence_refresh_action_temporal_stale() -> None:
    s = _recommended_operator_action_evidence_refresh(
        {
            "next_action": "temporal_refresh",
            "orchestration_status": "stale_refresh_needed",
            "readiness_tier": "unprofiled",
        }
    )
    assert "temporal" in s.lower()
    assert "stale_refresh_needed" in s or "stale" in s.lower()


def test_evidence_refresh_action_findings() -> None:
    s = _recommended_operator_action_evidence_refresh(
        {
            "next_action": "findings_generate",
            "orchestration_status": "eligible",
            "readiness_tier": "unprofiled",
        }
    )
    assert "findings" in s.lower()
