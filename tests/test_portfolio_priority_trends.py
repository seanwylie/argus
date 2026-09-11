"""Portfolio priority generation history and derived trends (argus.portfolio_priority_trends.v1)."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from argus.orchestrator.artifact_paths import (
    orchestration_generations_dir,
    portfolio_priority_trends_path,
)
from argus.orchestrator.portfolio_priorities import (
    PORTFOLIO_PRIORITIES_SCHEMA,
    write_portfolio_priorities_payload,
)
from argus.orchestrator.portfolio_priority_trends import (
    DEFAULT_TREND_WINDOW,
    MATERIAL_RANK_DELTA,
    PORTFOLIO_PRIORITY_TRENDS_SCHEMA,
    build_portfolio_priority_trends,
    list_portfolio_priority_generation_paths,
    read_portfolio_priority_trends_json,
    write_portfolio_priority_trends_artifact,
)


def _snap(
    *,
    ts: str,
    products: list[dict],
    rec: str | None = None,
    na: str | None = None,
) -> dict:
    top = products[0] if products else None
    rid = rec if rec is not None else (top["product_id"] if top else None)
    rna = na if na is not None else (top.get("next_action") if top else None)
    return {
        "schema": PORTFOLIO_PRIORITIES_SCHEMA,
        "schema_version": "1",
        "generated_at_utc": ts,
        "repo_root": "/tmp",
        "recommended_product_id": rid,
        "recommended_next_action": rna,
        "products": products,
    }


class TestPortfolioPriorityTrends(unittest.TestCase):
    def test_no_generations_empty_trends(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            p = build_portfolio_priority_trends(root, window_size=DEFAULT_TREND_WINDOW)
            self.assertEqual(p["schema"], PORTFOLIO_PRIORITY_TRENDS_SCHEMA)
            self.assertEqual(p["window_size"], 0)
            self.assertEqual(p["products"], [])
            self.assertEqual(p["portfolio_stability"], "stable")
            self.assertEqual(p["operator_recommendations"], [])

    def test_stability_volatile_when_majority_transitions_change_top(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            gen = orchestration_generations_dir(root)
            gen.mkdir(parents=True)
            # Four snapshots: top alternates a,b,a,b → 3 transitions, 3 changes → volatile
            for i, top in enumerate(["a", "b", "a", "b"]):
                pl = _snap(
                    ts=f"2026-06-{10+i:02d}T12:00:00+00:00",
                    products=[
                        {"product_id": top, "rank": 1, "priority_score": 10, "priority_reasons": []},
                        {"product_id": "z", "rank": 2, "priority_score": 1, "priority_reasons": []},
                    ],
                )
                (gen / f"portfolio_priorities_202606{10+i:02d}T120000Z.json").write_text(
                    json.dumps(pl), encoding="utf-8"
                )
            out = build_portfolio_priority_trends(root, window_size=5)
            self.assertEqual(out["portfolio_stability"], "volatile")
            self.assertGreaterEqual(float(out["portfolio_stability_score"]), 0.99)
            self.assertTrue(any("shifting" in r.lower() or "inspect" in r.lower() for r in out["operator_recommendations"]))

    def test_operator_recommendations_and_top_inspect_deterministic(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            gen = orchestration_generations_dir(root)
            gen.mkdir(parents=True)
            pl = _snap(
                ts="2026-07-01T12:00:00+00:00",
                products=[
                    {"product_id": "c", "rank": 3, "priority_score": 1, "priority_reasons": []},
                    {"product_id": "a", "rank": 1, "priority_score": 10, "priority_reasons": []},
                    {"product_id": "b", "rank": 2, "priority_score": 5, "priority_reasons": []},
                ],
            )
            (gen / "portfolio_priorities_20260701T120000Z.json").write_text(json.dumps(pl), encoding="utf-8")
            out = build_portfolio_priority_trends(root, window_size=5)
            self.assertEqual(out["top_products_to_inspect"], ["a", "b", "c"])
            o1 = build_portfolio_priority_trends(root, window_size=5)
            o2 = build_portfolio_priority_trends(root, window_size=5)
            self.assertEqual(o1["operator_recommendations"], o2["operator_recommendations"])

    def test_read_trends_json_roundtrip(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_portfolio_priority_trends_artifact(root, window_size=3)
            raw, err = read_portfolio_priority_trends_json(root)
            self.assertIsNone(err)
            self.assertIsNotNone(raw)
            assert raw is not None
            self.assertEqual(raw.get("schema"), PORTFOLIO_PRIORITY_TRENDS_SCHEMA)

    def test_times_ranked_first_and_churn(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            gen = orchestration_generations_dir(root)
            gen.mkdir(parents=True)
            for i, ts in enumerate(
                [
                    "2026-04-10T10:00:00+00:00",
                    "2026-04-11T10:00:00+00:00",
                    "2026-04-12T10:00:00+00:00",
                ]
            ):
                prods = [
                    {"product_id": "a", "rank": 1, "priority_score": 10, "priority_reasons": []},
                    {"product_id": "b", "rank": 2, "priority_score": 5, "priority_reasons": []},
                ]
                if i == 1:
                    prods = [
                        {"product_id": "b", "rank": 1, "priority_score": 10, "priority_reasons": []},
                        {"product_id": "a", "rank": 2, "priority_score": 5, "priority_reasons": []},
                    ]
                payload = _snap(ts=ts, products=prods)
                name = f"portfolio_priorities_{ts[:10].replace('-', '')}T100000Z.json"
                if i == 1:
                    name = "portfolio_priorities_20260411T100000Z.json"
                if i == 2:
                    name = "portfolio_priorities_20260412T100000Z.json"
                (gen / name).write_text(json.dumps(payload), encoding="utf-8")

            out = build_portfolio_priority_trends(root, window_size=5)
            self.assertIn("changed", out["churn_summary"].lower())
            by_id = {r["product_id"]: r for r in out["products"]}
            self.assertEqual(by_id["a"]["times_ranked_first"], 2)
            self.assertEqual(by_id["b"]["times_ranked_first"], 1)

    def test_rising_and_falling_classification(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            gen = orchestration_generations_dir(root)
            gen.mkdir(parents=True)

            # Rising: ranks over time 4 → 3 → 1 (latest much better than average ~2.67)
            pl1 = _snap(
                ts="2026-04-10T12:00:00+00:00",
                products=[
                    {"product_id": "rise", "rank": 4, "priority_score": 1, "priority_reasons": []},
                    {"product_id": "x", "rank": 1, "priority_score": 9, "priority_reasons": []},
                ],
            )
            (gen / "portfolio_priorities_20260410T100000Z.json").write_text(json.dumps(pl1), encoding="utf-8")
            pl2 = _snap(
                ts="2026-04-11T12:00:00+00:00",
                products=[
                    {"product_id": "rise", "rank": 3, "priority_score": 2, "priority_reasons": []},
                    {"product_id": "x", "rank": 2, "priority_score": 8, "priority_reasons": []},
                ],
            )
            (gen / "portfolio_priorities_20260411T100000Z.json").write_text(json.dumps(pl2), encoding="utf-8")
            pl3 = _snap(
                ts="2026-04-12T12:00:00+00:00",
                products=[
                    {"product_id": "rise", "rank": 1, "priority_score": 9, "priority_reasons": []},
                    {"product_id": "x", "rank": 2, "priority_score": 7, "priority_reasons": []},
                ],
            )
            (gen / "portfolio_priorities_20260412T100000Z.json").write_text(json.dumps(pl3), encoding="utf-8")

            out = build_portfolio_priority_trends(root, window_size=5)
            rise = next(r for r in out["products"] if r["product_id"] == "rise")
            self.assertTrue(rise["rising"], msg=f"expected rising, got {rise}")
            self.assertFalse(rise["falling"])

            # Falling: reset folder
            for f in gen.glob("*.json"):
                f.unlink()
            for i, rk in enumerate([1, 2, 4]):
                ts = f"2026-04-{10+i:02d}T12:00:00+00:00"
                pl = _snap(
                    ts=ts,
                    products=[
                        {"product_id": "fall", "rank": rk, "priority_score": 10 - i, "priority_reasons": []},
                        {"product_id": "z", "rank": 5, "priority_score": 1, "priority_reasons": []},
                    ],
                )
                (gen / f"portfolio_priorities_202604{10+i:02d}T120000Z.json").write_text(
                    json.dumps(pl), encoding="utf-8"
                )
            out_f = build_portfolio_priority_trends(root, window_size=5)
            fall = next(r for r in out_f["products"] if r["product_id"] == "fall")
            self.assertTrue(fall["falling"], msg=f"expected falling, got {fall}")
            self.assertGreaterEqual(float(fall["latest_rank"]) - float(fall["average_rank"]), MATERIAL_RANK_DELTA - 0.01)

    def test_same_second_generation_gets_suffix(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            gen = orchestration_generations_dir(root)
            gen.mkdir(parents=True)
            ts = "2026-10-01T12:00:00+00:00"
            base = _snap(
                ts=ts,
                products=[
                    {"product_id": "a", "rank": 1, "priority_score": 1, "priority_reasons": []},
                ],
            )
            from argus.orchestrator.portfolio_priority_trends import (
                write_portfolio_priorities_generation_copy,
            )

            text = json.dumps(base) + "\n"
            p1 = write_portfolio_priorities_generation_copy(root, base, text)
            p2 = write_portfolio_priorities_generation_copy(root, base, text)
            self.assertIsNotNone(p1)
            self.assertIsNotNone(p2)
            assert p1 is not None and p2 is not None
            self.assertTrue(p1.is_file())
            self.assertTrue(p2.is_file())
            self.assertNotEqual(p1.name, p2.name)
            self.assertIn("_2.json", p2.name)

    def test_stable_and_fewer_than_five_snapshots(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            gen = orchestration_generations_dir(root)
            gen.mkdir(parents=True)
            for day in (1, 2):
                pl = _snap(
                    ts=f"2026-05-{day:02d}T12:00:00+00:00",
                    products=[
                        {"product_id": "st", "rank": 2, "priority_score": 5, "priority_reasons": []},
                        {"product_id": "o", "rank": 1, "priority_score": 9, "priority_reasons": []},
                    ],
                )
                (gen / f"portfolio_priorities_202605{day:02d}T120000Z.json").write_text(
                    json.dumps(pl), encoding="utf-8"
                )
            out = build_portfolio_priority_trends(root, window_size=5)
            self.assertEqual(out["window_size"], 2)
            st = next(r for r in out["products"] if r["product_id"] == "st")
            self.assertTrue(st["stable"])
            self.assertFalse(st["rising"])
            self.assertFalse(st["falling"])

    def test_missing_product_in_some_snapshots(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            gen = orchestration_generations_dir(root)
            gen.mkdir(parents=True)
            (gen / "portfolio_priorities_20260601T120000Z.json").write_text(
                json.dumps(
                    _snap(
                        ts="2026-06-01T12:00:00+00:00",
                        products=[
                            {"product_id": "only_later", "rank": 1, "priority_score": 9, "priority_reasons": []},
                        ],
                    )
                ),
                encoding="utf-8",
            )
            (gen / "portfolio_priorities_20260602T120000Z.json").write_text(
                json.dumps(
                    _snap(
                        ts="2026-06-02T12:00:00+00:00",
                        products=[
                            {"product_id": "only_later", "rank": 1, "priority_score": 9, "priority_reasons": []},
                            {"product_id": "newbie", "rank": 2, "priority_score": 5, "priority_reasons": []},
                        ],
                    )
                ),
                encoding="utf-8",
            )
            out = build_portfolio_priority_trends(root, window_size=5)
            nb = next(r for r in out["products"] if r["product_id"] == "newbie")
            self.assertEqual(nb["latest_rank"], 2)
            self.assertEqual(nb["average_rank"], 2.0)
            self.assertEqual(nb["times_ranked_first"], 0)

    def test_deterministic_product_order_and_rounding(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            gen = orchestration_generations_dir(root)
            gen.mkdir(parents=True)
            (gen / "portfolio_priorities_20260701T120000Z.json").write_text(
                json.dumps(
                    _snap(
                        ts="2026-07-01T12:00:00+00:00",
                        products=[
                            {"product_id": "z", "rank": 2, "priority_score": 10, "priority_reasons": []},
                            {"product_id": "a", "rank": 1, "priority_score": 20, "priority_reasons": []},
                        ],
                    )
                ),
                encoding="utf-8",
            )
            o1 = build_portfolio_priority_trends(root)["products"]
            o2 = build_portfolio_priority_trends(root)["products"]
            self.assertEqual(o1, o2)
            self.assertEqual([r["product_id"] for r in o1], ["a", "z"])

    def test_write_payload_writes_generation_and_trends(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            payload = _snap(
                ts="2026-08-01T15:30:45+00:00",
                products=[
                    {"product_id": "solo", "rank": 1, "priority_score": 99, "priority_reasons": []},
                ],
            )
            write_portfolio_priorities_payload(root, payload)
            gens = list_portfolio_priority_generation_paths(root)
            self.assertEqual(len(gens), 1)
            self.assertIn("portfolio_priorities_20260801T153045Z.json", gens[0].name)
            tr = portfolio_priority_trends_path(root)
            self.assertTrue(tr.is_file())
            raw = json.loads(tr.read_text(encoding="utf-8"))
            self.assertEqual(raw["schema"], "argus.portfolio_priority_trends.v1")
            self.assertEqual(len(raw["products"]), 1)

    def test_write_trends_artifact_idempotent_shape(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            p = write_portfolio_priority_trends_artifact(root, window_size=3)
            self.assertTrue(p.is_file())

    def test_invalid_generation_files_skipped_with_message(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            gen = orchestration_generations_dir(root)
            gen.mkdir(parents=True)
            (gen / "portfolio_priorities_20260901T120000Z.json").write_text("not json", encoding="utf-8")
            out = build_portfolio_priority_trends(root)
            self.assertEqual(out["generations_considered"], 0)
            self.assertIn("valid", out["churn_summary"].lower())


if __name__ == "__main__":
    unittest.main()
