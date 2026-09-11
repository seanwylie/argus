"""Decision lineage: matching, change classification, bundle metadata."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from argus.core.models.decision import DecisionCandidate
from argus.core.models.enums import ActionType, LifecycleStage
from argus.core.serialize import dumps_json
from argus.decision.evolution import (
    build_decision_lineage_payload,
    change_summary_from_bundle_extras,
)
from argus.decision.persistence import latest_product_path, save_product_decisions
from argus.lifecycle.model import LifecycleAssessment


def _la(pid: str) -> LifecycleAssessment:
    return LifecycleAssessment(
        product_id=pid,
        stage=LifecycleStage.IDEA,
        move_forward=0.5,
        hold=0.2,
        improve=0.3,
        deprecate=0.1,
        kill=0.05,
    )


def _cand(
    pid: str,
    cid: str,
    *,
    summary: str = "Do the thing",
    confidence: float = 0.7,
    priority_score: float = 0.5,
) -> DecisionCandidate:
    return DecisionCandidate(
        id=cid,
        product_id=pid,
        action_type=ActionType.ANALYZE,
        summary=summary,
        expected_impact="",
        confidence=confidence,
        rationale="because",
        priority_score=priority_score,
    )


def _write_prev_bundle(root: Path, pid: str, cands: list[DecisionCandidate], ts: str) -> None:
    p = latest_product_path(root, pid)
    p.parent.mkdir(parents=True, exist_ok=True)
    from argus.core.serialize import decision_candidate_to_dict

    bundle = {
        "schema": "argus.decisions_bundle.v1",
        "product_id": pid,
        "generated_at_utc": ts,
        "repo_root": str(root),
        "lifecycle": {"product_id": pid, "stage": "idea", "scores": {}, "reasoning": {}, "kill_candidate": False},
        "candidates": [decision_candidate_to_dict(c) for c in cands],
    }
    p.write_text(dumps_json(bundle) + "\n", encoding="utf-8")


class TestDecisionEvolution(unittest.TestCase):
    def test_first_generation_all_new(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_ev0"
            c = _cand(pid, "c1")
            lin = build_decision_lineage_payload(root, pid, [c])
            self.assertEqual(lin["candidate_augmentations"][0]["change_type"], "new")
            ev = lin["bundle_extras"]["decision_evolution"]
            self.assertIsNone(ev["previous_generated_at_utc"])
            self.assertEqual(ev["new_count"], 1)
            self.assertEqual(ev["unchanged_count"], 0)
            self.assertEqual(ev["modified_count"], 0)
            self.assertEqual(ev.get("ranking_modified_count"), 0)
            self.assertEqual(ev["removed_count"], 0)
            self.assertNotIn("previous_decisions_generated_at_utc", lin["bundle_extras"])

    def test_refresh_unchanged_when_identical(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_ev1"
            ts = "2026-04-12T10:00:00+00:00"
            c = _cand(pid, "c1")
            _write_prev_bundle(root, pid, [c], ts)
            lin = build_decision_lineage_payload(root, pid, [_cand(pid, "c1")])
            self.assertEqual(lin["candidate_augmentations"][0]["change_type"], "unchanged")
            self.assertEqual(lin["candidate_augmentations"][0]["matched_previous_decision_id"], "c1")
            ev = lin["bundle_extras"]["decision_evolution"]
            self.assertEqual(ev["unchanged_count"], 1)
            self.assertEqual(ev["new_count"], 0)
            self.assertEqual(ev["modified_count"], 0)
            self.assertEqual(ev.get("ranking_modified_count"), 0)
            self.assertEqual(lin["bundle_extras"]["previous_decisions_generated_at_utc"], ts)
            self.assertIn("runs/decisions/latest", lin["bundle_extras"]["previous_decisions_path"])

    def test_refresh_modified_when_summary_changes(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_ev2"
            _write_prev_bundle(root, pid, [_cand(pid, "c1", summary="Old")], "2026-04-12T10:00:00+00:00")
            lin = build_decision_lineage_payload(root, pid, [_cand(pid, "c1", summary="New")])
            self.assertEqual(lin["candidate_augmentations"][0]["change_type"], "modified_content")
            ev = lin["bundle_extras"]["decision_evolution"]
            self.assertEqual(ev["modified_count"], 1)
            self.assertEqual(ev.get("ranking_modified_count"), 0)
            self.assertEqual(ev["unchanged_count"], 0)

    def test_refresh_unchanged_when_only_priority_score_changes(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_ev_pri"
            ts = "2026-04-12T10:00:00+00:00"
            _write_prev_bundle(root, pid, [_cand(pid, "c1", priority_score=0.1)], ts)
            lin = build_decision_lineage_payload(root, pid, [_cand(pid, "c1", priority_score=0.9)])
            self.assertEqual(lin["candidate_augmentations"][0]["change_type"], "unchanged")
            ev = lin["bundle_extras"]["decision_evolution"]
            self.assertEqual(ev["unchanged_count"], 1)
            self.assertEqual(ev["modified_count"], 0)
            self.assertEqual(ev.get("ranking_modified_count"), 0)

    def test_refresh_modified_ranking_when_only_confidence_changes(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_ev_conf"
            _write_prev_bundle(root, pid, [_cand(pid, "c1", confidence=0.5)], "2026-04-12T10:00:00+00:00")
            lin = build_decision_lineage_payload(root, pid, [_cand(pid, "c1", confidence=0.9)])
            self.assertEqual(lin["candidate_augmentations"][0]["change_type"], "modified_ranking")
            ev = lin["bundle_extras"]["decision_evolution"]
            self.assertEqual(ev["modified_count"], 0)
            self.assertEqual(ev.get("ranking_modified_count"), 1)
            self.assertEqual(ev["unchanged_count"], 0)

    def test_removal_when_previous_had_extra_id(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_ev3"
            _write_prev_bundle(
                root,
                pid,
                [_cand(pid, "stay"), _cand(pid, "gone")],
                "2026-04-12T10:00:00+00:00",
            )
            lin = build_decision_lineage_payload(root, pid, [_cand(pid, "stay")])
            rem = lin["bundle_extras"]["removed_decisions"]
            self.assertEqual(len(rem), 1)
            self.assertEqual(rem[0]["decision_id"], "gone")
            self.assertEqual(rem[0]["reason"], "not_present_in_new_generation")
            self.assertEqual(lin["bundle_extras"]["decision_evolution"]["removed_count"], 1)

    def test_save_product_decisions_merges_lineage(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_ev4"
            a = _la(pid)
            c = _cand(pid, "c1")
            lin = build_decision_lineage_payload(root, pid, [c])
            save_product_decisions(
                root,
                pid,
                a,
                [c],
                lineage_bundle_extras=lin["bundle_extras"],
                lineage_candidate_augmentations=lin["candidate_augmentations"],
            )
            data = json.loads(latest_product_path(root, pid).read_text(encoding="utf-8"))
            self.assertIn("decision_evolution", data)
            self.assertIn("change_type", (data.get("candidates") or [{}])[0])

    def test_change_summary_helper(self) -> None:
        bx = {
            "decision_evolution": {
                "new_count": 1,
                "unchanged_count": 2,
                "modified_count": 0,
                "ranking_modified_count": 1,
                "removed_count": 1,
            }
        }
        cs = change_summary_from_bundle_extras(bx)
        self.assertEqual(cs["new_count"], 1)
        self.assertEqual(cs["removed_count"], 1)
        self.assertEqual(cs.get("ranking_modified_count"), 1)


if __name__ == "__main__":
    unittest.main()
