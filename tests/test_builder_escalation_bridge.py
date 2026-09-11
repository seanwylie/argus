"""Builder reconcile → escalation packet bridge."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from argus.builder.escalation_bridge import (
    emit_builder_escalation_if_needed,
    evaluate_builder_escalation_triggers,
    list_operator_visible_builder_packets,
    summarize_latest_builder_escalation,
)
from argus.escalation.rules import (
    RULE_BUILDER_ARGUS_CORE_BREACH,
    RULE_BUILDER_EXECUTION_OUTCOME_BLOCKED,
    RULE_BUILDER_SEMANTIC_SCOPE_BREACH,
)


def test_argus_core_breach_triggers() -> None:
    sc = {
        "schema": "argus.builder_scope_check.v2",
        "path_scope": {
            "argus_core_breach": True,
            "non_product_root_breach": False,
            "scope_breach": True,
            "breach_reasons": ["path_scope:modified_argus_core argus/foo"],
        },
        "semantic_scope_breach": False,
        "scope_breach": True,
        "breach_reasons": ["path_scope:modified_argus_core argus/foo"],
    }
    m = evaluate_builder_escalation_triggers(
        scope_check=sc,
        execution_outcome=None,
        invoke_data=None,
    )
    assert any(x.rule_id == RULE_BUILDER_ARGUS_CORE_BREACH for x in m)


def test_semantic_breach_triggers() -> None:
    sc = {
        "schema": "argus.builder_scope_check.v2",
        "path_scope": {"scope_breach": False, "argus_core_breach": False},
        "semantic_scope_breach": True,
        "semantic_scope": {
            "semantic_breach_reasons": ["semantic_scope: primary_target mismatch"],
        },
        "scope_breach": True,
        "breach_reasons": [],
    }
    m = evaluate_builder_escalation_triggers(
        scope_check=sc,
        execution_outcome=None,
        invoke_data=None,
    )
    assert any(x.rule_id == RULE_BUILDER_SEMANTIC_SCOPE_BREACH for x in m)


def test_execution_blocked_triggers() -> None:
    m = evaluate_builder_escalation_triggers(
        scope_check={"schema": "argus.builder_scope_check.v2", "path_scope": {}},
        execution_outcome={
            "schema": "argus.builder.execution_outcome.v1",
            "outcome": "blocked",
            "reasons": ["scope"],
        },
        invoke_data=None,
    )
    assert any(x.rule_id == RULE_BUILDER_EXECUTION_OUTCOME_BLOCKED for x in m)


def test_clean_scope_no_triggers() -> None:
    sc = {
        "schema": "argus.builder_scope_check.v2",
        "path_scope": {
            "argus_core_breach": False,
            "non_product_root_breach": False,
            "scope_breach": False,
        },
        "semantic_scope_breach": False,
        "scope_breach": False,
    }
    m = evaluate_builder_escalation_triggers(
        scope_check=sc,
        execution_outcome={"outcome": "completed", "reasons": []},
        invoke_data=None,
    )
    assert m == []


def test_emit_creates_packet_argus_core(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    saved: list[Any] = []

    def _fake_save(repo_root: Path, pkt: Any) -> Path:
        saved.append(pkt)
        d = tmp_path / "runs" / "escalations" / "latest"
        d.mkdir(parents=True, exist_ok=True)
        p = d / f"{pkt.packet_id}.json"
        p.write_text("{}", encoding="utf-8")
        return p

    monkeypatch.setattr(
        "argus.builder.escalation_bridge.find_recent_duplicate_packet",
        lambda *a, **k: None,
    )
    monkeypatch.setattr("argus.builder.escalation_bridge.save_packet", _fake_save)

    record = {
        "builder_scope_check": {
            "schema": "argus.builder_scope_check.v2",
            "path_scope": {"argus_core_breach": True, "non_product_root_breach": False},
            "semantic_scope_breach": False,
            "scope_breach": True,
            "breach_reasons": ["x"],
        },
        "execution_outcome": {"outcome": "completed", "reasons": []},
        "builder_branch_review": {"review_status": "unsafe", "review_reasons": []},
        "source_invoke_record_path": None,
    }
    out = emit_builder_escalation_if_needed(tmp_path, "p1", record, invoke_data=None)
    assert out.get("emitted") is True
    assert out.get("packet_id")
    assert saved, "save_packet should have been called"
    assert RULE_BUILDER_ARGUS_CORE_BREACH in (out.get("triggering_rules") or [])


def test_summarize_and_list_operator_visible_packets(tmp_path: Path) -> None:
    d = tmp_path / "runs" / "escalations" / "latest"
    d.mkdir(parents=True, exist_ok=True)
    high = {
        "packet_id": "esc_high",
        "product_id": "p9",
        "risk_level": "high",
        "title": "t",
        "why_stopped": "line1\nline2",
        "summary": "s",
        "created_at": "2026-04-16T14:00:00Z",
        "metadata": {"builder_escalation": True},
    }
    med = {
        "packet_id": "esc_med",
        "product_id": "p9",
        "risk_level": "medium",
        "title": "t",
        "why_stopped": "m",
        "metadata": {"builder_escalation": True},
    }
    (d / "esc_high.json").write_text(json.dumps(high), encoding="utf-8")
    (d / "esc_med.json").write_text(json.dumps(med), encoding="utf-8")
    s = summarize_latest_builder_escalation(tmp_path, "p9")
    assert s["present"] is True
    assert s["severity"] == "high"
    assert s["short_reason"] == "line1"
    assert s["operator_visible"] is True
    vis = list_operator_visible_builder_packets(tmp_path)
    assert len(vis) == 1
    assert vis[0]["packet_id"] == "esc_high"


def test_emit_skips_when_dedupe(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        "argus.builder.escalation_bridge.find_recent_duplicate_packet",
        lambda *a, **k: {"packet_id": "existing", "path_repo": "runs/escalations/latest/x.json"},
    )
    record = {
        "builder_scope_check": {
            "schema": "argus.builder_scope_check.v2",
            "path_scope": {"argus_core_breach": True},
            "scope_breach": True,
            "breach_reasons": [],
        },
        "execution_outcome": {},
        "builder_branch_review": {},
    }
    out = emit_builder_escalation_if_needed(tmp_path, "p1", record, invoke_data=None)
    assert out.get("emitted") is False
    assert out.get("reason") == "dedupe_recent_packet"
