"""Tests for human input storage, scope, expiration, and apply layer."""

from __future__ import annotations

import unittest
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

from argus.core.models.enums import EffortBucket, FindingKind, SeverityLevel
from argus.core.models.finding import Finding
from argus.input.apply import (
    apply_to_findings,
    is_active,
    merged_inputs_for_product,
    planning_suppress_kill_flag,
)
from argus.input.models import HumanInput
from argus.input.store import remove_input, save_input


class TestHumanInputModel(unittest.TestCase):
    def test_global_clears_product_id(self) -> None:
        hi = HumanInput(
            id="x",
            scope="global",
            product_id="should_clear",
            type="note",
            content="",
        )
        self.assertIsNone(hi.product_id)

    def test_product_requires_id(self) -> None:
        with self.assertRaises(ValueError):
            HumanInput(id="x", scope="product", product_id=None, type="note", content="")


class TestExpirationAndScope(unittest.TestCase):
    def test_expired_inactive(self) -> None:
        from datetime import datetime, timezone

        past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        hi = HumanInput(
            id="a",
            scope="global",
            product_id=None,
            type="strategy",
            content="x",
            expires_at=past,
        )
        self.assertFalse(is_active(hi))

    def test_merged_global_and_product(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            g = HumanInput(
                id="g1",
                scope="global",
                product_id=None,
                type="strategy",
                content="portfolio",
                created_at="2020-01-01T00:00:00+00:00",
            )
            p = HumanInput(
                id="p1",
                scope="product",
                product_id="alpha",
                type="note",
                content="only alpha",
                created_at="2020-01-02T00:00:00+00:00",
            )
            save_input(root, g)
            save_input(root, p)
            ma = merged_inputs_for_product(root, "alpha")
            self.assertEqual([x.id for x in ma], ["g1", "p1"])
            mb = merged_inputs_for_product(root, "beta")
            self.assertEqual([x.id for x in mb], ["g1"])

    def test_remove(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            hi = HumanInput(
                id="rm1",
                scope="global",
                product_id=None,
                type="note",
                content="",
                created_at="2020-01-01T00:00:00+00:00",
            )
            save_input(root, hi)
            self.assertTrue(remove_input(root, "rm1"))
            self.assertFalse(remove_input(root, "rm1"))


class TestApplySoft(unittest.TestCase):
    def test_findings_idempotent_source_confidence(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            hi = HumanInput(
                id="h1",
                scope="global",
                product_id=None,
                type="strategy",
                content="Do not kill any products this week",
                structured_fields={"no_kill": True},
                priority_weight=1.0,
                created_at="2020-01-01T00:00:00+00:00",
            )
            save_input(root, hi)
            f = Finding(
                id="f1",
                product_id="p",
                kind=FindingKind.DEPRECATION_CANDIDATE,
                severity=SeverityLevel.HIGH,
                effort=EffortBucket.SMALL,
                title="t",
                summary="s",
                recommendation="r",
                confidence=0.8,
            )
            a = apply_to_findings(root, "p", [f])
            b = apply_to_findings(root, "p", a)
            self.assertAlmostEqual(a[0].confidence, b[0].confidence, places=5)

    def test_planning_suppress_kill_soft(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            hi = HumanInput(
                id="h2",
                scope="global",
                product_id=None,
                type="strategy",
                content="no kill",
                structured_fields={"no_kill": True},
                priority_weight=1.0,
                created_at="2020-01-01T00:00:00+00:00",
            )
            save_input(root, hi)
            self.assertFalse(planning_suppress_kill_flag(root, "x", True))


if __name__ == "__main__":
    unittest.main()
