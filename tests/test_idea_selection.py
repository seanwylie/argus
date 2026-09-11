"""Tests for idea portfolio selection (quotas, channels, cost, invent forcing)."""

from __future__ import annotations

import unittest

from argus.idea_generation.models import Idea, IdeaSource, IdeaType
from argus.idea_generation.select import SelectionPolicy, select_ideas
from argus.strategy.modes import PROFILE_BY_MODE, StrategyMode


def _mk(
    iid: str,
    *,
    itype: IdeaType,
    channel: str = "web",
    monetization: str = "ads",
    cost: str = "small",
    novelty: float = 0.5,
    ev: float = 0.55,
    conf: float = 0.6,
    title: str | None = None,
    desc: str | None = None,
) -> Idea:
    return Idea(
        idea_id=iid,
        title=title or f"Idea {iid}",
        description=desc or f"Description for {iid} with enough text tokens.",
        type=itype,
        source=IdeaSource.SIGNALS,
        novelty_score=novelty,
        adjacency_score=0.5,
        expected_value_score=ev,
        confidence_score=conf,
        cost_estimate=cost,
        channel_type=channel,
        monetization_type=monetization,
        rationale="test",
        product_id="p1",
    )


class TestChannelSaturation(unittest.TestCase):
    def test_tiktok_capped(self) -> None:
        ideas = [
            _mk(f"t{i}", itype=IdeaType.EXPLOIT, channel="tiktok", novelty=0.4, ev=0.5)
            for i in range(12)
        ]
        pol = SelectionPolicy(
            total_slots=10,
            max_per_channel=10,
            channel_max_overrides={"tiktok": 2},
            max_cost_units=100.0,
        )
        r = select_ideas(ideas, pol)
        tik = sum(1 for x in r.selected if x.channel_type.lower() == "tiktok")
        self.assertLessEqual(tik, 2)
        self.assertGreater(len(r.selected), 0)


class TestInventForced(unittest.TestCase):
    def test_invent_included_when_quota_rounded_to_zero(self) -> None:
        """When slot math leaves 0 invent slots, still force one invent if present in pool."""
        exploit_batch = [
            _mk(f"e{i}", itype=IdeaType.EXPLOIT, novelty=0.35, ev=0.45) for i in range(8)
        ]
        invent_one = _mk(
            "inv1",
            itype=IdeaType.INVENT,
            novelty=0.7,
            ev=0.62,
            title="Greenfield hybrid platform",
            desc="novel paradigm breakthrough combine ecosystem",
        )
        ideas = exploit_batch + [invent_one]
        pol = SelectionPolicy(
            total_slots=6,
            exploit_ratio=0.6,
            explore_ratio=0.3,
            invent_ratio=0.1,
            max_cost_units=80.0,
            force_invent_if_available=True,
        )
        r = select_ideas(ideas, pol)
        kinds = [x.type for x in r.selected]
        self.assertIn(IdeaType.INVENT, kinds)
        self.assertTrue(r.meta.get("invent_forced") or IdeaType.INVENT in kinds)


class TestCostLimits(unittest.TestCase):
    def test_high_cost_ideas_limited_by_budget(self) -> None:
        cheap = [
            _mk(f"c{i}", itype=IdeaType.EXPLOIT, cost="small", novelty=0.4, ev=0.5)
            for i in range(5)
        ]
        pricey = [
            _mk(f"h{i}", itype=IdeaType.EXPLOIT, cost="large", novelty=0.45, ev=0.7)
            for i in range(6)
        ]
        pol = SelectionPolicy(total_slots=10, max_cost_units=5.0, max_per_channel=10)
        r = select_ideas(cheap + pricey, pol)
        self.assertLessEqual(r.meta["cost_units_used"], 5.0 + 1e-6)
        large_pick = [x for x in r.selected if "large" in x.cost_estimate.lower()]
        self.assertLessEqual(len(large_pick), 1)


class TestStrategyAlignment(unittest.TestCase):
    def test_exploration_prefers_invent_in_meta_when_selected(self) -> None:
        inv = _mk(
            "i1",
            itype=IdeaType.INVENT,
            novelty=0.8,
            ev=0.55,
            title="New category net.new",
            desc="unusual hybrid invent breakthrough",
        )
        ex = _mk("x1", itype=IdeaType.EXPLOIT, novelty=0.3, ev=0.6)
        profile = PROFILE_BY_MODE[StrategyMode.EXPLORATION]
        r = select_ideas(
            [inv, ex],
            SelectionPolicy(total_slots=2, max_cost_units=20.0),
            strategy_profile=profile,
        )
        self.assertEqual(r.meta.get("strategy_mode"), "exploration")
        self.assertEqual(len(r.selected), 2)


class TestRejectLowValue(unittest.TestCase):
    def test_drops_low_novelty_and_low_ev(self) -> None:
        bad = _mk("bad", itype=IdeaType.EXPLOIT, novelty=0.1, ev=0.15)
        good = _mk("ok", itype=IdeaType.EXPLOIT, novelty=0.4, ev=0.5)
        r = select_ideas([bad, good], SelectionPolicy(total_slots=5))
        ids = {x.idea_id for x in r.selected}
        self.assertNotIn("bad", ids)
        self.assertIn("ok", ids)
        codes = [c for i, c in r.rejected if i == "bad"]
        self.assertIn("low_novelty_and_low_value", codes[0])


if __name__ == "__main__":
    unittest.main()
