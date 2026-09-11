"""Tests for :mod:`argus.portfolio.strategy`."""

from __future__ import annotations

import shutil
import textwrap
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from argus.policy.effectiveness import OPERATOR_POLICY_EFFECTIVENESS_SCHEMA
from argus.portfolio.intervention_inbox import INTERVENTION_INBOX_SCHEMA
from argus.portfolio.operator_queue import OPERATOR_QUEUE_SCHEMA
from argus.portfolio.outcomes import PORTFOLIO_OUTCOMES_SCHEMA
from argus.portfolio.patterns import PORTFOLIO_PATTERNS_SCHEMA
from argus.portfolio.strategy import (
    PORTFOLIO_STRATEGY_SCHEMA,
    derive_portfolio_strategy_fields,
    render_portfolio_strategy_markdown,
)
from argus.products.creation import PRODUCT_CREATION_PROPOSALS_SCHEMA


def _outcomes(
    *,
    n: int,
    pos: int,
    neg: int,
    mix: int,
    flat: int,
    mas: dict[str, int],
    distinct: int = 2,
) -> dict:
    mids = [f"m{i}" for i in range(max(1, distinct))]
    return {
        "schema": PORTFOLIO_OUTCOMES_SCHEMA,
        "portfolio_outcome_summary": {
            "products_evaluated": n,
            "positive_count": pos,
            "negative_count": neg,
            "mixed_count": mix,
            "no_meaningful_movement_count": flat,
        },
        "mission_alignment_summary": mas,
        "portfolio_mission_provenance": {
            "mission_mix_summary": {
                "distinct_mission_ids": mids,
                "counts_by_mission_id": {mids[0]: n},
            }
        },
        "per_product_outcomes": [],
        "products_with_positive_trajectory": [],
        "products_with_negative_trajectory": [],
        "outcome_reason_codes": [],
    }


def _policy(*, strain: float = 0.15, stagnation: float = 0.35) -> dict:
    return {
        "schema": OPERATOR_POLICY_EFFECTIVENESS_SCHEMA,
        "current": {
            "by_objective": {
                "default": {
                    "intervention_strain_rate": strain,
                    "stagnation_rate": stagnation,
                }
            }
        },
        "notable_patterns": ["descriptive association A", "descriptive association B"],
    }


def _queue(entries: list[dict]) -> dict:
    return {"schema": OPERATOR_QUEUE_SCHEMA, "entries": entries}


def _patterns(rows: list[dict]) -> dict:
    return {"schema": PORTFOLIO_PATTERNS_SCHEMA, "detected_patterns": rows}


def _inbox(items: list[dict]) -> dict:
    return {"schema": INTERVENTION_INBOX_SCHEMA, "open_items": items}


def _delta(*, new_product: bool = False) -> dict:
    rows = [{"product_id": "new1", "note": "new_product_in_queue"}] if new_product else []
    return {"schema": "argus.portfolio_delta_report.v1", "products_with_material_change": rows}


def _creation(n: int) -> dict:
    return {"schema": PRODUCT_CREATION_PROPOSALS_SCHEMA, "proposal_count": n}


def _minimal_valid_product_yaml(product_id: str) -> str:
    return textwrap.dedent(
        f"""\
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
        """
    ).strip() + "\n"


class PortfolioStrategyTests(unittest.TestCase):
    def test_schema_growth_expand(self) -> None:
        o = _outcomes(
            n=8,
            pos=7,
            neg=0,
            mix=1,
            flat=0,
            mas={"positive": 7, "neutral": 1, "negative": 0},
            distinct=2,
        )
        q = _queue(
            [{"readiness_tier": "advance_ready", "understanding_debt": 0.2, "product_id": "a"}] * 6
        )
        r = derive_portfolio_strategy_fields(
            outcomes=o,
            patterns=_patterns([]),
            operator_queue=q,
            intervention_inbox=_inbox([]),
            policy_effectiveness=_policy(strain=0.1, stagnation=0.2),
            delta_report=_delta(),
            creation_proposals=None,
        )
        self.assertEqual(r["strategic_posture"], "expand")

    def test_schema_stagnating_consolidate(self) -> None:
        o = _outcomes(
            n=10,
            pos=1,
            neg=1,
            mix=4,
            flat=4,
            mas={"positive": 2, "neutral": 5, "negative": 3},
            distinct=4,
        )
        r = derive_portfolio_strategy_fields(
            outcomes=o,
            patterns=_patterns([]),
            operator_queue=_queue([]),
            intervention_inbox=_inbox([]),
            policy_effectiveness=_policy(strain=0.2, stagnation=0.6),
            delta_report=_delta(),
            creation_proposals=None,
        )
        self.assertEqual(r["strategic_posture"], "consolidate")

    def test_schema_intervention_heavy_repair(self) -> None:
        o = _outcomes(
            n=8,
            pos=1,
            neg=5,
            mix=1,
            flat=1,
            mas={"positive": 1, "neutral": 2, "negative": 5},
        )
        pat = _patterns(
            [
                {"pattern_id": "patterns.import.shared", "severity": "high", "title": "Import"},
                {"pattern_id": "patterns.import.blocked", "severity": "high", "title": "Blocked"},
            ]
        )
        ib = _inbox(
            [
                {
                    "product_id": "a",
                    "severity": "high",
                    "intervention_category": "import_health",
                    "in_active_queue": True,
                },
                {
                    "product_id": "b",
                    "severity": "high",
                    "intervention_category": "import_health",
                    "in_active_queue": True,
                },
                {
                    "product_id": "c",
                    "severity": "medium",
                    "intervention_category": "import_health",
                    "in_active_queue": True,
                },
            ]
        )
        q = _queue(
            [{"readiness_tier": "advance_ready", "understanding_debt": 0.3, "product_id": "a"}] * 4
        )
        r = derive_portfolio_strategy_fields(
            outcomes=o,
            patterns=pat,
            operator_queue=q,
            intervention_inbox=ib,
            policy_effectiveness=_policy(strain=0.55, stagnation=0.4),
            delta_report=_delta(),
            creation_proposals=None,
        )
        self.assertEqual(r["strategic_posture"], "repair")

    def test_schema_creation_opportunity_create(self) -> None:
        o = _outcomes(
            n=4,
            pos=1,
            neg=0,
            mix=1,
            flat=2,
            mas={"positive": 2, "neutral": 2, "negative": 0},
        )
        q = _queue(
            [{"readiness_tier": "advance_ready", "understanding_debt": 0.4, "product_id": "a"}] * 3
        )
        r = derive_portfolio_strategy_fields(
            outcomes=o,
            patterns=_patterns([]),
            operator_queue=q,
            intervention_inbox=_inbox([]),
            policy_effectiveness=_policy(strain=0.1, stagnation=0.3),
            delta_report=_delta(new_product=True),
            creation_proposals=_creation(4),
        )
        self.assertEqual(r["strategic_posture"], "create")

    def test_schema_mixed_signals_consolidate(self) -> None:
        o = _outcomes(
            n=10,
            pos=3,
            neg=0,
            mix=3,
            flat=4,
            mas={"positive": 3, "neutral": 4, "negative": 3},
            distinct=4,
        )
        q = _queue([{"readiness_tier": "observe_gap", "product_id": f"p{i}"} for i in range(8)])
        r = derive_portfolio_strategy_fields(
            outcomes=o,
            patterns=_patterns(
                [{"pattern_id": "patterns.stagnation.soft", "severity": "medium", "title": "Stagnation"}]
            ),
            operator_queue=q,
            intervention_inbox=_inbox(
                [{"product_id": "z", "severity": "low", "intervention_category": "x", "in_active_queue": True}]
            ),
            policy_effectiveness=_policy(strain=0.2, stagnation=0.45),
            delta_report=_delta(),
            creation_proposals=None,
        )
        self.assertEqual(r["strategic_posture"], "consolidate")

    def test_top_opportunities_filters_by_inventory_ids(self) -> None:
        o = _outcomes(
            n=4,
            pos=2,
            neg=0,
            mix=1,
            flat=1,
            mas={"positive": 2, "neutral": 2, "negative": 0},
        )
        o["products_with_positive_trajectory"] = ["prod_a", "ghost_positive", "prod_c"]
        q = _queue(
            [
                {"readiness_tier": "advance_ready", "understanding_debt": 0.2, "product_id": "prod_a"},
                {"readiness_tier": "advance_ready", "understanding_debt": 0.2, "product_id": "ghost_adv"},
            ]
        )
        r_open = derive_portfolio_strategy_fields(
            outcomes=o,
            patterns=_patterns([]),
            operator_queue=q,
            intervention_inbox=_inbox([]),
            policy_effectiveness=_policy(strain=0.1, stagnation=0.2),
            delta_report=_delta(),
            creation_proposals=None,
            valid_product_ids=None,
        )
        joined_open = "\n".join(r_open["top_opportunities"])
        self.assertIn("ghost_positive", joined_open)
        self.assertIn("ghost_adv", joined_open)

        r_filt = derive_portfolio_strategy_fields(
            outcomes=o,
            patterns=_patterns([]),
            operator_queue=q,
            intervention_inbox=_inbox([]),
            policy_effectiveness=_policy(strain=0.1, stagnation=0.2),
            delta_report=_delta(),
            creation_proposals=None,
            valid_product_ids=frozenset({"prod_a", "prod_c"}),
        )
        joined_f = "\n".join(r_filt["top_opportunities"])
        self.assertNotIn("ghost_positive", joined_f)
        self.assertNotIn("ghost_adv", joined_f)
        self.assertIn("prod_a", joined_f)
        self.assertIn("prod_c", joined_f)

    def test_top_risks_filters_negative_trajectory_by_inventory(self) -> None:
        o = _outcomes(
            n=3,
            pos=0,
            neg=2,
            mix=1,
            flat=0,
            mas={"positive": 0, "neutral": 1, "negative": 2},
        )
        o["products_with_negative_trajectory"] = ["gone_neg", "stay_neg"]
        r = derive_portfolio_strategy_fields(
            outcomes=o,
            patterns=_patterns([]),
            operator_queue=_queue([]),
            intervention_inbox=_inbox([]),
            policy_effectiveness=_policy(strain=0.2, stagnation=0.3),
            delta_report=_delta(),
            creation_proposals=None,
            valid_product_ids=frozenset({"stay_neg"}),
        )
        joined = "\n".join(r["top_risks"])
        self.assertNotIn("gone_neg", joined)
        self.assertIn("stay_neg", joined)

    def test_dominant_mission_mix_separates_inventory_and_outcomes_history(self) -> None:
        o = _outcomes(
            n=7,
            pos=3,
            neg=0,
            mix=2,
            flat=2,
            mas={"positive": 3, "neutral": 3, "negative": 1},
        )
        o["portfolio_mission_provenance"] = {
            "mission_mix_summary": {
                "distinct_mission_ids": ["revenue"],
                "counts_by_mission_id": {"revenue": 7},
            }
        }
        q = {
            "schema": OPERATOR_QUEUE_SCHEMA,
            "entries": [],
            "portfolio_mission_provenance": {
                "mission_mix_summary": {
                    "distinct_mission_ids": ["revenue"],
                    "counts_by_mission_id": {"revenue": 4},
                }
            },
        }
        r = derive_portfolio_strategy_fields(
            outcomes=o,
            patterns=_patterns([]),
            operator_queue=q,
            intervention_inbox=_inbox([]),
            policy_effectiveness=_policy(strain=0.1, stagnation=0.2),
            delta_report=_delta(),
            creation_proposals=None,
            inventory_valid_product_count=4,
        )
        dmm = r["dominant_mission_mix"]
        self.assertEqual(dmm.get("scope"), "inventory")
        self.assertIn("current inventory", dmm.get("summary", "").lower())
        self.assertIn("**4**", dmm.get("summary", ""))
        omm = dmm.get("outcomes_mission_mix")
        self.assertIsInstance(omm, dict)
        self.assertIn("**7**", omm.get("summary", ""))
        self.assertIn("outcomes-evaluated trajectory", omm.get("summary", "").lower())

    def test_build_payload_keys(self) -> None:
        o = _outcomes(n=2, pos=1, neg=0, mix=1, flat=0, mas={"positive": 1, "neutral": 1, "negative": 0})
        r = derive_portfolio_strategy_fields(
            outcomes=o,
            patterns=_patterns([]),
            operator_queue=_queue([]),
            intervention_inbox=_inbox([]),
            policy_effectiveness=_policy(),
            delta_report=_delta(),
            creation_proposals=None,
        )
        self.assertIn("dominant_mission_mix", r)
        self.assertIn("portfolio_pressures", r)
        self.assertIn("top_opportunities", r)
        self.assertIn("top_risks", r)
        self.assertIn("recommended_next_portfolio_moves", r)
        self.assertIn("posture_scores", r)

    def test_empty_portfolio_default_create(self) -> None:
        o = {
            "schema": PORTFOLIO_OUTCOMES_SCHEMA,
            "portfolio_outcome_summary": {"products_evaluated": 0},
            "mission_alignment_summary": {},
            "per_product_outcomes": [],
        }
        r = derive_portfolio_strategy_fields(
            outcomes=o,
            patterns=None,
            operator_queue=None,
            intervention_inbox=None,
            policy_effectiveness=None,
            delta_report=None,
            creation_proposals=None,
        )
        self.assertEqual(r["strategic_posture"], "create")
        sig = r["signals"]
        self.assertEqual(sig.get("zero_evaluated_bucket"), "unknown_without_outcomes")
        self.assertNotIn("until inventory exists", " ".join(r["rationale"]).lower())
        rationale_joined = " ".join(r["rationale"])
        self.assertIn("zero evaluated products", rationale_joined.lower())

    def test_zero_evaluated_no_inventory(self) -> None:
        o = {
            "schema": PORTFOLIO_OUTCOMES_SCHEMA,
            "portfolio_outcome_summary": {"products_evaluated": 0},
            "mission_alignment_summary": {},
            "per_product_outcomes": [],
        }
        r = derive_portfolio_strategy_fields(
            outcomes=o,
            patterns=None,
            operator_queue=None,
            intervention_inbox=None,
            policy_effectiveness=None,
            delta_report=None,
            creation_proposals=None,
            inventory_valid_product_count=0,
        )
        self.assertEqual(r["signals"].get("zero_evaluated_bucket"), "no_inventory")
        self.assertIn("validated inventory is empty", " ".join(r["rationale"]).lower())

    def test_zero_evaluated_inventory_without_outcomes(self) -> None:
        o = {
            "schema": PORTFOLIO_OUTCOMES_SCHEMA,
            "portfolio_outcome_summary": {"products_evaluated": 0},
            "mission_alignment_summary": {},
            "per_product_outcomes": [],
        }
        r = derive_portfolio_strategy_fields(
            outcomes=o,
            patterns=_patterns([]),
            operator_queue=_queue([]),
            intervention_inbox=_inbox([]),
            policy_effectiveness=None,
            delta_report=None,
            creation_proposals=None,
            inventory_valid_product_count=3,
        )
        self.assertEqual(r["signals"].get("zero_evaluated_bucket"), "inventory_without_outcomes")
        self.assertIn("3 product(s)", " ".join(r["rationale"]))
        self.assertIn("usable outcome history", " ".join(r["rationale"]).lower())

    def test_zero_evaluated_unknown_sparse_when_inventory_unscoped(self) -> None:
        o = {
            "schema": PORTFOLIO_OUTCOMES_SCHEMA,
            "portfolio_outcome_summary": {"products_evaluated": 0},
            "mission_alignment_summary": {},
            "per_product_outcomes": [],
        }
        r = derive_portfolio_strategy_fields(
            outcomes=o,
            patterns=_patterns(
                [{"pattern_id": "patterns.x", "severity": "high", "title": "H"}]
            ),
            operator_queue=None,
            intervention_inbox=None,
            policy_effectiveness=None,
            delta_report=None,
            creation_proposals=None,
        )
        self.assertEqual(r["signals"].get("zero_evaluated_bucket"), "unknown_sparse_evidence")

    def test_zero_evaluated_sparse_portfolio_evidence(self) -> None:
        o = {
            "schema": PORTFOLIO_OUTCOMES_SCHEMA,
            "portfolio_outcome_summary": {"products_evaluated": 0},
            "mission_alignment_summary": {},
            "per_product_outcomes": [],
        }
        r = derive_portfolio_strategy_fields(
            outcomes=o,
            patterns=_patterns(
                [{"pattern_id": "patterns.x", "severity": "medium", "title": "T"}]
            ),
            operator_queue=_queue([]),
            intervention_inbox=_inbox([]),
            policy_effectiveness=None,
            delta_report=None,
            creation_proposals=None,
            inventory_valid_product_count=2,
        )
        self.assertEqual(r["signals"].get("zero_evaluated_bucket"), "sparse_portfolio_evidence")
        joined = " ".join(r["rationale"])
        self.assertIn("2 product(s)", joined)
        self.assertIn("sparse", joined.lower())

    def test_render_markdown_includes_zero_evaluated_note(self) -> None:
        payload = {
            "schema": PORTFOLIO_STRATEGY_SCHEMA,
            "run_id": "x",
            "evaluated_at_utc": "t",
            "strategic_posture": "create",
            "rationale": ["a"],
            "dominant_mission_mix": {},
            "inputs": {"zero_evaluated_bucket": "no_inventory"},
        }
        md = render_portfolio_strategy_markdown(payload)
        self.assertIn("no_inventory", md)
        self.assertIn("zero evaluated products", md.lower())


class PortfolioStrategyBuildTests(unittest.TestCase):
    def test_run_wraps_schema(self) -> None:
        from pathlib import Path
        from tempfile import TemporaryDirectory

        from argus.portfolio.strategy import build_portfolio_strategy_payload

        with TemporaryDirectory() as td:
            root = Path(td)
            (root / "products").mkdir(parents=True, exist_ok=True)
            (root / "config").mkdir(parents=True, exist_ok=True)
            payload = build_portfolio_strategy_payload(
                root,
                limit_history=1,
                outcomes_payload=_outcomes(
                    n=1, pos=0, neg=0, mix=0, flat=1, mas={"positive": 0, "neutral": 1, "negative": 0}
                ),
                patterns_payload=_patterns([]),
                operator_queue_payload=_queue([]),
                intervention_inbox_payload=_inbox([]),
                policy_effectiveness_payload=_policy(),
                delta_report_payload=_delta(),
                creation_proposals_payload=None,
            )
            self.assertEqual(payload.get("schema"), PORTFOLIO_STRATEGY_SCHEMA)
            self.assertIn("strategic_posture", payload)
            self.assertEqual(payload["inputs"].get("inventory_valid_product_count"), 0)

    def test_build_payload_zero_outcomes_no_inventory_bucket(self) -> None:
        from pathlib import Path
        from tempfile import TemporaryDirectory

        from argus.portfolio.strategy import build_portfolio_strategy_payload

        with TemporaryDirectory() as td:
            root = Path(td)
            (root / "products").mkdir(parents=True, exist_ok=True)
            (root / "config").mkdir(parents=True, exist_ok=True)
            payload = build_portfolio_strategy_payload(
                root,
                limit_history=1,
                outcomes_payload={
                    "schema": PORTFOLIO_OUTCOMES_SCHEMA,
                    "portfolio_outcome_summary": {"products_evaluated": 0},
                    "mission_alignment_summary": {},
                    "per_product_outcomes": [],
                },
                patterns_payload=_patterns([]),
                operator_queue_payload=_queue([]),
                intervention_inbox_payload=_inbox([]),
                policy_effectiveness_payload=None,
                delta_report_payload=None,
                creation_proposals_payload=None,
            )
            self.assertEqual(payload["inputs"].get("zero_evaluated_bucket"), "no_inventory")
            md = render_portfolio_strategy_markdown(payload)
            self.assertIn("no_inventory", md)

    def test_build_payload_top_opportunities_no_ghost_after_product_removed(self) -> None:
        from argus.portfolio.strategy import build_portfolio_strategy_payload

        outcomes_stale = {
            "schema": PORTFOLIO_OUTCOMES_SCHEMA,
            "portfolio_outcome_summary": {
                "products_evaluated": 4,
                "positive_count": 2,
                "negative_count": 0,
                "mixed_count": 1,
                "no_meaningful_movement_count": 1,
            },
            "mission_alignment_summary": {"positive": 2, "neutral": 2, "negative": 0},
            "portfolio_mission_provenance": {
                "mission_mix_summary": {
                    "distinct_mission_ids": ["revenue"],
                    "counts_by_mission_id": {"revenue": 4},
                }
            },
            "per_product_outcomes": [],
            "products_with_positive_trajectory": ["prod_a", "prod_b", "prod_c", "phantom_artifact"],
            "products_with_negative_trajectory": [],
            "outcome_reason_codes": [],
        }
        with TemporaryDirectory() as td:
            root = Path(td)
            products = root / "products"
            for pid in ("prod_a", "prod_b", "prod_c"):
                pdir = products / pid
                pdir.mkdir(parents=True)
                (pdir / "product.yaml").write_text(_minimal_valid_product_yaml(pid), encoding="utf-8")
                scr = pdir / "scripts"
                scr.mkdir(exist_ok=True)
                (scr / "s.sh").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            (root / "config").mkdir(parents=True, exist_ok=True)

            pl_full = build_portfolio_strategy_payload(
                root,
                limit_history=1,
                outcomes_payload=outcomes_stale,
                patterns_payload=_patterns([]),
                operator_queue_payload=_queue([]),
                intervention_inbox_payload=_inbox([]),
                policy_effectiveness_payload=_policy(strain=0.1, stagnation=0.2),
                delta_report_payload=_delta(),
                creation_proposals_payload=None,
            )
            self.assertEqual(set(pl_full["inputs"]["inventory_valid_product_ids"]), {"prod_a", "prod_b", "prod_c"})
            joined = "\n".join(pl_full["top_opportunities"])
            self.assertNotIn("phantom_artifact", joined)
            self.assertIn("prod_a", joined)

            shutil.rmtree(products / "prod_b")

            pl_rm = build_portfolio_strategy_payload(
                root,
                limit_history=1,
                outcomes_payload=outcomes_stale,
                patterns_payload=_patterns([]),
                operator_queue_payload=_queue([]),
                intervention_inbox_payload=_inbox([]),
                policy_effectiveness_payload=_policy(strain=0.1, stagnation=0.2),
                delta_report_payload=_delta(),
                creation_proposals_payload=None,
            )
            self.assertEqual(set(pl_rm["inputs"]["inventory_valid_product_ids"]), {"prod_a", "prod_c"})
            joined2 = "\n".join(pl_rm["top_opportunities"])
            self.assertNotIn("phantom_artifact", joined2)
            self.assertNotIn("prod_b", joined2)
            self.assertIn("prod_a", joined2)
            self.assertIn("prod_c", joined2)


if __name__ == "__main__":
    unittest.main()
