"""Novelty and diversity scoring for ideas."""

from __future__ import annotations

import unittest

from argus.idea_generation.diversity import (
    marginal_diversity_impact,
    saturation_penalty_for_position,
    saturation_penalty_multiplier,
)
from argus.idea_generation.models import Idea, IdeaSource, IdeaType
from argus.idea_generation.novelty import (
    compute_novelty_score,
    cross_domain_score,
    duplicate_penalty,
    idea_fingerprint,
    max_similarity_to_corpus,
    pairing_novelty,
)
from argus.idea_generation.score import lexical_type_novelty


def _idea(
    *,
    idea_id: str = "id1",
    title: str,
    desc: str,
    channel: str = "web",
    monetization: str = "subscription",
    itype: IdeaType = IdeaType.EXPLORE,
) -> Idea:
    return Idea(
        idea_id=idea_id,
        title=title,
        description=desc,
        type=itype,
        source=IdeaSource.SYNTHESIS,
        novelty_score=0.0,
        adjacency_score=0.5,
        expected_value_score=0.5,
        confidence_score=0.5,
        cost_estimate="small",
        channel_type=channel,
        monetization_type=monetization,
        rationale="r",
    )


class TestNovelty(unittest.TestCase):
    def test_duplicate_fingerprint_lowers_novelty(self) -> None:
        a = _idea(title="Same TikTok growth play", desc="clone viral format for niche")
        b = _idea(title="Same TikTok growth play", desc="clone viral format for niche")
        self.assertEqual(idea_fingerprint(a), idea_fingerprint(b))
        pen = duplicate_penalty(
            a,
            recent_fingerprints={idea_fingerprint(a)},
            sibling_ideas=[],
        )
        self.assertLess(pen, 0.2)

    def test_cross_domain_higher_than_single_domain(self) -> None:
        single = cross_domain_score("Blog SEO", "write articles for organic traffic")
        multi = cross_domain_score(
            "API + content hybrid",
            "B2B saas platform combining subscription api and tiktok distribution",
        )
        self.assertGreater(multi, single)

    def test_duplicate_idea_low_novelty_score(self) -> None:
        idea = _idea(
            title="TikTok SEO funnel",
            desc="TikTok clips driving to SEO landing pages with ads monetization",
            channel="tiktok",
            monetization="ads",
        )
        lex = lexical_type_novelty(idea, seed="x")
        near_dup = _idea(
            idea_id="id2",
            title="TikTok SEO funnel",
            desc="TikTok clips driving to SEO landing pages with ads monetization",
            channel="tiktok",
            monetization="ads",
        )
        nov = compute_novelty_score(
            idea,
            product_summaries=["tiktok clone app for creators"],
            recent_idea_texts=[],
            recent_fingerprints=set(),
            batch_siblings=[near_dup],
            lexical_novelty=lex,
            seed="x",
        )
        self.assertLess(nov, 0.45)

    def test_cross_domain_idea_higher_novelty(self) -> None:
        inv = _idea(
            title="Greenfield",
            desc="net.new hybrid combining api marketplace and community content network",
            channel="api",
            monetization="usage",
            itype=IdeaType.INVENT,
        )
        plain = _idea(
            title="Optimize",
            desc="scale conversion on existing web funnel",
            channel="web",
            monetization="subscription",
            itype=IdeaType.EXPLOIT,
        )
        lex_i = lexical_type_novelty(inv, seed="s")
        lex_p = lexical_type_novelty(plain, seed="s")
        nov_inv = compute_novelty_score(
            inv,
            product_summaries=["saas web app subscription"],
            recent_idea_texts=[],
            recent_fingerprints=set(),
            batch_siblings=[],
            lexical_novelty=lex_i,
            seed="s",
        )
        nov_plain = compute_novelty_score(
            plain,
            product_summaries=["saas web app subscription"],
            recent_idea_texts=[],
            recent_fingerprints=set(),
            batch_siblings=[],
            lexical_novelty=lex_p,
            seed="s",
        )
        self.assertGreater(nov_inv, nov_plain)

    def test_pairing_tiktok_ads_is_common(self) -> None:
        self.assertLess(pairing_novelty("tiktok", "ads"), pairing_novelty("api", "enterprise"))


class TestDiversity(unittest.TestCase):
    def test_saturated_channel_penalty(self) -> None:
        from collections import Counter

        ch = Counter({"tiktok": 6})
        mo = Counter({"ads": 6})
        ln: Counter[str] = Counter()
        pat: Counter[tuple[str, str, str]] = Counter()
        idea = _idea(title="x", desc="y", channel="tiktok", monetization="ads")
        m = saturation_penalty_multiplier(
            idea,
            channel_counts=ch,
            monetization_counts=mo,
            lineage_counts=ln,
            pattern_counts=pat,
            channel_cap=6,
        )
        self.assertLess(m, 1.0)

    def test_marginal_diversity_rarer_channel_higher(self) -> None:
        from collections import Counter

        prior_ch = Counter({"web": 5})
        prior_mo = Counter({"subscription": 5})
        prior_pt: Counter[tuple[str, str, str]] = Counter()
        rare = _idea(title="a", desc="b", channel="api", monetization="usage")
        common = _idea(title="c", desc="d", channel="web", monetization="subscription")
        dr = marginal_diversity_impact(
            rare,
            prior_channels=prior_ch,
            prior_monetizations=prior_mo,
            prior_patterns=prior_pt,
        )
        dc = marginal_diversity_impact(
            common,
            prior_channels=prior_ch,
            prior_monetizations=prior_mo,
            prior_patterns=prior_pt,
        )
        self.assertGreater(dr, dc)

    def test_saturation_position_stacks_tiktok(self) -> None:
        ideas = [
            _idea(title=f"t{i}", desc="d", channel="tiktok", monetization="ads") for i in range(6)
        ]
        seventh = _idea(title="t7", desc="d", channel="tiktok", monetization="ads")
        p = saturation_penalty_for_position(seventh, ideas)
        self.assertLessEqual(p, 0.75)


class TestCorpusSimilarity(unittest.TestCase):
    def test_max_similarity_identical(self) -> None:
        self.assertAlmostEqual(
            max_similarity_to_corpus("hello world foo bar", ["hello world foo bar"]),
            1.0,
            places=2,
        )


if __name__ == "__main__":
    unittest.main()
