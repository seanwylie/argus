"""Path construction and session predicates for ``argus.refinement.queries`` (layout single source of truth)."""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from argus.refinement.models import RefinementSessionSnap, SessionStatus
from argus.refinement.persistence import read_json, write_json
from argus.refinement.queries import (
    draft_path,
    refinement_cycle_incomplete,
    refinement_reviews_in_missing,
    reviews_in_round_path,
    reviews_path,
    session_json_path,
    synthesis_round_path,
)


class TestRefinementQueries(unittest.TestCase):
    def test_round_paths_under_session_dir(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            sid = "ref_20260101T000000Z_abcd1234"
            self.assertEqual(
                draft_path(root, sid, 2),
                root / "runs" / "refinement" / sid / "drafts" / "round_2.json",
            )
            self.assertEqual(
                reviews_path(root, sid, 2),
                root / "runs" / "refinement" / sid / "reviews" / "round_2.json",
            )
            self.assertEqual(
                reviews_in_round_path(root, sid, 2),
                root / "runs" / "refinement" / sid / "reviews_in" / "round_2.json",
            )
            self.assertEqual(
                synthesis_round_path(root, sid, 2),
                root / "runs" / "refinement" / sid / "synthesis" / "round_2.json",
            )
            self.assertEqual(
                session_json_path(root, sid),
                root / "runs" / "refinement" / sid / "session.json",
            )

    def test_refinement_reviews_in_missing_matches_files(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            sid = "ref_20260101T000000Z_abcd1234"
            draft = draft_path(root, sid, 0)
            draft.parent.mkdir(parents=True, exist_ok=True)
            write_json(draft, {"schema": "argus.artifact_draft.v1", "draft_id": "x", "round_number": 0})
            sess = RefinementSessionSnap(
                session_id=sid,
                artifact_type="idea",
                product_id="p1",
                status=SessionStatus.IN_REVIEW.value,
                current_round=0,
                max_rounds=4,
                updated_at_utc="2026-01-01T00:00:00+00:00",
            )
            self.assertTrue(refinement_reviews_in_missing(root, sess))
            write_json(reviews_in_round_path(root, sid, 0), {"product": {"verdict": "pass"}})
            self.assertFalse(refinement_reviews_in_missing(root, sess))

    def test_refinement_cycle_incomplete_matches_files(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            sid = "ref_20260101T000000Z_abcd1234"
            draft = draft_path(root, sid, 1)
            draft.parent.mkdir(parents=True, exist_ok=True)
            write_json(draft, {"schema": "argus.artifact_draft.v1", "draft_id": "x", "round_number": 1})
            sess = RefinementSessionSnap(
                session_id=sid,
                artifact_type="idea",
                product_id="p1",
                status=SessionStatus.REFINING.value,
                current_round=1,
                max_rounds=4,
                updated_at_utc="2026-01-01T00:00:00+00:00",
            )
            self.assertTrue(refinement_cycle_incomplete(root, sess))
            write_json(reviews_path(root, sid, 1), {"reviews": []})
            self.assertFalse(refinement_cycle_incomplete(root, sess))

    def test_session_json_path_roundtrip(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            sid = "ref_20260101T000000Z_abcd1234"
            p = session_json_path(root, sid)
            p.parent.mkdir(parents=True, exist_ok=True)
            write_json(p, {"session_id": sid, "artifact_type": "idea", "status": "draft"})
            raw = read_json(p)
            self.assertEqual(raw.get("session_id"), sid)


if __name__ == "__main__":
    unittest.main()
