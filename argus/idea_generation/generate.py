"""Generate :class:`Idea` rows from signals, findings, synthesis, and mutation."""

from __future__ import annotations

from pathlib import Path

from argus.core.models.enums import SignalType
from argus.core.models.finding import Finding
from argus.findings.persistence import load_latest_findings
from argus.idea_generation.classify import classify_type
from argus.idea_generation.models import Idea, IdeaSource, new_idea_id
from argus.idea_generation.score import apply_scores
from argus.idea_generation.signal_hygiene import (
    apply_hygiene_score_nudge,
    classify_signal_record,
    sort_key_for_idea_generation,
)
from argus.idea_generation.synthesis import synthesize_ideas
from argus.signals.persistence import load_latest_bundle
from argus.temporal.persistence import load_latest_temporal_bundle


def _clip(s: str, n: int) -> str:
    s = s.strip()
    return s if len(s) <= n else s[: n - 3] + "..."


def generate_from_signals(
    repo_root: Path,
    product_id: str,
    *,
    seed: str = "",
) -> list[Idea]:
    """Derive ideas from latest signal records (emphasizes temporal / freshness when present)."""
    bundle = load_latest_bundle(repo_root, product_id)
    if bundle is None or not bundle.records:
        return []

    temporal = load_latest_temporal_bundle(repo_root, product_id)
    temporal_hint = ""
    if isinstance(temporal, dict):
        sigs = temporal.get("signals") or []
        if isinstance(sigs, list) and sigs:
            buckets = [str(s.get("freshness_bucket", "")) for s in sigs if isinstance(s, dict)]
            if any(b == "stale" for b in buckets):
                temporal_hint = " Signal recency: some observations are stale — refresh before scaling."

    records = sorted(bundle.records, key=sort_key_for_idea_generation)

    ideas: list[Idea] = []
    for r in records[:8]:
        hygiene = classify_signal_record(r)
        p = r.payload if isinstance(r.payload, dict) else {}
        summary = ""
        if r.signal_type == SignalType.TEMPORAL:
            t = p.get("temporal") if isinstance(p.get("temporal"), dict) else {}
            summary = str(t.get("normalized") or t.get("headline") or r.source)
        else:
            summary = str(p.get("summary") or p.get("metric") or r.source)

        title = f"Signal-led action: {r.signal_type.value} ({r.id[:12]})"
        desc = (
            f"Observed at {r.observed_at.isoformat()}. Source={r.source}. "
            f"Payload hint: {_clip(summary, 280)}.{temporal_hint}"
        )
        if hygiene.signal_quality_score != "high":
            desc += f" [signal_quality={hygiene.signal_quality_score}]"
        it = classify_type(title, desc, source=IdeaSource.SIGNALS)
        idea = Idea(
            idea_id=new_idea_id("idea"),
            title=title,
            description=desc,
            type=it,
            source=IdeaSource.SIGNALS,
            novelty_score=0.0,
            adjacency_score=0.0,
            expected_value_score=0.0,
            confidence_score=0.0,
            cost_estimate="small",
            channel_type=_infer_channel(desc),
            monetization_type=_infer_monetization(desc),
            rationale="Derived from normalized SignalRecord; prioritize validate-then-scale.",
            product_id=product_id,
            signal_hygiene=hygiene.to_dict(),
            signal_provenance={
                "signal_type": r.signal_type.value,
                "adapter_source": r.source,
            },
        )
        apply_scores(idea, seed=seed + product_id)
        c, e = apply_hygiene_score_nudge(idea.confidence_score, idea.expected_value_score, hygiene)
        idea.confidence_score = c
        idea.expected_value_score = e
        ideas.append(idea)

    return ideas


def generate_from_findings(
    repo_root: Path,
    product_id: str,
    *,
    seed: str = "",
    findings_rows: list[Finding] | None = None,
) -> list[Idea]:
    """Map findings to experiment / product motion ideas.

    When ``findings_rows`` is set (e.g. merged canonical + experiment-surfaced), use it instead of
    ``runs/findings/latest`` alone.
    """
    if findings_rows is not None:
        rows = findings_rows[:32]
    else:
        fb = load_latest_findings(repo_root, product_id)
        if fb is None or not fb.findings:
            return []
        rows = fb.findings[:32]

    ideas: list[Idea] = []
    for f in rows:
        title = f"Finding → action: {f.title}"
        desc = _clip(f.summary or f.title, 400)
        if f.recommendation:
            desc += f" Recommendation: {_clip(f.recommendation, 240)}"
        it = classify_type(title, desc, source=IdeaSource.FINDINGS)
        idea = Idea(
            idea_id=new_idea_id("idea"),
            title=title,
            description=desc,
            type=it,
            source=IdeaSource.FINDINGS,
            novelty_score=0.0,
            adjacency_score=0.0,
            expected_value_score=0.0,
            confidence_score=0.0,
            cost_estimate=_effort_to_cost(f.effort.value if f.effort else "medium"),
            channel_type="hybrid",
            monetization_type=_infer_monetization(desc),
            rationale=f"Grounded in finding kind={f.kind.value}, severity={f.severity.value}.",
            product_id=product_id,
        )
        apply_scores(idea, seed=seed + f.id)
        ideas.append(idea)
    return ideas


def generate_from_advisors_stub(
    repo_root: Path,
    product_id: str,
    *,
    seed: str = "",
) -> list[Idea]:
    """
    Deprecated: advisors no longer add net-new rows to the primary idea list.

    Use :mod:`argus.advisors.idea_expansion` (invoked from :func:`run_pipeline`) so advisors
    only expand/critique existing system-generated ideas under bundle metadata.
    """
    _ = repo_root, product_id, seed
    return []


def generate_synthesis(
    repo_root: Path,
    product_id: str | None,
    *,
    seed: str = "",
) -> list[Idea]:
    """
    Combinatorial synthesis (products, signals, findings, channels, monetization).

    Implemented in :mod:`argus.idea_generation.synthesis` — all ideas are ``invent`` / ``synthesis``.
    """
    return synthesize_ideas(repo_root, product_id, seed=seed, max_ideas=32)


def generate_mutation(
    repo_root: Path,
    product_id: str | None,
    base_ideas: list[Idea],
    *,
    seed: str = "",
    count: int = 3,
) -> list[Idea]:
    """Derive perturbed variants from existing ideas (seeded; see :mod:`argus.idea_generation.mutation`)."""
    from argus.idea_generation.mutation import mutate_ideas

    _ = repo_root  # reserved for future repo-aware mutation (e.g. novelty corpus)
    return mutate_ideas(base_ideas, count=count, seed=seed, product_id=product_id, weird_ratio=0.35)


def _infer_channel(text: str) -> str:
    t = text.lower()
    if "tiktok" in t or "short" in t:
        return "tiktok"
    if "api" in t:
        return "api"
    if "mobile" in t or "app" in t:
        return "tool"
    return "web"


def _infer_monetization(text: str) -> str:
    t = text.lower()
    if "subscription" in t or "mrr" in t:
        return "subscription"
    if "ad" in t or "ads" in t:
        return "ads"
    if "usage" in t or "meter" in t:
        return "usage"
    if "enterprise" in t:
        return "enterprise"
    return "hybrid"


def _effort_to_cost(effort: str) -> str:
    e = effort.lower()
    if e == "small":
        return "small"
    if e == "large":
        return "large"
    return "medium"
