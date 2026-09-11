"""Artifact refinement framework (deterministic / mocked; no network)."""

from __future__ import annotations

import json
import os
import textwrap
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from argus.council.routing import member_profiles_for_artifact
from argus.decision_assessment.evaluate import evaluate_decision_context
from argus.refinement.context_cache import load_cycle_review_context
from argus.refinement.convergence import evaluate_convergence
from argus.refinement.convergence_narrative import build_convergence_narrative
from argus.refinement.dashboard_block import build_refinement_dashboard_block
from argus.refinement.doctor import check_refinement_health
from argus.refinement.models import (
    ArtifactDraft,
    ArtifactType,
    GeneratedBy,
    ObjectionCategory,
    ReviewVerdict,
    SessionStatus,
    StakeholderReview,
    StakeholderType,
)
from argus.refinement.review import (
    _collect_stakeholder_review_with_context,
    load_cursor_review_for_stakeholder,
)
from argus.refinement.round_files import scan_round_chain
from argus.refinement.routing import can_hard_block, council_for
from argus.refinement.session import (
    approve_session,
    create_session,
    load_session,
    run_refinement_cycle,
)
from argus.refinement.signals import refinement_uncertainty_nudge
from argus.refinement.synthesize import synthesize_reviews


def _write(p: Path, content: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(textwrap.dedent(content).strip() + "\n", encoding="utf-8")


def _cursor_review_obj(
    *,
    rationale: str = "cursor file rationale",
    verdict: str = "pass",
) -> dict:
    return {
        "verdict": verdict,
        "blocking": False,
        "confidence_score": 0.88,
        "objection_categories": [],
        "objections": [],
        "suggestions": ["s1"],
        "rationale": rationale,
    }


def _minimal_product(root: Path, product_id: str = "p1") -> None:
    _write(
        root / "products" / product_id / "product.yaml",
        f"""
    id: {product_id}
    name: Test
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
    )
    (root / "products" / product_id / "scripts").mkdir(parents=True, exist_ok=True)
    (root / "products" / product_id / "scripts" / "s.sh").write_text("#!/bin/sh\ntrue\n", encoding="utf-8")


class TestRouting(unittest.TestCase):
    def test_idea_council_has_finance(self) -> None:
        c = council_for(ArtifactType.IDEA)
        ids = [x[0] for x in c]
        self.assertIn(StakeholderType.FINANCE, ids)

    def test_impl_plan_has_bones(self) -> None:
        c = council_for(ArtifactType.IMPLEMENTATION_PLAN)
        ids = [x[0] for x in c]
        self.assertIn(StakeholderType.BONES, ids)

    def test_doctrine_hard_block_product_spec(self) -> None:
        self.assertTrue(can_hard_block(ArtifactType.PRODUCT_SPEC, StakeholderType.DOCTRINE))


class TestSynthesis(unittest.TestCase):
    def test_themes(self) -> None:
        revs = [
            StakeholderReview(
                review_id="r1",
                session_id="s",
                draft_id="d",
                round_number=0,
                stakeholder_type=StakeholderType.FINANCE,
                verdict=ReviewVerdict.CONCERN,
                blocking=False,
                confidence_score=0.5,
                objection_categories=[],
                objections=["cost unclear"],
                suggestions=["add pricing"],
                rationale="x",
                created_at_utc="2026-01-01T00:00:00+00:00",
            )
        ]
        syn = synthesize_reviews("s", "d", 0, revs)
        self.assertIn("concern", syn.themes[0])


class TestConvergence(unittest.TestCase):
    def test_approve_when_all_pass(self) -> None:
        council = council_for(ArtifactType.IDEA)
        revs = []
        for st, w, _ in council:
            revs.append(
                StakeholderReview(
                    review_id="x",
                    session_id="s",
                    draft_id="d",
                    round_number=0,
                    stakeholder_type=st,
                    verdict=ReviewVerdict.PASS,
                    blocking=False,
                    confidence_score=0.9,
                    objection_categories=[],
                    objections=[],
                    suggestions=[],
                    rationale="ok",
                    created_at_utc="2026-01-01T00:00:00+00:00",
                )
            )
        cr = evaluate_convergence(
            session_id="s",
            round_number=0,
            max_rounds=4,
            artifact_type=ArtifactType.IDEA,
            reviews=revs,
            council=council,
        )
        self.assertTrue(cr.converged)
        self.assertEqual(cr.final_status, SessionStatus.APPROVED)

    def test_max_rounds_human(self) -> None:
        council = council_for(ArtifactType.IDEA)
        revs = []
        for st, w, _ in council:
            revs.append(
                StakeholderReview(
                    review_id="x",
                    session_id="s",
                    draft_id="d",
                    round_number=3,
                    stakeholder_type=st,
                    verdict=ReviewVerdict.CONCERN,
                    blocking=False,
                    confidence_score=0.4,
                    objection_categories=[],
                    objections=["x"],
                    suggestions=[],
                    rationale="n",
                    created_at_utc="2026-01-01T00:00:00+00:00",
                )
            )
        cr = evaluate_convergence(
            session_id="s",
            round_number=3,
            max_rounds=4,
            artifact_type=ArtifactType.IDEA,
            reviews=revs,
            council=council,
        )
        self.assertTrue(cr.converged)
        self.assertEqual(cr.final_status, SessionStatus.HUMAN_REVIEW_REQUIRED)

    def test_evaluate_convergence_does_not_attach_narrative(self) -> None:
        council = council_for(ArtifactType.IDEA)
        revs = []
        for st, w, _ in council:
            revs.append(
                StakeholderReview(
                    review_id="x",
                    session_id="s",
                    draft_id="d",
                    round_number=0,
                    stakeholder_type=st,
                    verdict=ReviewVerdict.PASS,
                    blocking=False,
                    confidence_score=0.9,
                    objection_categories=[],
                    objections=[],
                    suggestions=[],
                    rationale="ok",
                    created_at_utc="2026-01-01T00:00:00+00:00",
                )
            )
        cr = evaluate_convergence(
            session_id="s",
            round_number=0,
            max_rounds=4,
            artifact_type=ArtifactType.IDEA,
            reviews=revs,
            council=council,
        )
        self.assertIsNone(cr.narrative)


class TestConvergenceNarrative(unittest.TestCase):
    def test_narrative_deterministic_and_documents_verdicts(self) -> None:
        council = council_for(ArtifactType.IDEA)
        revs = []
        for st, w, _ in council:
            cats = [ObjectionCategory.COST] if st == StakeholderType.FINANCE else []
            revs.append(
                StakeholderReview(
                    review_id="x",
                    session_id="s",
                    draft_id="d",
                    round_number=0,
                    stakeholder_type=st,
                    verdict=ReviewVerdict.PASS,
                    blocking=False,
                    confidence_score=0.9,
                    objection_categories=cats,
                    objections=[],
                    suggestions=[],
                    rationale="ok",
                    created_at_utc="2026-01-01T00:00:00+00:00",
                )
            )
        cr = evaluate_convergence(
            session_id="s",
            round_number=0,
            max_rounds=4,
            artifact_type=ArtifactType.IDEA,
            reviews=revs,
            council=council,
        )
        syn = synthesize_reviews("s", "d", 0, revs)
        n1 = build_convergence_narrative(reviews=revs, convergence=cr, synthesis=syn)
        n2 = build_convergence_narrative(reviews=revs, convergence=cr, synthesis=syn)
        self.assertEqual(n1, n2)
        self.assertIn("quality_gate_pass", cr.reasons)
        self.assertIn("summary_line", n1)
        self.assertIn("grounded_pass=", n1["summary_line"])
        self.assertEqual(n1["grounded_verdict_counts"]["pass"], len(revs))
        self.assertEqual(len(n1["objection_categories_grounded"]), 1)
        self.assertEqual(n1["objection_categories_grounded"][0]["category"], "cost")
        self.assertTrue(n1["flags"]["all_grounded_pass"])
        self.assertTrue(n1["flags"]["meets_weighted_confidence_gate"])
        self.assertFalse(n1["flags"]["any_concern_in_round"])
        self.assertEqual(len(n1["reasons_detail"]), len(cr.reasons))
        self.assertEqual(
            [x["code"] for x in n1["reasons_detail"]],
            list(cr.reasons),
        )
        self.assertTrue(all("legible" in x for x in n1["reasons_detail"]))
        self.assertIsInstance(n1["inspection_lines"], list)
        self.assertGreater(len(n1["inspection_lines"]), 2)
        self.assertIn("Grounded gate:", n1["inspection_lines"][1])

    def test_evaluate_convergence_semantics_unchanged_when_narrative_built(self) -> None:
        """Narrative is display-only; convergence fields match evaluate_convergence in isolation."""
        council = council_for(ArtifactType.IDEA)
        revs = []
        for st, w, _ in council:
            revs.append(
                StakeholderReview(
                    review_id="x",
                    session_id="s",
                    draft_id="d",
                    round_number=0,
                    stakeholder_type=st,
                    verdict=ReviewVerdict.PASS,
                    blocking=False,
                    confidence_score=0.9,
                    objection_categories=[],
                    objections=[],
                    suggestions=[],
                    rationale="ok",
                    created_at_utc="2026-01-01T00:00:00+00:00",
                )
            )
        cr = evaluate_convergence(
            session_id="s",
            round_number=0,
            max_rounds=4,
            artifact_type=ArtifactType.IDEA,
            reviews=revs,
            council=council,
        )
        syn = synthesize_reviews("s", "d", 0, revs)
        n = build_convergence_narrative(reviews=revs, convergence=cr, synthesis=syn)
        self.assertEqual(cr.final_status.value, "approved")
        self.assertEqual(cr.pass_ratio, 1.0)
        self.assertEqual(cr.weighted_confidence, 0.9)
        self.assertEqual(n["reasons_detail"][-1]["code"], cr.reasons[-1])


class TestCursorFileReviews(unittest.TestCase):
    """CURSOR backend uses reviews_in/round_<n>.json — no stub fallback."""

    def test_load_cursor_valid(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            sid = "ref_20260101T000000Z_a1b2c3d4"
            p = root / "runs" / "refinement" / sid / "reviews_in" / "round_0.json"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(
                json.dumps({"product": _cursor_review_obj(rationale="real rationale text")}),
                encoding="utf-8",
            )
            d = load_cursor_review_for_stakeholder(root, sid, 0, StakeholderType.PRODUCT)
            self.assertEqual(d["rationale"], "real rationale text")

    def test_load_missing_file_raises(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            sid = "ref_20260101T000000Z_a1b2c3d4"
            with self.assertRaises(ValueError) as ctx:
                load_cursor_review_for_stakeholder(root, sid, 0, StakeholderType.PRODUCT)
            self.assertIn("cursor review input missing", str(ctx.exception))
            self.assertIn("refine show", str(ctx.exception))

    def test_load_invalid_json_raises(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            sid = "ref_20260101T000000Z_a1b2c3d4"
            p = root / "runs" / "refinement" / sid / "reviews_in" / "round_0.json"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("{not json", encoding="utf-8")
            with self.assertRaises(ValueError) as ctx:
                load_cursor_review_for_stakeholder(root, sid, 0, StakeholderType.PRODUCT)
            self.assertIn("invalid JSON", str(ctx.exception))

    def test_load_missing_stakeholder_key_raises(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            sid = "ref_20260101T000000Z_a1b2c3d4"
            p = root / "runs" / "refinement" / sid / "reviews_in" / "round_0.json"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps({"finance": _cursor_review_obj()}), encoding="utf-8")
            with self.assertRaises(ValueError) as ctx:
                load_cursor_review_for_stakeholder(root, sid, 0, StakeholderType.PRODUCT)
            self.assertIn("missing entry", str(ctx.exception))

    def test_load_missing_required_field_in_review_raises(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            sid = "ref_20260101T000000Z_a1b2c3d4"
            p = root / "runs" / "refinement" / sid / "reviews_in" / "round_0.json"
            p.parent.mkdir(parents=True, exist_ok=True)
            bad = _cursor_review_obj()
            del bad["rationale"]
            p.write_text(json.dumps({"product": bad}), encoding="utf-8")
            with self.assertRaises(ValueError) as ctx:
                load_cursor_review_for_stakeholder(root, sid, 0, StakeholderType.PRODUCT)
            self.assertIn("missing required keys", str(ctx.exception))

    def test_collect_cursor_produces_real_review_not_stub(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _minimal_product(root, "p1")
            sid = "ref_20260101T000000Z_a1b2c3d4"
            p = root / "runs" / "refinement" / sid / "reviews_in" / "round_0.json"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(
                json.dumps({"product": _cursor_review_obj(rationale="semantic rationale from file")}),
                encoding="utf-8",
            )
            draft = ArtifactDraft(
                draft_id="d1",
                session_id=sid,
                round_number=0,
                artifact_type=ArtifactType.IDEA,
                title="t",
                content="body",
                structured_fields={},
                created_at_utc="2026-01-01T00:00:00+00:00",
                generated_by=GeneratedBy.DETERMINISTIC,
            )
            ctx = load_cycle_review_context(root, "p1")
            pmap = {p.stakeholder_type: p for p in member_profiles_for_artifact(ArtifactType.IDEA)}
            prof = pmap[StakeholderType.PRODUCT]
            r = _collect_stakeholder_review_with_context(
                root,
                draft,
                StakeholderType.PRODUCT,
                ArtifactType.IDEA,
                "p1",
                sid,
                ctx,
                profile=prof,
            )
            self.assertEqual(r.backend_used, "cursor")
            self.assertEqual(r.llm_status, "file")
            self.assertEqual(r.rationale, "semantic rationale from file")
            self.assertNotIn("ARGUS-STUB", r.rationale)


class TestSessionFlow(unittest.TestCase):
    @patch.dict(os.environ, {"ARGUS_LLM_ENABLED": "", "ARGUS_OPENAI_API_KEY": ""}, clear=False)
    def test_create_and_run_stub_cycle(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _minimal_product(root, "p1")
            (root / "runs" / "ideas").mkdir(parents=True, exist_ok=True)
            (root / "runs" / "ideas" / "latest.json").write_text(
                json.dumps(
                    {
                        "schema": "argus.ideas_bundle.v1",
                        "ideas": [
                            {
                                "idea_id": "idea_test_1",
                                "title": "Test idea",
                                "description": "Desc",
                                "type": "explore",
                                "source": "synthesis",
                                "novelty_score": 0.5,
                                "adjacency_score": 0.5,
                                "expected_value_score": 0.5,
                                "confidence_score": 0.5,
                                "cost_estimate": "small",
                                "channel_type": "hybrid",
                                "monetization_type": "hybrid",
                                "rationale": "r",
                                "product_id": "p1",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            sess = create_session(root, ArtifactType.IDEA, "idea_test_1", product_id="p1", max_rounds=2)
            self.assertEqual(sess.status, SessionStatus.DRAFT)
            # Grounded CURSOR members require reviews_in/round_0.json (map: product, finance, technical).
            rin = root / "runs" / "refinement" / sess.session_id / "reviews_in"
            rin.mkdir(parents=True, exist_ok=True)
            payload = {
                "product": _cursor_review_obj(rationale="product ok"),
                "finance": _cursor_review_obj(rationale="finance ok"),
                "technical": _cursor_review_obj(rationale="technical ok"),
            }
            (rin / "round_0.json").write_text(json.dumps(payload), encoding="utf-8")
            s2, conv = run_refinement_cycle(root, sess.session_id)
            self.assertIsNotNone(conv)
            self.assertTrue(s2.session_id)
            rev_path = root / "runs" / "refinement" / sess.session_id / "reviews" / "round_0.json"
            self.assertTrue(rev_path.is_file())
            bundle = json.loads(rev_path.read_text(encoding="utf-8"))
            by_st = {x["stakeholder_type"]: x for x in bundle["reviews"]}
            self.assertEqual(by_st["product"]["backend_used"], "cursor")
            self.assertEqual(by_st["product"]["llm_status"], "file")
            self.assertEqual(by_st["finance"]["backend_used"], "cursor")
            self.assertEqual(by_st["technical"]["backend_used"], "cursor")
            self.assertNotIn("ARGUS-STUB", by_st["product"]["rationale"])


class TestSignals(unittest.TestCase):
    def test_nudge_zero_without_sessions(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertEqual(refinement_uncertainty_nudge(root, "p1"), 0.0)


class TestDecisionIntegration(unittest.TestCase):
    def test_evaluate_with_refinement_dir(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _minimal_product(root, "p1")
            (root / "runs" / "signals" / "latest").mkdir(parents=True, exist_ok=True)
            (root / "runs" / "signals" / "latest" / "p1.json").write_text(
                json.dumps({"collected_at_utc": "2026-01-01T00:00:00+00:00", "records": []}),
                encoding="utf-8",
            )
            (root / "runs" / "refinement").mkdir(parents=True, exist_ok=True)
            (root / "runs" / "refinement" / "index.json").write_text(
                json.dumps(
                    {
                        "schema": "argus.refinement_index.v1",
                        "sessions": [
                            {
                                "session_id": "ref_20260101T000000Z_a1b2c3d4",
                                "artifact_type": "idea",
                                "status": "refining",
                                "current_round": 1,
                                "product_id": "p1",
                                "source_id": "x",
                                "updated_at_utc": "2026-01-01T00:00:00+00:00",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            ass = evaluate_decision_context(root, "p1")
            self.assertGreaterEqual(ass.uncertainty_score, 0.0)


class TestApprove(unittest.TestCase):
    @patch.dict(os.environ, {"ARGUS_LLM_ENABLED": ""}, clear=False)
    def test_manual_approve(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _minimal_product(root, "p1")
            (root / "runs" / "ideas").mkdir(parents=True, exist_ok=True)
            (root / "runs" / "ideas" / "latest.json").write_text(
                json.dumps({"schema": "argus.ideas_bundle.v1", "ideas": []}), encoding="utf-8"
            )
            s = create_session(root, ArtifactType.IDEA, "idea_x", product_id="p1", max_rounds=2)
            a = approve_session(root, s.session_id)
            self.assertEqual(a.status, SessionStatus.APPROVED)
            loaded = load_session(root, s.session_id)
            assert loaded is not None
            self.assertEqual(loaded.status, SessionStatus.APPROVED)


class TestRoundChain(unittest.TestCase):
    def test_reviews_without_draft_is_error(self) -> None:
        with TemporaryDirectory() as tmp:
            sd = Path(tmp) / "sess"
            (sd / "reviews").mkdir(parents=True)
            (sd / "reviews" / "round_1.json").write_text("{}", encoding="utf-8")
            err, _warn = scan_round_chain(sd)
            self.assertTrue(any("drafts/round_1" in e for e in err))

    def test_draft_without_reviews_warns(self) -> None:
        with TemporaryDirectory() as tmp:
            sd = Path(tmp) / "sess"
            (sd / "drafts").mkdir(parents=True)
            (sd / "drafts" / "round_1.json").write_text("{}", encoding="utf-8")
            _err, warn = scan_round_chain(sd)
            self.assertTrue(any("reviews/round_1" in w for w in warn))


class TestRefinementDoctor(unittest.TestCase):
    def test_orphan_round_file_surfaces_as_error(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            sid = "ref_20260101T000000Z_a1b2c3d4"
            sd = root / "runs" / "refinement" / sid
            (sd / "reviews").mkdir(parents=True)
            (sd / "reviews" / "round_1.json").write_text('{"reviews":[]}', encoding="utf-8")
            (sd / "session.json").write_text(
                json.dumps(
                    {
                        "session_id": sid,
                        "status": "refining",
                        "current_round": 1,
                        "max_rounds": 4,
                    }
                ),
                encoding="utf-8",
            )
            (root / "runs" / "refinement" / "index.json").write_text(
                json.dumps(
                    {
                        "schema": "argus.refinement_index.v1",
                        "sessions": [
                            {
                                "session_id": sid,
                                "artifact_type": "idea",
                                "status": "refining",
                                "current_round": 1,
                                "product_id": "p1",
                                "source_id": "x",
                                "updated_at_utc": "2026-01-01T00:00:00+00:00",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            err, _warn = check_refinement_health(root)
            self.assertTrue(any("drafts/round_1" in e for e in err))


class TestRefinementDashboardBlock(unittest.TestCase):
    def test_schema_v2_includes_session_details(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "runs" / "refinement").mkdir(parents=True)
            (root / "runs" / "refinement" / "index.json").write_text(
                json.dumps({"schema": "argus.refinement_index.v1", "sessions": []}),
                encoding="utf-8",
            )
            b = build_refinement_dashboard_block(root)
            self.assertEqual(b["schema"], "argus.dashboard_refinement.v2")
            self.assertIn("session_details", b)
            self.assertIsInstance(b["session_details"], dict)


class TestCycleReviewContext(unittest.TestCase):
    def test_load_without_product(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            ctx = load_cycle_review_context(root, None)
            self.assertEqual(ctx.doctrine_excerpt, "")
            self.assertIsInstance(ctx.strategy_summary, str)
