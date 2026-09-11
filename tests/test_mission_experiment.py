"""Tests for mission read-only experiments (:mod:`argus.mission.experiment`)."""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from argus.mission.experiment import (
    evaluate_mission_experiment,
    parse_mission_composition_experiment_arg,
    run_mission_experiment,
)
from argus.mission.mission import (
    MissionExperimentComposition,
    mission_experiment_scope,
    resolve_product_mission,
)
from argus.policy.operator_policy import load_operator_policy


def _write_minimal_product(root: Path, pid: str, *, mission_id: str) -> None:
    pr = root / "products" / pid
    pr.mkdir(parents=True)
    (pr / "product.yaml").write_text(
        f"""
id: {pid}
name: X
owner:
  team: t
lifecycle:
  stage: idea
mission_id: {mission_id}
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
        encoding="utf-8",
    )
    (pr / "scripts").mkdir(parents=True)
    (pr / "scripts" / "s.sh").write_text("#!/bin/sh\necho\n", encoding="utf-8")


class TestMissionExperiment(unittest.TestCase):
    def test_scope_override_beats_yaml_inside_only(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "config").mkdir(parents=True)
            _write_minimal_product(root, "px", mission_id="education")
            self.assertEqual(
                resolve_product_mission(root, "px")["resolved_mission_id"], "education"
            )
            with mission_experiment_scope(product_mission_overrides={"px": "revenue"}):
                self.assertEqual(
                    resolve_product_mission(root, "px")["resolved_mission_id"], "revenue"
                )
            self.assertEqual(
                resolve_product_mission(root, "px")["resolved_mission_id"], "education"
            )

    def test_targeted_profiles_change_policy_snippet(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "config").mkdir(parents=True)
            _write_minimal_product(root, "p1", mission_id="education")
            pl = evaluate_mission_experiment(
                root,
                ["education", "revenue"],
                product_ids=["p1"],
                mode="targeted",
            )
            self.assertEqual(pl["schema"], "argus.mission_experiment.v2")
            self.assertEqual(pl["experiment_variant_kind"], "profiles")
            self.assertIsNone(pl.get("compared_compositions"))
            self.assertEqual(pl["experiment_mode"], "targeted")
            self.assertEqual(pl["products_evaluated"], ["p1"])
            per = pl["per_profile"]
            self.assertEqual(len(per), 2)
            g0 = per[0]["operator_policy_global"]["confidence_low_threshold"]
            g1 = per[1]["operator_policy_global"]["confidence_low_threshold"]
            self.assertNotEqual(g0, g1)

    def test_multi_product_sweep_mixed(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "config").mkdir(parents=True)
            _write_minimal_product(root, "a", mission_id="revenue")
            _write_minimal_product(root, "b", mission_id="engagement")
            pl = evaluate_mission_experiment(
                root,
                ["education", "revenue"],
                product_ids=["a", "b"],
                mode="sweep_mixed",
            )
            self.assertEqual(pl["experiment_mode"], "sweep_mixed")
            mix0 = pl["per_profile"][0]["mission_mix_used"]["mission_mix_summary"][
                "counts_by_mission_id"
            ]
            self.assertEqual(mix0.get("education"), 2)

    def test_single_profile_lists_no_cross_delta(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "config").mkdir(parents=True)
            _write_minimal_product(root, "solo", mission_id="revenue")
            pl = evaluate_mission_experiment(
                root, ["education"], product_ids=["solo"], mode="targeted"
            )
            self.assertTrue(
                any(
                    "cross_profile" in str(x) for x in pl["areas_no_meaningful_change_vs_reference"]
                )
            )

    def test_no_artifact_writes_when_disabled(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "config").mkdir(parents=True)
            _write_minimal_product(root, "nw", mission_id="revenue")
            before = {p for p in root.rglob("*")}
            pl = run_mission_experiment(
                root,
                ["revenue"],
                product_ids=["nw"],
                mode="targeted",
                write_artifacts=False,
            )
            after = {p for p in root.rglob("*")}
            self.assertEqual(before, after)
            self.assertFalse((root / "runs" / "mission" / "experiments").exists())
            self.assertEqual(pl["read_only"], True)

    def test_duplicate_profiles_no_material_delta(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "config").mkdir(parents=True)
            _write_minimal_product(root, "d", mission_id="revenue")
            pl = evaluate_mission_experiment(
                root,
                ["revenue", "revenue"],
                product_ids=["d"],
                mode="targeted",
            )
            self.assertEqual(len(pl["per_profile"]), 2)
            self.assertFalse(any(d.get("fields_changed") for d in pl["policy_deltas_vs_reference"]))
            self.assertIn(
                "operator_policy_global (vs first profile)",
                pl["areas_no_meaningful_change_vs_reference"],
            )

    def test_operator_policy_cache_separates_experiment_contexts(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "config").mkdir(parents=True)
            _write_minimal_product(root, "c", mission_id="revenue")
            with mission_experiment_scope(product_mission_overrides={"c": "education"}):
                pe = load_operator_policy(root, product_id="c")["confidence"]["low_threshold"]
            pr = load_operator_policy(root, product_id="c")["confidence"]["low_threshold"]
            self.assertNotEqual(pe, pr)

    def test_parse_composition_string(self) -> None:
        c = parse_mission_composition_experiment_arg(
            "objective=revenue drivers=engagement,education guardrails=education"
        )
        self.assertEqual(c.objective, "revenue")
        self.assertEqual(c.drivers, ("engagement", "education"))
        self.assertEqual(c.guardrails, ("education",))

    def test_compositions_differ_from_objective_only(self) -> None:
        """Driver/guardrail overlays change policy vs objective-only structured mission."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "config").mkdir(parents=True)
            _write_minimal_product(root, "p1", mission_id="education")
            pl = evaluate_mission_experiment(
                root,
                compared_compositions=[
                    MissionExperimentComposition("revenue", (), ()),
                    MissionExperimentComposition("revenue", ("engagement",), ("education",)),
                ],
                product_ids=["p1"],
                mode="targeted",
            )
            self.assertEqual(pl["experiment_variant_kind"], "compositions")
            self.assertEqual(len(pl["compared_compositions"]), 2)
            per = pl["per_profile"]
            self.assertEqual(len(per), 2)
            # Per-product policy reflects structured composition; global snapshot aligns to objective only.
            g0 = per[0]["per_product_operator_policy"]["p1"]["adjustments_applied_count"]
            g1 = per[1]["per_product_operator_policy"]["p1"]["adjustments_applied_count"]
            self.assertNotEqual(g0, g1)

    def test_composition_duplicate_profile_id_rejected(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "config").mkdir(parents=True)
            _write_minimal_product(root, "p1", mission_id="education")
            with self.assertRaises(ValueError):
                evaluate_mission_experiment(
                    root,
                    compared_compositions=[
                        MissionExperimentComposition("revenue", ("revenue",), ()),
                    ],
                    product_ids=["p1"],
                    mode="targeted",
                )
