"""Bounded orchestration progression loop."""

from __future__ import annotations

import json
import shutil
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from unittest.mock import patch

import argus.orchestrator.advancement as orchestrator_advancement
import argus.orchestrator.progression as orchestrator_progression
import argus.orchestrator.state_pass as orchestrator_state_pass
from argus.core.serialize import dumps_json
from argus.orchestrator.eligibility import evaluate_product_orchestration
from argus.orchestrator.progression import (
    _should_stop_before_advance,
    _state_progress_fingerprint,
    run_orchestration_progression,
)
from argus.orchestrator.state_models import (
    ACTION_DECISIONS_GENERATE,
    ACTION_ESCALATION_PACKET_GENERATE,
    ACTION_EXPERIMENTS_ACTIVATE,
    ACTION_EXPERIMENTS_CLOSE_STALE,
    ACTION_EXPERIMENTS_CREATE,
    ACTION_EXPERIMENTS_EVALUATE,
    ACTION_EXPERIMENTS_PRIORITIZE,
    ACTION_EXPERIMENTS_PROPOSE,
    ACTION_FINDINGS_GENERATE,
    ACTION_IDEAS_GENERATE,
    ACTION_REFINEMENT_RUN,
    ACTION_REFINEMENT_START_IDEA,
    ACTION_REFINEMENT_SUBMIT_REVIEWS_IN,
    ACTION_STATUS_EXECUTED,
    ACTION_STATUS_QUEUED,
    ACTION_STATUS_QUEUED_UNHANDLED,
    ORCH_STATUS_BLOCKED_WAITING_INPUT,
    ORCH_STATUS_ELIGIBLE,
    ORCH_STATUS_STALE_REFRESH_NEEDED,
    ORCHESTRATION_PROGRESSION_RUN_ARTIFACT_SCHEMA,
    ORCHESTRATION_PROGRESSION_RUN_SCHEMA,
    ORCHESTRATION_STATE_SCHEMA,
)
from tests.test_orchestration_state import _write
from tests.test_orchestration_step_executor import _minimal_product


def _seed_idea_refinement_in_review_missing_reviews_in(root: Path, product_id: str) -> str:
    """After ``refinement_start_idea``, force CURSOR-shaped wait: draft + in_review, no reviews_in/reviews.

    Eligibility uses ``session.current_round`` for draft/reviews/reviews_in paths. If r1 ran
    ``refinement_run``, ``current_round`` may be >0 while we only seed ``drafts/round_0.json`` —
    that mismatch makes ``refinement_reviews_in_missing`` / cycle-incomplete blockers disappear
    and soft planning picks other actions (flake). We therefore align ``current_round`` to 0
    with the round-0 draft and strip stray per-round artifacts.
    """
    ref = root / "runs" / "refinement"
    idea_rows: list[tuple[str, dict[str, Any]]] = []
    for p in sorted(ref.glob("ref_*/session.json")):
        raw = json.loads(p.read_text(encoding="utf-8"))
        if raw.get("artifact_type") == "idea" and raw.get("product_id") == product_id:
            idea_rows.append((str(raw["session_id"]), raw))
    if not idea_rows:
        raise AssertionError("expected an idea refinement session under runs/refinement/")
    terminal = frozenset({"approved", "approved_with_risks", "rejected"})

    def _is_terminal(status: object) -> bool:
        return str(status or "").strip().lower() in terminal

    non_terminal = [(sid, r) for sid, r in idea_rows if not _is_terminal(r.get("status"))]
    pick_from = non_terminal if non_terminal else idea_rows
    pick_from.sort(key=lambda t: t[0])
    sid = pick_from[0][0]
    sd = ref / sid
    (sd / "drafts").mkdir(parents=True, exist_ok=True)
    (sd / "reviews").mkdir(parents=True, exist_ok=True)
    (sd / "reviews_in").mkdir(parents=True, exist_ok=True)
    # Drop stale round files so predicates see a single round-0 corridor.
    for sub, pattern in (
        ("drafts", "round_*.json"),
        ("reviews", "round_*.json"),
        ("reviews_in", "round_*.json"),
    ):
        d = sd / sub
        if d.is_dir():
            for fp in d.glob(pattern):
                if fp.is_file():
                    fp.unlink()
    sj = json.loads((sd / "session.json").read_text(encoding="utf-8"))
    sj["status"] = "in_review"
    sj["current_round"] = 0
    sj["updated_at_utc"] = "2026-04-12T12:00:00+00:00"
    (sd / "session.json").write_text(dumps_json(sj) + "\n", encoding="utf-8")
    idx_path = ref / "index.json"
    idx = json.loads(idx_path.read_text(encoding="utf-8"))
    for row in idx.get("sessions", []):
        if row.get("session_id") == sid:
            row["status"] = "in_review"
            row["current_round"] = 0
            row["updated_at_utc"] = "2026-04-12T12:00:00+00:00"
    idx_path.write_text(dumps_json(idx) + "\n", encoding="utf-8")
    draft_body = {
        "schema": "argus.artifact_draft.v1",
        "draft_id": f"{sid}_r0_draft",
        "session_id": sid,
        "round_number": 0,
        "artifact_type": "idea",
        "title": "Corridor seed",
        "content": "Minimal draft for deterministic refinement cycle.",
        "structured_fields": {},
        "created_at_utc": "2026-04-12T12:00:00+00:00",
        "generated_by": "deterministic",
    }
    (sd / "drafts" / "round_0.json").write_text(dumps_json(draft_body) + "\n", encoding="utf-8")
    # Progression may create multiple idea refinement sessions; eligibility walks all of them.
    # Remove other idea sessions for this product so the corridor targets exactly ``sid``.
    removed_sids: list[str] = []
    for p in sorted(ref.glob("ref_*")):
        if p.name == sid:
            continue
        sp = p / "session.json"
        if not sp.is_file():
            continue
        try:
            other = json.loads(sp.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if other.get("artifact_type") == "idea" and other.get("product_id") == product_id:
            removed_sids.append(p.name)
            shutil.rmtree(p, ignore_errors=True)
    idx_path = ref / "index.json"
    idx = json.loads(idx_path.read_text(encoding="utf-8"))
    if removed_sids:
        rem = set(removed_sids)
        idx["sessions"] = [row for row in (idx.get("sessions") or []) if row.get("session_id") not in rem]
    # Index can still list another idea session id for this product (stale row). Drop non-``sid`` idea rows.
    kept: list[dict[str, Any]] = []
    for row in idx.get("sessions") or []:
        rid = str(row.get("session_id") or "").strip()
        if rid == sid:
            kept.append(row)
            continue
        sp = ref / rid / "session.json"
        if not sp.is_file():
            continue
        try:
            o = json.loads(sp.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            kept.append(row)
            continue
        if o.get("product_id") == product_id and o.get("artifact_type") == "idea":
            continue
        kept.append(row)
    idx["sessions"] = kept
    idx_path.write_text(dumps_json(idx) + "\n", encoding="utf-8")
    return sid


class TestOrchestrationProgression(unittest.TestCase):
    def test_fingerprint_stable_ignores_evaluated_at(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            a = evaluate_product_orchestration(root, "x")
            b = dict(a)
            b["evaluated_at_utc"] = "2099-01-01T00:00:00+00:00"
            self.assertEqual(_state_progress_fingerprint(a), _state_progress_fingerprint(b))

    def test_stop_before_advance_blocked_waiting_input(self) -> None:
        st, r = _should_stop_before_advance(
            {"orchestration_status": ORCH_STATUS_BLOCKED_WAITING_INPUT, "eligible_actions": [{}]}
        )
        self.assertTrue(st)
        self.assertEqual(r, "blocked_waiting_input")

    def test_no_stop_when_stale_but_eligible(self) -> None:
        st, r = _should_stop_before_advance(
            {
                "orchestration_status": ORCH_STATUS_STALE_REFRESH_NEEDED,
                "eligible_actions": [{"action_id": "signals_collect", "reason": "x", "reason_codes": []}],
                "next_action": "signals_collect",
            }
        )
        self.assertFalse(st)
        self.assertEqual(r, "")

    def test_progression_empty_artifacts_one_advance_then_unchanged(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p1"
            r = run_orchestration_progression(root, pid, max_steps=8, execute=False)
            self.assertEqual(r.stopped_reason, "state_unchanged_after_advance")
            self.assertEqual(r.steps_executed, 1)
            self.assertEqual(len(r.advancement_steps), 1)
            self.assertEqual(r.final_state.get("schema"), ORCHESTRATION_STATE_SCHEMA)
            adv = r.advancement_steps[0]["advancement"]
            self.assertEqual(adv.get("action_status"), ACTION_STATUS_QUEUED)
            j = r.to_jsonable()
            self.assertEqual(j["schema"], ORCHESTRATION_PROGRESSION_RUN_SCHEMA)
            self.assertEqual(j["step_count"], 1)
            self.assertEqual(len(j["actions_taken"]), 1)

    def test_progression_blocked_waiting_zero_advance(self) -> None:
        """Waiting-input posture still allows advancing ``refinement_submit_reviews_in`` (handled action)."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p1"
            _minimal_product(root, pid)
            sid = "ref_20260101T000000Z_abcd1234"
            ref = root / "runs" / "refinement"
            _write(
                ref / "index.json",
                {
                    "schema": "argus.refinement_index.v1",
                    "sessions": [
                        {
                            "session_id": sid,
                            "artifact_type": "product_spec",
                            "product_id": pid,
                            "status": "in_review",
                            "current_round": 0,
                            "updated_at_utc": "2026-01-01T00:00:00+00:00",
                        },
                    ],
                },
            )
            sd = ref / sid
            _write(
                sd / "session.json",
                {
                    "schema": "argus.refinement_session.v1",
                    "session_id": sid,
                    "artifact_type": "product_spec",
                    "source_id": "idea1",
                    "product_id": pid,
                    "current_round": 0,
                    "max_rounds": 4,
                    "status": "in_review",
                    "required_stakeholders": ["product"],
                    "optional_stakeholders": [],
                    "created_at_utc": "2026-01-01T00:00:00+00:00",
                    "updated_at_utc": "2026-01-01T00:00:00+00:00",
                    "meta": {},
                },
            )
            _write(sd / "drafts" / "round_0.json", {"schema": "argus.artifact_draft.v1", "round_number": 0})
            r = run_orchestration_progression(root, pid, max_steps=1)
            self.assertEqual(r.steps_executed, 1)
            self.assertEqual(r.actions_taken[0].get("selected_action"), ACTION_REFINEMENT_SUBMIT_REVIEWS_IN)
            self.assertEqual(r.actions_taken[0].get("action_status"), ACTION_STATUS_EXECUTED)

    def test_max_steps_zero(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            r = run_orchestration_progression(root, "p1", max_steps=0)
            self.assertEqual(r.stopped_reason, "max_steps_zero")
            self.assertEqual(r.steps_executed, 0)
            self.assertEqual(r.final_state.get("schema"), ORCHESTRATION_STATE_SCHEMA)

    def test_progression_artifact_shape_and_paths(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p1"
            r = run_orchestration_progression(root, pid, max_steps=0)
            self.assertEqual(len(r.artifact_paths), 2)
            latest_rel, gen_rel = r.artifact_paths
            self.assertIn("runs/orchestration/latest/progression_runs/p1.json", latest_rel.replace("\\", "/"))
            self.assertIn("runs/orchestration/progression_runs/generations/prog_", gen_rel.replace("\\", "/"))
            latest = root / latest_rel
            gen = root / gen_rel
            self.assertTrue(latest.is_file())
            self.assertTrue(gen.is_file())
            self.assertEqual(latest.read_text(encoding="utf-8"), gen.read_text(encoding="utf-8"))
            data = json.loads(latest.read_text(encoding="utf-8"))
            self.assertEqual(data["schema"], ORCHESTRATION_PROGRESSION_RUN_ARTIFACT_SCHEMA)
            self.assertEqual(data["product_id"], pid)
            self.assertEqual(data["step_count"], 0)
            self.assertEqual(data["terminal_reason"], "max_steps_zero")
            self.assertIn("started_at_utc", data)
            self.assertIn("completed_at_utc", data)
            self.assertIn("run_id", data)
            self.assertIn("orchestration_fingerprint", data)
            self.assertIn("final_state_summary", data)
            j = r.to_jsonable()
            self.assertEqual(j["schema"], ORCHESTRATION_PROGRESSION_RUN_SCHEMA)
            self.assertEqual(len(j["artifact_paths"]), 2)

    def test_progression_write_artifact_false(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            r = run_orchestration_progression(root, "p1", max_steps=0, write_artifact=False)
            self.assertEqual(r.artifact_paths, [])
            prog_latest = root / "runs" / "orchestration" / "latest" / "progression_runs"
            self.assertFalse(prog_latest.exists())

    def test_progression_artifact_satisfies_proof_run_predicates(self) -> None:
        """Mirror docs/proof-run.md section 5 jq + cmp for progression (locks proof-path contract)."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "proof_product"
            _ = run_orchestration_progression(root, pid, max_steps=0)
            p = root / "runs" / "orchestration" / "latest" / "progression_runs" / f"{pid}.json"
            self.assertTrue(p.is_file())
            data = json.loads(p.read_text(encoding="utf-8"))
            self.assertEqual(data["schema"], ORCHESTRATION_PROGRESSION_RUN_ARTIFACT_SCHEMA)
            self.assertEqual(data["product_id"], pid)
            for key in (
                "run_id",
                "started_at_utc",
                "completed_at_utc",
                "terminal_status",
                "terminal_reason",
                "step_count",
                "actions_taken",
                "orchestration_fingerprint",
                "final_state_summary",
            ):
                self.assertIn(key, data)
            self.assertIsInstance(data["terminal_status"], str)
            self.assertIsInstance(data["terminal_reason"], str)
            self.assertIsInstance(data["actions_taken"], list)
            run_id = data["run_id"]
            self.assertTrue(str(run_id))
            g = root / "runs" / "orchestration" / "progression_runs" / "generations" / f"{run_id}.json"
            self.assertTrue(g.is_file())
            self.assertEqual(p.read_text(encoding="utf-8"), g.read_text(encoding="utf-8"))

    def test_max_steps_reached_when_fp_always_differs(self) -> None:
        """Synthetic: if eligibility fingerprint never stabilizes, cap at max_steps advances."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            base = evaluate_product_orchestration(root, "p1")
            n = [0]

            def fake_eval(_r: Path, _p: str) -> dict:
                n[0] += 1
                out = dict(base)
                out["orchestration_status"] = ORCH_STATUS_ELIGIBLE
                out["eligible_actions"] = [
                    {
                        "action_id": "signals_collect",
                        "reason": "r",
                        "reason_codes": [f"tick_{n[0]}"],
                    },
                ]
                out["next_action"] = "signals_collect"
                return out

            with (
                patch.object(
                    orchestrator_progression,
                    "evaluate_product_orchestration",
                    side_effect=fake_eval,
                ),
                patch.object(
                    orchestrator_advancement,
                    "evaluate_product_orchestration",
                    side_effect=fake_eval,
                ),
                patch.object(
                    orchestrator_state_pass,
                    "evaluate_product_orchestration",
                    side_effect=fake_eval,
                ),
            ):
                r = run_orchestration_progression(root, "p1", max_steps=3, execute=False)
            self.assertEqual(r.stopped_reason, "max_steps_reached")
            self.assertEqual(r.steps_executed, 3)

    def test_progression_execute_multi_step_signals_findings_decisions(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _minimal_product(root, "p1")
            r = run_orchestration_progression(root, "p1", max_steps=8, execute=True)
            self.assertGreaterEqual(r.steps_executed, 3)
            self.assertEqual(len(r.actions_taken), r.steps_executed)
            actions = [row.get("selected_action") for row in r.actions_taken]
            # Default product: in-process chain hits fingerprint fixpoint after collect → findings → decisions.
            self.assertIn("signals_collect", actions)
            self.assertIn(ACTION_FINDINGS_GENERATE, actions)
            self.assertIn(ACTION_DECISIONS_GENERATE, actions)
            i_sig = actions.index("signals_collect")
            i_fin = actions.index(ACTION_FINDINGS_GENERATE)
            i_dec = actions.index(ACTION_DECISIONS_GENERATE)
            self.assertLess(i_sig, i_fin)
            self.assertLess(i_fin, i_dec)
            statuses = [row.get("action_status") for row in r.actions_taken]
            self.assertIn(ACTION_STATUS_EXECUTED, statuses)
            self.assertIn(
                r.stopped_reason,
                (
                    "complete",
                    "queued_unhandled",
                    "state_unchanged_after_advance",
                    "max_steps_reached",
                    "execution_failed",
                ),
            )
            j = r.to_jsonable()
            self.assertTrue(j["terminal_status"])
            self.assertEqual(j["terminal_reason"], r.stopped_reason)

    def test_progression_execute_phase2_chain_includes_escalation_packet_generate(self) -> None:
        """End-to-end Phase-2 chain: collect → findings → decisions → escalation_packet (real executor)."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_epkg_chain"
            _minimal_product(root, pid)
            now_iso = "2026-04-12T12:00:00+00:00"
            _write(
                root / "runs" / "signals" / "latest" / f"{pid}.json",
                {
                    "schema": "argus.signal_collection.v1",
                    "product_id": pid,
                    "collected_at_utc": "2020-01-01T00:00:00+00:00",
                    "records": [],
                },
            )
            _write(
                root / "runs" / "temporal" / "latest" / f"{pid}.json",
                {
                    "schema": "argus.temporal_bundle.v1",
                    "product_id": pid,
                    "repo_root": str(root),
                    "collected_at_utc": now_iso,
                    "source_signal_schema": "argus.signal_collection.v1",
                    "record_count": 0,
                    "worst_freshness_status": "fresh",
                    "signals": [],
                },
            )
            _write(
                root / "runs" / "audit" / pid / "bundle.json",
                {
                    "schema": "argus.audit_bundle.v1",
                    "product_id": pid,
                    "generated_at_utc": now_iso,
                    "angles": {"product_gap": {"angle_status": "stub", "summary_lines": ["stub"]}},
                },
            )
            r = run_orchestration_progression(root, pid, max_steps=30, execute=True)
            actions = [row.get("selected_action") for row in r.actions_taken]
            self.assertIn("signals_collect", actions)
            self.assertIn(ACTION_FINDINGS_GENERATE, actions)
            self.assertIn(ACTION_DECISIONS_GENERATE, actions)
            self.assertIn(ACTION_IDEAS_GENERATE, actions)
            self.assertIn(ACTION_EXPERIMENTS_PROPOSE, actions)
            self.assertIn(ACTION_EXPERIMENTS_PRIORITIZE, actions)
            self.assertIn(ACTION_REFINEMENT_START_IDEA, actions)
            self.assertIn(ACTION_ESCALATION_PACKET_GENERATE, actions)
            i_sig = actions.index("signals_collect")
            i_fin = actions.index(ACTION_FINDINGS_GENERATE)
            i_dec = actions.index(ACTION_DECISIONS_GENERATE)
            i_ideas = actions.index(ACTION_IDEAS_GENERATE)
            i_exp = actions.index(ACTION_EXPERIMENTS_PROPOSE)
            i_pri = actions.index(ACTION_EXPERIMENTS_PRIORITIZE)
            i_rsi = actions.index(ACTION_REFINEMENT_START_IDEA)
            i_ep = actions.index(ACTION_ESCALATION_PACKET_GENERATE)
            self.assertLess(i_sig, i_fin)
            self.assertLess(i_fin, i_dec)
            self.assertLess(i_dec, i_ideas)
            self.assertLess(i_ideas, i_exp)
            self.assertLess(i_exp, i_pri)
            if ACTION_EXPERIMENTS_CREATE in actions:
                i_cre = actions.index(ACTION_EXPERIMENTS_CREATE)
                self.assertLess(i_pri, i_cre)
            i_act = (
                actions.index(ACTION_EXPERIMENTS_ACTIVATE)
                if ACTION_EXPERIMENTS_ACTIVATE in actions
                else None
            )
            if i_act is not None:
                if ACTION_EXPERIMENTS_CREATE in actions:
                    self.assertLess(i_cre, i_act)
                else:
                    self.assertLess(i_pri, i_act)
            i_close = (
                actions.index(ACTION_EXPERIMENTS_CLOSE_STALE)
                if ACTION_EXPERIMENTS_CLOSE_STALE in actions
                else None
            )
            if ACTION_EXPERIMENTS_EVALUATE in actions:
                i_ev = actions.index(ACTION_EXPERIMENTS_EVALUATE)
                if i_act is not None:
                    self.assertLess(i_act, i_ev)
                elif ACTION_EXPERIMENTS_CREATE in actions:
                    self.assertLess(i_cre, i_ev)
                else:
                    self.assertLess(i_pri, i_ev)
                if i_close is not None:
                    self.assertLess(i_ev, i_close)
                    self.assertLess(i_close, i_rsi)
                else:
                    self.assertLess(i_ev, i_rsi)
            elif ACTION_EXPERIMENTS_CREATE in actions:
                if i_act is not None:
                    self.assertLess(i_act, i_rsi)
                else:
                    self.assertLess(i_cre, i_rsi)
            elif i_act is not None:
                self.assertLess(i_act, i_rsi)
            else:
                self.assertLess(i_pri, i_rsi)
            self.assertLess(i_rsi, i_ep)
            self.assertEqual(r.actions_taken[i_ep].get("action_status"), ACTION_STATUS_EXECUTED)

    def test_progression_experiments_propose_ordering_after_decisions_and_ideas(self) -> None:
        """Proof: when decisions, ideas, and experiments are all executed, order is decisions → ideas → experiments."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_exp_order_proof"
            _minimal_product(root, pid)
            now_iso = "2026-04-12T12:00:00+00:00"
            _write(
                root / "runs" / "signals" / "latest" / f"{pid}.json",
                {
                    "schema": "argus.signal_collection.v1",
                    "product_id": pid,
                    "collected_at_utc": "2020-01-01T00:00:00+00:00",
                    "records": [],
                },
            )
            _write(
                root / "runs" / "temporal" / "latest" / f"{pid}.json",
                {
                    "schema": "argus.temporal_bundle.v1",
                    "product_id": pid,
                    "repo_root": str(root),
                    "collected_at_utc": now_iso,
                    "source_signal_schema": "argus.signal_collection.v1",
                    "record_count": 0,
                    "worst_freshness_status": "fresh",
                    "signals": [],
                },
            )
            _write(
                root / "runs" / "audit" / pid / "bundle.json",
                {
                    "schema": "argus.audit_bundle.v1",
                    "product_id": pid,
                    "generated_at_utc": now_iso,
                    "angles": {"product_gap": {"angle_status": "stub", "summary_lines": ["stub"]}},
                },
            )
            r = run_orchestration_progression(root, pid, max_steps=12, execute=True)
            actions = [row.get("selected_action") for row in r.actions_taken]
            self.assertIn(ACTION_DECISIONS_GENERATE, actions)
            self.assertIn(ACTION_IDEAS_GENERATE, actions)
            self.assertIn(ACTION_EXPERIMENTS_PROPOSE, actions)
            self.assertIn(ACTION_EXPERIMENTS_PRIORITIZE, actions)
            i_dec = actions.index(ACTION_DECISIONS_GENERATE)
            i_ideas = actions.index(ACTION_IDEAS_GENERATE)
            i_exp = actions.index(ACTION_EXPERIMENTS_PROPOSE)
            i_pri = actions.index(ACTION_EXPERIMENTS_PRIORITIZE)
            self.assertLess(i_dec, i_ideas)
            self.assertLess(i_ideas, i_exp)
            self.assertLess(i_exp, i_pri)
            self.assertLess(i_dec, i_exp)

    def test_progression_idea_refinement_corridor_submit_reviews_in_then_run(self) -> None:
        """Corridor: ideas_generate → refinement_start_idea, then reviews_in wait → submit → refinement_run."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_idea_corridor"
            _minimal_product(root, pid)
            now_iso = "2026-04-12T12:00:00+00:00"
            _write(
                root / "runs" / "signals" / "latest" / f"{pid}.json",
                {
                    "schema": "argus.signal_collection.v1",
                    "product_id": pid,
                    "collected_at_utc": "2020-01-01T00:00:00+00:00",
                    "records": [],
                },
            )
            _write(
                root / "runs" / "temporal" / "latest" / f"{pid}.json",
                {
                    "schema": "argus.temporal_bundle.v1",
                    "product_id": pid,
                    "repo_root": str(root),
                    "collected_at_utc": now_iso,
                    "source_signal_schema": "argus.signal_collection.v1",
                    "record_count": 0,
                    "worst_freshness_status": "fresh",
                    "signals": [],
                },
            )
            _write(
                root / "runs" / "audit" / pid / "bundle.json",
                {
                    "schema": "argus.audit_bundle.v1",
                    "product_id": pid,
                    "generated_at_utc": now_iso,
                    "angles": {"product_gap": {"angle_status": "stub", "summary_lines": ["stub"]}},
                },
            )
            r1 = run_orchestration_progression(root, pid, max_steps=30, execute=True)
            actions1 = [row.get("selected_action") for row in r1.actions_taken]
            self.assertIn(ACTION_IDEAS_GENERATE, actions1)
            self.assertIn(ACTION_EXPERIMENTS_PROPOSE, actions1)
            self.assertIn(ACTION_EXPERIMENTS_PRIORITIZE, actions1)
            self.assertIn(ACTION_REFINEMENT_START_IDEA, actions1)
            self.assertLess(actions1.index(ACTION_IDEAS_GENERATE), actions1.index(ACTION_EXPERIMENTS_PROPOSE))
            self.assertLess(actions1.index(ACTION_EXPERIMENTS_PROPOSE), actions1.index(ACTION_EXPERIMENTS_PRIORITIZE))
            self.assertLess(actions1.index(ACTION_EXPERIMENTS_PRIORITIZE), actions1.index(ACTION_REFINEMENT_START_IDEA))

            sid = _seed_idea_refinement_in_review_missing_reviews_in(root, pid)

            # Single corridor: one idea session dir for this product (seed prunes duplicates).
            ref = root / "runs" / "refinement"
            idea_dirs: list[str] = []
            for p in sorted(ref.glob("ref_*")):
                sp = p / "session.json"
                if not sp.is_file():
                    continue
                raw = json.loads(sp.read_text(encoding="utf-8"))
                if raw.get("artifact_type") == "idea" and raw.get("product_id") == pid:
                    idea_dirs.append(p.name)
            self.assertEqual(idea_dirs, [sid], msg=f"expected exactly one idea session {sid!r}, got {idea_dirs}")

            st_post_seed = evaluate_product_orchestration(root, pid)
            self.assertTrue(
                (st_post_seed.get("eligibility_facts") or {}).get("refinement_submit_reviews_in_eligible"),
                msg=(
                    "post-seed must yield refinement_submit_reviews_in eligible "
                    f"(next_action={st_post_seed.get('next_action')!r}, "
                    f"orchestration_status={st_post_seed.get('orchestration_status')!r})"
                ),
            )
            self.assertEqual(st_post_seed.get("next_action"), ACTION_REFINEMENT_SUBMIT_REVIEWS_IN)
            self.assertEqual(st_post_seed.get("orchestration_status"), ORCH_STATUS_BLOCKED_WAITING_INPUT)

            r2 = run_orchestration_progression(root, pid, max_steps=20, execute=True)

            trace = [row.get("selected_action") for row in r1.actions_taken + r2.actions_taken]
            corridor = [
                ACTION_IDEAS_GENERATE,
                ACTION_EXPERIMENTS_PROPOSE,
                ACTION_EXPERIMENTS_PRIORITIZE,
                ACTION_REFINEMENT_START_IDEA,
                ACTION_REFINEMENT_SUBMIT_REVIEWS_IN,
                ACTION_REFINEMENT_RUN,
            ]
            for aid in corridor:
                self.assertIn(aid, trace)
            i_ideas = trace.index(ACTION_IDEAS_GENERATE)
            i_exp = trace.index(ACTION_EXPERIMENTS_PROPOSE)
            i_pri = trace.index(ACTION_EXPERIMENTS_PRIORITIZE)
            i_rsi = trace.index(ACTION_REFINEMENT_START_IDEA)
            self.assertLess(i_ideas, i_exp)
            self.assertLess(i_exp, i_pri)
            self.assertLess(i_pri, i_rsi)
            self.assertEqual(r2.actions_taken[0].get("selected_action"), ACTION_REFINEMENT_SUBMIT_REVIEWS_IN)
            self.assertEqual(r2.actions_taken[0].get("action_status"), ACTION_STATUS_EXECUTED)
            r2_actions = [row.get("selected_action") for row in r2.actions_taken]
            if ACTION_REFINEMENT_RUN in r2_actions:
                self.assertLess(
                    r2_actions.index(ACTION_REFINEMENT_SUBMIT_REVIEWS_IN),
                    r2_actions.index(ACTION_REFINEMENT_RUN),
                )

    def test_progression_stops_on_queued_unhandled_with_patch(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _minimal_product(root, "p1")
            with patch(
                "argus.orchestrator.step_executor.execute_orchestration_action",
                return_value={
                    "action_status": ACTION_STATUS_QUEUED_UNHANDLED,
                    "execution_detail": {"reason": "stub"},
                    "execution_error": None,
                },
            ):
                r = run_orchestration_progression(root, "p1", max_steps=5, execute=True)
            self.assertEqual(r.stopped_reason, "queued_unhandled")
            self.assertEqual(r.steps_executed, 1)
            self.assertEqual(r.terminal_reason, "queued_unhandled")


if __name__ == "__main__":
    unittest.main()
