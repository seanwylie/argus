"""Tests for escalation packets and trigger rules."""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from argus.core.models.decision import DecisionCandidate
from argus.core.models.enums import (
    ActionType,
    EffortBucket,
    FindingKind,
    LifecycleStage,
    SeverityLevel,
)
from argus.core.models.finding import Finding
from argus.core.models.product import (
    ActionsMap,
    ConstraintsDefinition,
    CostDefinition,
    MetricsDefinition,
    OwnerInfo,
    ProductLifecycle,
    ProductNode,
    ProductTypeInfo,
    SignalDefinition,
)
from argus.core.serialize import dumps_json, loads_json
from argus.decision.engine import generate_decisions
from argus.decision.intents import DecisionIntent
from argus.escalation.models import EscalationOption, EscalationPacket, RiskLevel, SourceType
from argus.escalation.packet import (
    build_packet,
    default_options,
    new_packet_id,
    packet_from_dict,
    packet_to_json_dict,
    save_packet,
)
from argus.escalation.render import render_markdown
from argus.escalation.rules import (
    RULE_CONFIDENCE_TOO_LOW,
    RULE_COST_OVER_CEILING,
    RULE_KILL_OR_DEPRECATE,
    RULE_MISSING_BUSINESS_DATA,
    TriggerMatch,
    evaluate_triggers,
    max_risk_for_matches,
)
from argus.lifecycle.scoring import assess_lifecycle


def _product(
    pid: str = "p1",
    *,
    stage: LifecycleStage = LifecycleStage.BUILD,
    cap: float = 20.0,
    monthly: float | None = 5.0,
) -> ProductNode:
    return ProductNode(
        id=pid,
        name="P",
        owner=OwnerInfo(team="t"),
        metrics=MetricsDefinition(local_paths=[], primary=[]),
        cost=CostDefinition(monthly_usd=monthly),
        signals=[SignalDefinition(type="filesystem", enabled=True)],
        actions=ActionsMap(),
        constraints=ConstraintsDefinition(max_monthly_cost_usd=cap),
        lifecycle=ProductLifecycle(stage=stage),
        product_root=f"products/{pid}",
        config_path=f"products/{pid}/product.yaml",
        type_info=ProductTypeInfo(type="app", status="active"),
    )


def _finding(
    kind: FindingKind,
    *,
    pid: str = "p1",
    sev: SeverityLevel = SeverityLevel.MEDIUM,
    confidence: float | None = 0.8,
    evidence: dict | None = None,
) -> Finding:
    return Finding(
        id=f"find-{kind.value}",
        product_id=pid,
        kind=kind,
        severity=sev,
        effort=EffortBucket.SMALL,
        title="t",
        summary="s",
        recommendation="r",
        source_signals=["sig-1"],
        evidence=evidence or {},
        confidence=confidence,
        created_at=datetime.now(timezone.utc),
    )


class TestTriggerRules(unittest.TestCase):
    def test_cost_over_ceiling_from_product(self) -> None:
        p = _product(monthly=100.0, cap=50.0)
        a = assess_lifecycle(p, [])
        m = evaluate_triggers(p, [], a, [])
        ids = [x.rule_id for x in m]
        self.assertIn(RULE_COST_OVER_CEILING, ids)

    def test_cost_risk_finding_high(self) -> None:
        p = _product(monthly=5.0, cap=100.0)
        f = _finding(FindingKind.COST_RISK, sev=SeverityLevel.HIGH)
        a = assess_lifecycle(p, [f])
        m = evaluate_triggers(p, [f], a, [])
        self.assertTrue(any(x.rule_id == RULE_COST_OVER_CEILING for x in m))

    def test_missing_business_data(self) -> None:
        p = _product()
        f = _finding(FindingKind.QUALITY_ISSUE, sev=SeverityLevel.HIGH, confidence=None)
        a = assess_lifecycle(p, [f])
        m = evaluate_triggers(p, [f], a, [])
        self.assertTrue(any(x.rule_id == RULE_MISSING_BUSINESS_DATA for x in m))

    def test_confidence_too_low_destructive(self) -> None:
        p = _product()
        a = assess_lifecycle(p, [])
        c = DecisionCandidate(
            id="d1",
            product_id=p.id,
            action_type=ActionType.DEPRECATE,
            summary="dep",
            confidence=0.2,
            metadata={"intent": DecisionIntent.DEPRECATE_PRODUCT.value},
        )
        m = evaluate_triggers(p, [], a, [c])
        self.assertTrue(any(x.rule_id == RULE_CONFIDENCE_TOO_LOW for x in m))

    def test_max_risk_critical(self) -> None:
        m = [
            TriggerMatch(RULE_KILL_OR_DEPRECATE, "x"),
        ]
        self.assertEqual(max_risk_for_matches(m), "critical")


class TestPacketModel(unittest.TestCase):
    def test_option_generation_count(self) -> None:
        opts = default_options("my-product")
        self.assertEqual(len(opts), 5)
        actions = {o.action for o in opts}
        self.assertIn("proceed_anyway", actions)
        self.assertIn("gather_more_data", actions)

    def test_packet_id_format(self) -> None:
        pid = new_packet_id("foo-bar")
        self.assertTrue(pid.startswith("esc_"))
        self.assertIn("foo-bar", pid)

    def test_json_roundtrip(self) -> None:
        pkt = EscalationPacket(
            packet_id="esc_test_1",
            created_at=datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc),
            product_id="p1",
            source_type=SourceType.COMPOSITE,
            source_id="x",
            stopped_stage="execution_gate",
            title="T",
            summary="S",
            why_stopped="W",
            risk_level=RiskLevel.HIGH,
            triggering_rules=["a"],
            options=[
                EscalationOption(
                    action="a",
                    description="d",
                    command="c",
                    risk="low",
                    requires_human_confirmation=False,
                )
            ],
        )
        d = packet_to_json_dict(pkt)
        blob = dumps_json(d)
        back = loads_json(blob)
        p2 = packet_from_dict(back)
        self.assertEqual(p2.packet_id, pkt.packet_id)
        self.assertEqual(p2.risk_level, pkt.risk_level)
        self.assertEqual(len(p2.options), 1)


class TestPacketWrite(unittest.TestCase):
    def test_save_writes_latest(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "products").mkdir()
            p = _product(pid="write-test")
            f = _finding(FindingKind.COST_RISK, pid="write-test", sev=SeverityLevel.HIGH)
            a, cands = generate_decisions(p, [f])
            matches = evaluate_triggers(p, [f], a, cands)
            self.assertTrue(matches)
            pkt = build_packet(
                product=p,
                findings=[f],
                assessment=a,
                candidates=cands,
                matches=matches,
            )
            path = save_packet(root, pkt)
            self.assertTrue(path.is_file())
            latest = root / "runs" / "escalations" / "latest" / f"{pkt.packet_id}.json"
            self.assertTrue(latest.is_file())
            data = json.loads(latest.read_text(encoding="utf-8"))
            self.assertEqual(data["product_id"], "write-test")
            self.assertIn("risk_score", data)
            self.assertIn("decision_confidence_enrichment", data.get("metadata", {}))


class TestPacketEnrichment(unittest.TestCase):
    def test_build_includes_decision_confidence_fields(self) -> None:
        p = _product()
        f = _finding(FindingKind.COST_RISK, sev=SeverityLevel.HIGH)
        a, cands = generate_decisions(p, [f])
        m = evaluate_triggers(p, [f], a, cands)
        self.assertTrue(m)
        pkt = build_packet(
            product=p,
            findings=[f],
            assessment=a,
            candidates=cands,
            matches=m,
            repo_root=Path("/tmp"),
        )
        self.assertIsNotNone(pkt.risk_score)
        self.assertIsNotNone(pkt.escalation_pressure)
        self.assertTrue(pkt.metadata.get("decision_confidence_enrichment"))

    def test_legacy_json_without_enrichment_fields(self) -> None:
        legacy = {
            "packet_id": "esc_legacy_only",
            "created_at": "2026-01-01T12:00:00+00:00",
            "product_id": "p",
            "source_type": "composite",
            "source_id": "x",
            "stopped_stage": "execution_gate",
            "title": "t",
            "summary": "s",
            "why_stopped": "w",
            "risk_level": "high",
            "triggering_rules": ["rule_a"],
            "options": [
                {
                    "action": "a",
                    "description": "d",
                    "command": "c",
                    "risk": "low",
                    "requires_human_confirmation": False,
                }
            ],
            "context_files": [],
            "notes": "",
            "metadata": {},
        }
        pkt = packet_from_dict(legacy)
        self.assertIsNone(pkt.confidence_score)
        self.assertEqual(pkt.top_uncertainty_factors, [])
        self.assertIsNone(pkt.exploratory_action_considered)

    def test_missing_context_graceful_scores(self) -> None:
        p = _product()
        f = _finding(FindingKind.COST_RISK, sev=SeverityLevel.HIGH)
        a, cands = generate_decisions(p, [f])
        m = evaluate_triggers(p, [f], a, cands)
        pkt = build_packet(
            product=p,
            findings=[f],
            assessment=a,
            candidates=cands,
            matches=m,
            repo_root=None,
        )
        self.assertIsNone(pkt.recurrence_risk_score)

    def test_recurrence_from_prior_packets_on_disk(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            lat = root / "runs" / "escalations" / "latest"
            lat.mkdir(parents=True)
            now = datetime.now(timezone.utc).isoformat()
            for name in ("esc_20260101T120000Z_rp.json", "esc_20260102T120000Z_rp.json"):
                lat.joinpath(name).write_text(
                    json.dumps(
                        {
                            "packet_id": name.replace(".json", ""),
                            "product_id": "rec-p",
                            "created_at": now,
                        }
                    ),
                    encoding="utf-8",
                )
            p = _product(pid="rec-p")
            f = _finding(FindingKind.COST_RISK, pid="rec-p", sev=SeverityLevel.HIGH)
            a, cands = generate_decisions(p, [f])
            m = evaluate_triggers(p, [f], a, cands)
            pkt = build_packet(
                product=p,
                findings=[f],
                assessment=a,
                candidates=cands,
                matches=m,
                repo_root=root,
            )
            self.assertIsNotNone(pkt.recurrence_risk_score)
            self.assertGreaterEqual(pkt.recurrence_risk_score or 0, 0.4)

    def test_render_markdown_operator_section(self) -> None:
        pkt = EscalationPacket(
            packet_id="esc_md",
            created_at=datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc),
            product_id="p1",
            source_type=SourceType.COMPOSITE,
            source_id="x",
            stopped_stage="execution_gate",
            title="T",
            summary="S",
            why_stopped="W",
            risk_level=RiskLevel.HIGH,
            triggering_rules=["a"],
            options=[
                EscalationOption(
                    action="a",
                    description="d",
                    command="c",
                    risk="low",
                    requires_human_confirmation=False,
                )
            ],
            risk_score=0.55,
            uncertainty_score=0.4,
            top_uncertainty_factors=["[x] sample"],
            top_blocking_factors=["[y] block"],
            exploratory_action_considered="experiment intent in stack",
            exploratory_action_rejected_reason="policy gate",
        )
        md = render_markdown(pkt)
        self.assertIn("operators", md.lower())
        self.assertIn("Leap-of-faith", md)


if __name__ == "__main__":
    unittest.main()
