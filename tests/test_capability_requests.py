"""Capability request store and rules."""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from argus.capabilities.requests.integrations import (
    record_advisor_llm_gap,
    record_autonomy_policy_block,
    record_execution_blocked,
    record_experiment_evaluation_gap,
)
from argus.capabilities.requests.models import (
    CapabilityRequestSource,
    CapabilityRequestStatus,
)
from argus.capabilities.requests.store import (
    create_request,
    list_requests,
    load_request,
    update_request_status,
)
from argus.experiments.models import EvaluationVerdict, ExperimentEvaluation


class TestCapabilityRequestStore(unittest.TestCase):
    def test_create_list_update(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            r = create_request(
                root,
                title="Need X",
                description="Details",
                source=CapabilityRequestSource.MANUAL,
                capability_hint="test.x",
            )
            self.assertTrue(r.request_id.startswith("creq_"))
            loaded = load_request(root, r.request_id)
            assert loaded is not None
            self.assertEqual(loaded.status, CapabilityRequestStatus.OPEN)
            upd = update_request_status(
                root,
                r.request_id,
                status=CapabilityRequestStatus.FULFILLED,
                resolution_note="done",
            )
            self.assertEqual(upd.status, CapabilityRequestStatus.FULFILLED)
            rows = list_requests(root, status=CapabilityRequestStatus.FULFILLED)
            self.assertEqual(len(rows), 1)


class TestIntegrations(unittest.TestCase):
    def test_record_autonomy_policy_block(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            rec = record_autonomy_policy_block(
                root,
                ["autonomy mode is OFF — execution is disabled"],
                action_id="a1",
                product_id="p1",
                action_path="/tmp/x.yaml",
            )
            self.assertEqual(rec.source, CapabilityRequestSource.AUTONOMY)
            self.assertEqual(rec.capability_hint, "autonomy.policy")

    def test_record_execution_blocked(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            rec = record_execution_blocked(
                root,
                ["sandbox: blocked"],
                action_path="/tmp/a.yaml",
                action_id="x",
                product_id="p",
            )
            self.assertEqual(rec.source, CapabilityRequestSource.EXECUTION)
            self.assertIn("sandbox", rec.description)

    def test_record_experiment_gap(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            ev = ExperimentEvaluation(
                experiment_id="exp_1",
                product_id="p",
                verdict=EvaluationVerdict.INCONCLUSIVE,
                composite_score=0.0,
                summary="not enough data",
                evaluated_at_utc="2026-01-01T00:00:00+00:00",
            )
            rec = record_experiment_evaluation_gap(root, ev)
            assert rec is not None
            self.assertEqual(rec.source, CapabilityRequestSource.EXPERIMENT)
            rec2 = record_experiment_evaluation_gap(root, ev)
            self.assertIsNone(rec2)

    def test_advisor_gap_skips_when_key_present(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch.dict(
                "os.environ",
                {"ARGUS_OPENAI_API_KEY": "sk-test-fake", "ARGUS_LLM_ENABLED": "1"},
            ):
                out = record_advisor_llm_gap(root, "p1", stub_only=False, use_llm=None)
            self.assertIsNone(out)

    def test_advisor_gap_when_no_key(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = {"ARGUS_OPENAI_API_KEY": ""}
            with patch.dict("os.environ", env, clear=False):
                rec = record_advisor_llm_gap(root, "p1", stub_only=False, use_llm=None)
            assert rec is not None
            self.assertEqual(rec.capability_hint, "advisors.llm")
            with patch.dict("os.environ", env, clear=False):
                rec2 = record_advisor_llm_gap(root, "p1", stub_only=False, use_llm=None)
            self.assertIsNone(rec2)


if __name__ == "__main__":
    unittest.main()
