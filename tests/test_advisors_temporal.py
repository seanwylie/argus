"""Temporal grounding for advisor prompts and consensus."""

from __future__ import annotations

import json
import textwrap
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from argus.advisors.consensus import build_consensus
from argus.advisors.context import ConsultationContext, gather_consultation_context
from argus.advisors.models import AdvisorResponse
from argus.advisors.prompts import build_user_prompt
from argus.advisors.registry import global_advisors
from argus.advisors.temporal import build_temporal_grounding, legacy_string_context_grounding
from argus.core.models.enums import EffortBucket, FindingKind, SeverityLevel
from argus.core.models.finding import Finding
from argus.findings.persistence import save_findings_bundle


def _write(p: Path, content: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(textwrap.dedent(content).strip() + "\n", encoding="utf-8")


def _minimal_product_yaml(pid: str) -> str:
    return f"""
    id: {pid}
    name: {pid}
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
      max_monthly_cost_usd: 100
      min_activity_threshold: 0
    """


class TestTemporalPromptGrounding(unittest.TestCase):
    def test_prompt_contains_temporal_section_and_freshness(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pr = root / "products" / "tp"
            _write(pr / "product.yaml", _minimal_product_yaml("tp"))
            (pr / "scripts").mkdir(parents=True)
            (pr / "scripts" / "s.sh").write_text("#!/bin/sh\n", encoding="utf-8")
            ctx = gather_consultation_context(root, "tp")
            self.assertIsNotNone(ctx.temporal)
            adv = global_advisors()[0]
            user = build_user_prompt(adv, ctx)
            self.assertIn("Temporal grounding", user)
            self.assertIn("freshness risk", user.lower())
            self.assertIn("Recent signal records", user)
            self.assertIn("do not invent live metrics", user.lower())

    def test_legacy_override_prompt_states_ambiguity(self) -> None:
        leg = legacy_string_context_grounding("2026-01-01T00:00:00+00:00")
        ctx = ConsultationContext(
            product_id="x",
            findings_section="manual",
            temporal=leg,
        )
        adv = global_advisors()[0]
        user = build_user_prompt(adv, ctx)
        self.assertIn("ambiguous", user.lower())


class TestStaleAndMissingTemporal(unittest.TestCase):
    def test_stale_findings_increases_freshness_risk(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pr = root / "products" / "st"
            _write(pr / "product.yaml", _minimal_product_yaml("st"))
            (pr / "scripts").mkdir(parents=True)
            (pr / "scripts" / "s.sh").write_text("#!/bin/sh\n", encoding="utf-8")
            save_findings_bundle(
                root,
                "st",
                [
                    Finding(
                        id="f1",
                        product_id="st",
                        kind=FindingKind.CURRENT_RISK,
                        severity=SeverityLevel.MEDIUM,
                        effort=EffortBucket.SMALL,
                        title="t",
                        summary="s",
                        recommendation="r",
                    )
                ],
            )
            old = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
            lp = root / "runs" / "findings" / "latest" / "st.json"
            data = json.loads(lp.read_text(encoding="utf-8"))
            data["generated_at_utc"] = old
            lp.write_text(json.dumps(data), encoding="utf-8")

            tg = build_temporal_grounding(root, "st")
            fsrc = next(s for s in tg.sources if s.key == "findings")
            self.assertEqual(fsrc.status, "stale")
            self.assertGreater(tg.overall_freshness_risk, 0.2)

    def test_missing_sources_listed(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pr = root / "products" / "ms"
            _write(pr / "product.yaml", _minimal_product_yaml("ms"))
            (pr / "scripts").mkdir(parents=True)
            (pr / "scripts" / "s.sh").write_text("#!/bin/sh\n", encoding="utf-8")
            tg = build_temporal_grounding(root, "ms")
            self.assertIn("signals", tg.missing_sources)
            self.assertIn("findings", tg.missing_sources)


class TestConsensusTemporal(unittest.TestCase):
    def test_consensus_reduces_confidence_when_stale(self) -> None:
        advisors = global_advisors()[:2]
        responses_fresh: list[AdvisorResponse] = []
        responses_stale: list[AdvisorResponse] = []
        for a in advisors:
            r = AdvisorResponse(
                advisor_id=a.id,
                archetype=a.archetype,
                recommendation="r",
                rationale="x",
                risk_assessment="low",
                confidence=0.8,
                metadata={"stance": 0.5, "temporal_freshness_penalty": 0.05},
            )
            responses_fresh.append(r)
            responses_stale.append(
                AdvisorResponse(
                    advisor_id=a.id,
                    archetype=a.archetype,
                    recommendation="r",
                    rationale="x",
                    risk_assessment="low",
                    confidence=0.8,
                    metadata={"stance": 0.5, "temporal_freshness_penalty": 0.9},
                )
            )
        c1 = build_consensus(Path("/tmp"), "p", advisors, responses_fresh, temporal_grounding=None)
        c2 = build_consensus(Path("/tmp"), "p", advisors, responses_stale, temporal_grounding=None)
        self.assertGreater(c1.confidence_score, c2.confidence_score)
        self.assertGreater(c2.freshness_adjustment_applied, c1.freshness_adjustment_applied)


if __name__ == "__main__":
    unittest.main()
