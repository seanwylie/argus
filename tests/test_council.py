"""Council routing, convergence grounding, synthesis source tags."""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from argus.council.models import CouncilMode, CouncilProfile, ReviewDimension
from argus.council.profiles import default_council_profile, profile_for_idea
from argus.council.routing import council_entries_for_refinement, validate_council_profile
from argus.refinement.convergence import evaluate_convergence
from argus.refinement.models import (
    ArtifactType,
    ObjectionCategory,
    ReviewVerdict,
    SessionStatus,
    StakeholderReview,
    StakeholderType,
)
from argus.refinement.routing import can_hard_block, council_for
from argus.refinement.synthesize import synthesize_reviews
from tests.test_refinement import _minimal_product


def _rev(
    st: StakeholderType,
    verdict: ReviewVerdict,
    *,
    council_mode: str = "grounded",
    blocking: bool = False,
) -> StakeholderReview:
    return StakeholderReview(
        review_id=f"rev_{st.value}",
        session_id="s",
        draft_id="d",
        round_number=0,
        stakeholder_type=st,
        verdict=verdict,
        blocking=blocking,
        confidence_score=0.9,
        objection_categories=[ObjectionCategory.AMBIGUITY],
        objections=[],
        suggestions=[],
        rationale="t",
        created_at_utc="2020-01-01T00:00:00+00:00",
        council_mode=council_mode,
        backend_used="deterministic",
    )


class TestCouncilRouting(unittest.TestCase):
    def test_idea_includes_finance_and_outsiders(self) -> None:
        cp = profile_for_idea()
        ids = [m.stakeholder_type for m in cp.members]
        self.assertIn(StakeholderType.FINANCE, ids)
        self.assertIn(StakeholderType.INVESTOR, ids)
        self.assertIn(StakeholderType.MARKETER, ids)
        self.assertEqual(validate_council_profile(cp), [])

    def test_outsider_not_hard_block(self) -> None:
        self.assertFalse(can_hard_block(ArtifactType.IDEA, StakeholderType.INVESTOR))

    def test_implementation_plan_grounded_only(self) -> None:
        cp = default_council_profile(ArtifactType.IMPLEMENTATION_PLAN)
        self.assertEqual(cp.outsider_count(), 0)
        self.assertGreaterEqual(cp.grounded_count(), 1)

    def test_council_matches_refinement_routing(self) -> None:
        ce = council_entries_for_refinement(ArtifactType.IDEA)
        cf = council_for(ArtifactType.IDEA)
        self.assertEqual(len(ce), len(cf))


class TestConvergenceOutsider(unittest.TestCase):
    def test_outsider_fail_does_not_reject_when_grounded_pass(self) -> None:
        council = council_for(ArtifactType.IDEA)
        reviews = [
            _rev(StakeholderType.PRODUCT, ReviewVerdict.PASS),
            _rev(StakeholderType.FINANCE, ReviewVerdict.PASS),
            _rev(StakeholderType.TECHNICAL, ReviewVerdict.PASS),
            _rev(StakeholderType.INVESTOR, ReviewVerdict.FAIL, council_mode=CouncilMode.OUTSIDER.value),
            _rev(StakeholderType.MARKETER, ReviewVerdict.FAIL, council_mode=CouncilMode.OUTSIDER.value),
            _rev(StakeholderType.CREATIVE, ReviewVerdict.PASS, council_mode=CouncilMode.OUTSIDER.value),
        ]
        cr = evaluate_convergence(
            session_id="s",
            round_number=0,
            max_rounds=4,
            artifact_type=ArtifactType.IDEA,
            reviews=reviews,
            council=council,
        )
        self.assertTrue(cr.converged)
        self.assertEqual(cr.final_status, SessionStatus.APPROVED)
        self.assertTrue(any("outsider_fail_count" in x for x in cr.reasons))

    def test_grounded_fail_still_rejects(self) -> None:
        """Outsider PASS cannot lift a grounded FAIL on implementation_plan (technical hard gate)."""
        council = council_for(ArtifactType.IMPLEMENTATION_PLAN)
        reviews = [
            _rev(StakeholderType.TECHNICAL, ReviewVerdict.FAIL, blocking=True),
            _rev(StakeholderType.ARCHITECTURE, ReviewVerdict.PASS),
            _rev(StakeholderType.BONES, ReviewVerdict.PASS),
            _rev(StakeholderType.PRODUCT, ReviewVerdict.PASS),
        ]
        cr = evaluate_convergence(
            session_id="s",
            round_number=0,
            max_rounds=4,
            artifact_type=ArtifactType.IMPLEMENTATION_PLAN,
            reviews=reviews,
            council=council,
        )
        self.assertTrue(cr.converged)
        self.assertEqual(cr.final_status, SessionStatus.REJECTED)


class TestSynthesis(unittest.TestCase):
    def test_theme_items_tag_source(self) -> None:
        reviews = [
            _rev(StakeholderType.PRODUCT, ReviewVerdict.CONCERN),
            _rev(StakeholderType.INVESTOR, ReviewVerdict.CONCERN, council_mode=CouncilMode.OUTSIDER.value),
        ]
        reviews[0].objections.append("grounded issue")
        reviews[1].objections.append("outsider issue")
        syn = synthesize_reviews("s", "d", 0, reviews)
        self.assertTrue(any("grounded" in str(x.get("source", "")) for x in syn.theme_items))
        self.assertTrue(any("outsider" in str(x.get("source", "")) for x in syn.theme_items))


class TestDoctorCouncil(unittest.TestCase):
    def test_check_profiles_no_errors(self) -> None:
        from argus.council.doctor import check_council_profiles

        err, warn = check_council_profiles()
        self.assertFalse(err)


class TestSessionMeta(unittest.TestCase):
    def test_create_session_has_council_profiles(self) -> None:
        from argus.refinement.session import create_session

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _minimal_product(root, "p1")
            (root / "runs" / "refinement").mkdir(parents=True)
            (root / "runs" / "strategy").mkdir(parents=True)
            (root / "runs" / "strategy" / "current.json").write_text('{"schema":"argus.strategy.v1"}', encoding="utf-8")
            sess = create_session(root, ArtifactType.IDEA, "src1", product_id="p1")
            meta = sess.meta
            self.assertIn("council_profiles", meta)
            self.assertGreaterEqual(len(meta["council_profiles"]), 4)


class TestMalformedProfile(unittest.TestCase):
    def test_validate_empty_members(self) -> None:
        p = CouncilProfile(
            artifact_type=ArtifactType.IDEA,
            phase="review",
            members=(),
        )
        self.assertTrue(any("no members" in x for x in validate_council_profile(p)))


class TestReviewDimensions(unittest.TestCase):
    def test_dimension_enum_values(self) -> None:
        self.assertEqual(ReviewDimension.GROUNDED_FEASIBILITY.value, "grounded_feasibility")
