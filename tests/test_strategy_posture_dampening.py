"""Posture dampening for strategy snapshots (deterministic, inspectable)."""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from argus.core.serialize import dumps_json
from argus.findings.experiment_surfaced import EXPERIMENT_SURFACED_SCHEMA
from argus.strategy.snapshot import (
    STRATEGY_SNAPSHOT_SCHEMA,
    apply_posture_dampening,
    build_strategy_snapshot,
    load_last_posture_raws_from_generations,
    strategy_generations_dir,
    strategy_latest_path,
)
from tests.test_strategy_refresh_orchestration import _decisions_bundle


def _write_strategy_gen(
    root: Path,
    pid: str,
    *,
    ts: str,
    posture_raw: str,
    posture: str | None = None,
) -> Path:
    """Write a generation file (minimal valid snapshot fields)."""
    gdir = strategy_generations_dir(root)
    gdir.mkdir(parents=True, exist_ok=True)
    p = gdir / f"{ts}_{pid}.json"
    body = {
        "schema": STRATEGY_SNAPSHOT_SCHEMA,
        "schema_version": "1",
        "product_id": pid,
        "generated_at_utc": f"{ts[:4]}-{ts[4:6]}-{ts[6:8]}T12:00:00+00:00",
        "source_decisions_generated_at_utc": "2026-04-12T12:00:00+00:00",
        "summary": "x",
        "posture_raw": posture_raw,
        "posture": posture if posture is not None else posture_raw,
        "posture_changed": False,
        "posture_history": [],
        "theme_signals": [],
        "recommended_mode": "x",
        "evidence": {},
    }
    p.write_text(dumps_json(body) + "\n", encoding="utf-8")
    return p


def _write_latest(root: Path, pid: str, *, posture: str) -> None:
    lp = strategy_latest_path(root, pid)
    lp.parent.mkdir(parents=True, exist_ok=True)
    lp.write_text(
        dumps_json(
            {
                "schema": STRATEGY_SNAPSHOT_SCHEMA,
                "schema_version": "1",
                "product_id": pid,
                "generated_at_utc": "2026-04-12T11:00:00+00:00",
                "source_decisions_generated_at_utc": "2026-04-12T12:00:00+00:00",
                "summary": "x",
                "posture_raw": posture,
                "posture": posture,
                "posture_changed": False,
                "posture_history": [],
                "theme_signals": [],
                "recommended_mode": "x",
                "evidence": {},
            }
        )
        + "\n",
        encoding="utf-8",
    )


class TestApplyPostureDampening(unittest.TestCase):
    def test_no_previous_accepts_raw(self) -> None:
        final, _ = apply_posture_dampening("explore", previous_dampened_posture=None, history_raws=[])
        self.assertEqual(final, "explore")

    def test_raw_equals_previous_accepts(self) -> None:
        final, _ = apply_posture_dampening(
            "stabilize", previous_dampened_posture="stabilize", history_raws=["explore", "explore"]
        )
        self.assertEqual(final, "stabilize")

    def test_single_fluctuation_rejected_without_second_match(self) -> None:
        """Window [R] only → one match < 2 → keep previous."""
        final, _ = apply_posture_dampening(
            "explore", previous_dampened_posture="stabilize", history_raws=[]
        )
        self.assertEqual(final, "stabilize")

    def test_one_history_raw_must_match_for_two_slot_window(self) -> None:
        """Window [h0, R] needs two matches with R → requires h0 == R."""
        final, _ = apply_posture_dampening(
            "explore", previous_dampened_posture="stabilize", history_raws=["explore"]
        )
        self.assertEqual(final, "explore")

        final2, _ = apply_posture_dampening(
            "explore", previous_dampened_posture="stabilize", history_raws=["stabilize"]
        )
        self.assertEqual(final2, "stabilize")

    def test_sustained_change_two_of_three(self) -> None:
        final, _ = apply_posture_dampening(
            "explore", previous_dampened_posture="stabilize", history_raws=["explore", "explore"]
        )
        self.assertEqual(final, "explore")

    def test_oscillation_pattern_stabilizes_when_insufficient_matches(self) -> None:
        """[explore, stabilize, explore] → explore appears twice → accept explore (flip)."""
        final, _ = apply_posture_dampening(
            "explore", previous_dampened_posture="stabilize", history_raws=["explore", "stabilize"]
        )
        self.assertEqual(final, "explore")


class TestBuildStrategySnapshotDampening(unittest.TestCase):
    def test_no_history_posture_unchanged_from_raw(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_nd"
            p = root / "runs" / "decisions" / "latest" / f"{pid}.json"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(
                dumps_json(
                    _decisions_bundle(
                        pid,
                        root,
                        gen_at="2026-04-12T12:00:00+00:00",
                        candidates=[
                            {"id": "a", "action_type": "analyze", "summary": "x", "change_type": "unchanged"},
                            {"id": "b", "action_type": "analyze", "summary": "y", "change_type": "unchanged"},
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
            s = build_strategy_snapshot(root, pid)
            self.assertEqual(s.get("posture_raw"), "stabilize")
            self.assertEqual(s.get("posture"), "stabilize")
            self.assertFalse(s.get("posture_changed"))
            self.assertEqual(s.get("posture_history"), [])

    def test_single_raw_flip_suppressed(self) -> None:
        """Prior dampened stabilize; prior gen raw was stabilize; new raw explore once → suppressed."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_sf"
            p = root / "runs" / "decisions" / "latest" / f"{pid}.json"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(
                dumps_json(
                    _decisions_bundle(
                        pid,
                        root,
                        gen_at="2026-04-12T12:00:00+00:00",
                        candidates=[
                            {"id": "a", "action_type": "analyze", "summary": "s", "change_type": "new"},
                            {"id": "b", "action_type": "analyze", "summary": "t", "change_type": "modified_content"},
                        ],
                        evolution={
                            "new_count": 1,
                            "unchanged_count": 0,
                            "modified_count": 1,
                            "removed_count": 0,
                        },
                    )
                )
                + "\n",
                encoding="utf-8",
            )
            sp = root / "runs" / "findings" / "experiment_surfaced" / "latest" / f"{pid}.json"
            sp.parent.mkdir(parents=True, exist_ok=True)
            sp.write_text(
                dumps_json(
                    {
                        "schema": EXPERIMENT_SURFACED_SCHEMA,
                        "product_id": pid,
                        "generated_at_utc": "2026-04-12T12:00:00+00:00",
                        "findings": [{"id": "x"}],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            _write_latest(root, pid, posture="stabilize")
            _write_strategy_gen(root, pid, ts="20260412T100000Z", posture_raw="stabilize", posture="stabilize")

            s = build_strategy_snapshot(root, pid)
            self.assertEqual(s.get("posture_raw"), "explore")
            self.assertEqual(s.get("posture"), "stabilize")
            self.assertTrue(s.get("posture_changed"))
            hist = load_last_posture_raws_from_generations(root, pid)
            self.assertEqual(hist, ["stabilize"])

    def test_sustained_raw_accepted_after_threshold(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_sus"
            p = root / "runs" / "decisions" / "latest" / f"{pid}.json"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(
                dumps_json(
                    _decisions_bundle(
                        pid,
                        root,
                        gen_at="2026-04-12T12:00:00+00:00",
                        candidates=[
                            {"id": "a", "action_type": "analyze", "summary": "s", "change_type": "new"},
                            {"id": "b", "action_type": "analyze", "summary": "t", "change_type": "modified_content"},
                        ],
                        evolution={
                            "new_count": 1,
                            "unchanged_count": 0,
                            "modified_count": 1,
                            "removed_count": 0,
                        },
                    )
                )
                + "\n",
                encoding="utf-8",
            )
            sp = root / "runs" / "findings" / "experiment_surfaced" / "latest" / f"{pid}.json"
            sp.parent.mkdir(parents=True, exist_ok=True)
            sp.write_text(
                dumps_json(
                    {
                        "schema": EXPERIMENT_SURFACED_SCHEMA,
                        "product_id": pid,
                        "generated_at_utc": "2026-04-12T12:00:00+00:00",
                        "findings": [{"id": "x"}],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            _write_latest(root, pid, posture="stabilize")
            _write_strategy_gen(root, pid, ts="20260412T100000Z", posture_raw="explore", posture="stabilize")
            _write_strategy_gen(root, pid, ts="20260412T110000Z", posture_raw="explore", posture="stabilize")

            s = build_strategy_snapshot(root, pid)
            self.assertEqual(s.get("posture_raw"), "explore")
            self.assertEqual(s.get("posture"), "explore")
            self.assertFalse(s.get("posture_changed"))
            self.assertEqual(load_last_posture_raws_from_generations(root, pid), ["explore", "explore"])


if __name__ == "__main__":
    unittest.main()
