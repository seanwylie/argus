"""Strategy skepticism: double_down requires non-synthetic canonical corroboration."""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from argus.core.models.enums import EffortBucket, FindingKind, SeverityLevel
from argus.core.models.finding import Finding
from argus.core.serialize import dumps_json
from argus.findings.experiment_surfaced import EXPERIMENT_SURFACED_SCHEMA
from argus.findings.persistence import save_findings_bundle
from argus.strategy.snapshot import build_strategy_snapshot
from tests.test_strategy_posture_dampening import _write_latest, _write_strategy_gen
from tests.test_strategy_refresh_orchestration import _decisions_bundle


def _experiment_surfaced(root: Path, pid: str, *, n: int = 1) -> None:
    sp = root / "runs" / "findings" / "experiment_surfaced" / "latest" / f"{pid}.json"
    sp.parent.mkdir(parents=True, exist_ok=True)
    sp.write_text(
        dumps_json(
            {
                "schema": EXPERIMENT_SURFACED_SCHEMA,
                "product_id": pid,
                "generated_at_utc": "2026-04-12T12:00:00+00:00",
                "findings": [{"id": f"s{i}"} for i in range(n)],
            }
        )
        + "\n",
        encoding="utf-8",
    )


def _double_down_fixture_decisions(pid: str, root: Path) -> None:
    """Evolution + candidates that yield raw double_down before skepticism."""
    p = root / "runs" / "decisions" / "latest" / f"{pid}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        dumps_json(
            _decisions_bundle(
                pid,
                root,
                gen_at="2026-04-12T12:00:00+00:00",
                candidates=[
                    {
                        "id": "a",
                        "action_type": "analyze",
                        "summary": "hello world",
                        "change_type": "new",
                    },
                    {
                        "id": "b",
                        "action_type": "analyze",
                        "summary": "hello world",
                        "change_type": "modified_content",
                    },
                    {"id": "c", "action_type": "stop", "summary": "z", "change_type": "unchanged"},
                ],
                evolution={
                    "new_count": 1,
                    "unchanged_count": 1,
                    "modified_count": 1,
                    "removed_count": 0,
                },
            )
        )
        + "\n",
        encoding="utf-8",
    )


class TestStrategySkepticism(unittest.TestCase):
    def test_surfaced_only_suppresses_double_down_to_explore(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_sk_exp"
            _double_down_fixture_decisions(pid, root)
            _experiment_surfaced(root, pid, n=2)

            s = build_strategy_snapshot(root, pid)
            self.assertEqual(s.get("posture_raw"), "explore")
            self.assertEqual(s.get("posture"), "explore")
            self.assertTrue(s.get("skepticism_applied"))
            self.assertEqual(
                s.get("skepticism_reason"),
                "double_down_suppressed_without_canonical_corroboration",
            )
            themes = s.get("theme_signals") or []
            st = [t for t in themes if t.get("signal") == "strengthening"]
            self.assertTrue(st)
            self.assertEqual(st[0].get("evidence_basis"), "experiment_driven")

    def test_canonical_corroboration_allows_double_down(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_sk_mix"
            _double_down_fixture_decisions(pid, root)
            _experiment_surfaced(root, pid, n=2)
            save_findings_bundle(
                root,
                pid,
                [
                    Finding(
                        id="cf1",
                        product_id=pid,
                        kind=FindingKind.CURRENT_RISK,
                        severity=SeverityLevel.LOW,
                        effort=EffortBucket.SMALL,
                        title="Canon",
                        summary="x",
                        recommendation="y",
                        evidence={"metric": "m"},
                    )
                ],
            )

            s = build_strategy_snapshot(root, pid)
            self.assertEqual(s.get("posture_raw"), "double_down")
            self.assertEqual(s.get("posture"), "double_down")
            self.assertFalse(s.get("skepticism_applied"))
            self.assertIsNone(s.get("skepticism_reason"))
            themes = s.get("theme_signals") or []
            st = [t for t in themes if t.get("signal") == "strengthening"]
            self.assertEqual(st[0].get("evidence_basis"), "mixed")

    def test_no_strengthening_unchanged_without_skepticism(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_sk_stable"
            p = root / "runs" / "decisions" / "latest" / f"{pid}.json"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(
                dumps_json(
                    _decisions_bundle(
                        pid,
                        root,
                        gen_at="2026-04-12T12:00:00+00:00",
                        candidates=[
                            {"id": "a", "action_type": "analyze", "summary": "u", "change_type": "unchanged"},
                            {"id": "b", "action_type": "analyze", "summary": "v", "change_type": "unchanged"},
                        ],
                        evolution={
                            "new_count": 0,
                            "unchanged_count": 2,
                            "modified_count": 0,
                            "removed_count": 0,
                        },
                    )
                )
                + "\n",
                encoding="utf-8",
            )
            _experiment_surfaced(root, pid, n=1)

            s = build_strategy_snapshot(root, pid)
            self.assertEqual(s.get("posture_raw"), "stabilize")
            self.assertFalse(s.get("skepticism_applied"))

    def test_dampening_runs_on_skeptical_raw_posture(self) -> None:
        """After suppression raw is explore; dampening can still hold prior stabilize."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_sk_damp"
            _double_down_fixture_decisions(pid, root)
            _experiment_surfaced(root, pid, n=2)
            _write_latest(root, pid, posture="stabilize")
            _write_strategy_gen(root, pid, ts="20260412T100000Z", posture_raw="stabilize", posture="stabilize")

            s = build_strategy_snapshot(root, pid)
            self.assertEqual(s.get("posture_raw"), "explore")
            self.assertEqual(s.get("posture"), "stabilize")
            self.assertTrue(s.get("posture_changed"))
            self.assertTrue(s.get("skepticism_applied"))

    def test_stabilize_when_raw_double_down_churn_zero_and_low_surfaced(self) -> None:
        """Inconsistent evolution vs change_types: double_down raw, skepticism → stabilize."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_sk_stab"
            p = root / "runs" / "decisions" / "latest" / f"{pid}.json"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(
                dumps_json(
                    _decisions_bundle(
                        pid,
                        root,
                        gen_at="2026-04-12T12:00:00+00:00",
                        candidates=[
                            {
                                "id": "a",
                                "action_type": "analyze",
                                "summary": "theme x",
                                "change_type": "modified_content",
                            },
                            {
                                "id": "b",
                                "action_type": "analyze",
                                "summary": "theme x",
                                "change_type": "unchanged",
                            },
                        ],
                        evolution={
                            "new_count": 0,
                            "unchanged_count": 2,
                            "modified_count": 0,
                            "removed_count": 0,
                        },
                    )
                )
                + "\n",
                encoding="utf-8",
            )
            _experiment_surfaced(root, pid, n=1)

            s = build_strategy_snapshot(root, pid)
            self.assertEqual(s.get("posture_raw"), "stabilize")
            self.assertTrue(s.get("skepticism_applied"))
            self.assertEqual(
                s.get("skepticism_reason"),
                "double_down_suppressed_without_canonical_corroboration_stable",
            )


if __name__ == "__main__":
    unittest.main()
