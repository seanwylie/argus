"""Tests for deterministic trend and drift rules."""

from __future__ import annotations

import unittest

from argus.history.models import ProductSnapshot
from argus.trends.analyze import analyze_product_series
from argus.trends.models import TrendFlag
from argus.trends.rules import assign_trend_flags, drift_signals, series_metrics


def _snap(
    *,
    product_id: str = "p1",
    observed_at: str,
    lifecycle_stage: str = "idea",
    findings: int = 0,
    action: str = "noop",
    cost: float | None = 10.0,
    conf: float | None = 0.5,
    prio: float | None = 1.0,
    esc: int = 0,
    kill: bool = False,
    last_signal: str | None = "2026-01-01T12:00:00+00:00",
) -> ProductSnapshot:
    return ProductSnapshot(
        snapshot_id="snap",
        product_id=product_id,
        observed_at_utc=observed_at,
        state=lifecycle_stage,
        status="active",
        lifecycle_stage=lifecycle_stage,
        monthly_cost_usd=cost,
        last_signal_at=last_signal,
        active_findings_count=findings,
        findings_by_severity={},
        top_recommended_action=action,
        priority_score=prio,
        top_confidence=conf,
        escalation_count=esc,
        lifecycle_scores={},
        kill_candidate=kill,
        source_paths={},
    )


class TestTrendRules(unittest.TestCase):
    def test_improving_trend(self) -> None:
        s = [
            _snap(observed_at="2026-01-01T00:00:00+00:00", findings=5),
            _snap(observed_at="2026-01-02T00:00:00+00:00", findings=3),
            _snap(observed_at="2026-01-03T00:00:00+00:00", findings=1),
        ]
        m = series_metrics(s)
        dr = drift_signals(s, m)
        fl = assign_trend_flags(s, m, dr)
        self.assertIn(TrendFlag.IMPROVING.value, fl)
        self.assertNotIn(TrendFlag.RISK_INCREASING.value, fl)

    def test_stagnation(self) -> None:
        s = [
            _snap(
                observed_at="2026-01-01T00:00:00+00:00",
                lifecycle_stage="validate",
                findings=2,
                last_signal="2026-01-01T10:00:00+00:00",
                action="steady",
            ),
            _snap(
                observed_at="2026-01-02T00:00:00+00:00",
                lifecycle_stage="validate",
                findings=2,
                last_signal="2026-01-01T10:00:00+00:00",
                action="steady",
            ),
            _snap(
                observed_at="2026-01-03T00:00:00+00:00",
                lifecycle_stage="validate",
                findings=2,
                last_signal="2026-01-01T10:00:00+00:00",
                action="steady",
            ),
        ]
        m = series_metrics(s)
        dr = drift_signals(s, m)
        self.assertTrue(any("no meaningful movement" in x for x in dr))
        fl = assign_trend_flags(s, m, dr)
        self.assertIn(TrendFlag.STAGNATING.value, fl)

    def test_rising_findings_drift(self) -> None:
        s = [
            _snap(observed_at="2026-01-01T00:00:00+00:00", findings=1),
            _snap(observed_at="2026-01-02T00:00:00+00:00", findings=2),
            _snap(observed_at="2026-01-03T00:00:00+00:00", findings=4),
        ]
        m = series_metrics(s)
        dr = drift_signals(s, m)
        self.assertTrue(any("rose" in x for x in dr))
        fl = assign_trend_flags(s, m, dr)
        self.assertIn(TrendFlag.RISK_INCREASING.value, fl)

    def test_escalation_recurrence(self) -> None:
        s = [
            _snap(observed_at="2026-01-01T00:00:00+00:00", esc=0, findings=1),
            _snap(observed_at="2026-01-02T00:00:00+00:00", esc=1, findings=1),
            _snap(observed_at="2026-01-03T00:00:00+00:00", esc=1, findings=1),
        ]
        m = series_metrics(s)
        dr = drift_signals(s, m)
        self.assertTrue(any("escalation" in x.lower() for x in dr))

    def test_analyze_end_to_end(self) -> None:
        s = [
            _snap(observed_at="2026-01-01T00:00:00+00:00", findings=3),
            _snap(observed_at="2026-01-02T00:00:00+00:00", findings=1),
        ]
        out = analyze_product_series(s)
        self.assertEqual(out.window_size, 2)
        self.assertGreater(len(out.summary), 0)
        self.assertEqual(out.metrics.get("n"), 2)


if __name__ == "__main__":
    unittest.main()
