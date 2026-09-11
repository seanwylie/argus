"""Churn and stability analysis over decision memory entries."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from argus.decision.history.models import DecisionChurnReport, DecisionMemoryEntry
from argus.escalation.packet import list_packets


def intent_bucket(intent: str, summary: str) -> str:
    """Coarse bucket for oscillation detection (deterministic keywords)."""
    s = f"{intent} {summary}".lower()
    if re.search(r"\b(hold|pause|freeze|wait)\b", s):
        return "hold"
    if re.search(r"\b(deprecate|deprecation|kill|sunset|abandon|wind\s*down)\b", s):
        return "deprecate"
    if re.search(r"\b(improve|grow|scale|invest|accelerat)\b", s):
        return "improve"
    return "other"


def _max_consecutive_bucket(entries: list[DecisionMemoryEntry], bucket: str) -> int:
    best = 0
    cur = 0
    for e in entries:
        b = intent_bucket(e.top_intent, e.top_recommended_action)
        if b == bucket:
            cur += 1
            best = max(best, cur)
        else:
            cur = 0
    return best


def _oscillation_triples(intents: list[str]) -> int:
    """Count i where intent[i]==intent[i-2] and intent[i]!=intent[i-1]."""
    if len(intents) < 3:
        return 0
    n = 0
    for i in range(2, len(intents)):
        if intents[i] == intents[i - 2] and intents[i] != intents[i - 1]:
            n += 1
    return n


def escalation_repeated_titles(repo_root: Path, product_id: str) -> list[str]:
    """Titles that appear more than once for this product (lightweight theme signal)."""
    rows = list_packets(repo_root, limit=400)
    titles: list[str] = []
    for r in rows:
        if str(r.get("product_id", "")) != product_id:
            continue
        t = str(r.get("title", "") or "").strip()
        if t:
            titles.append(t)
    seen: set[str] = set()
    dup: set[str] = set()
    for t in titles:
        if t in seen:
            dup.add(t)
        seen.add(t)
    return sorted(dup)[:12]


def analyze_churn(
    product_id: str,
    entries: list[DecisionMemoryEntry],
    *,
    repo_root: Path | None = None,
) -> DecisionChurnReport:
    """
    Compute churn/stability scores and human-readable summary lines.

    ``repo_root`` is optional; when set, repeated escalation titles are included.
    """
    n = len(entries)
    if n == 0:
        return DecisionChurnReport(
            product_id=product_id,
            run_count=0,
            top_action_change_count=0,
            max_consecutive_same_top=0,
            intent_bucket_flips=0,
            confidence_min=None,
            confidence_max=None,
            confidence_range=None,
            churn_score=0.0,
            stability_score=1.0,
            summary_lines=["No decision generation files found for this product."],
        )

    changes = sum(1 for e in entries if e.compared_to_previous == "changed")

    # longest run of identical top summary
    max_same = 1
    run = 1
    for i in range(1, n):
        if entries[i].top_recommended_action == entries[i - 1].top_recommended_action:
            run += 1
            max_same = max(max_same, run)
        else:
            run = 1

    intents = [intent_bucket(e.top_intent, e.top_recommended_action) for e in entries]
    bucket_flips = sum(1 for i in range(1, len(intents)) if intents[i] != intents[i - 1])
    triple_osc = _oscillation_triples(intents)

    confs = [e.confidence for e in entries if e.confidence is not None]
    cmin = min(confs) if confs else None
    cmax = max(confs) if confs else None
    crange = (cmax - cmin) if cmin is not None and cmax is not None else None

    change_rate = changes / max(1, n - 1)
    flip_rate = bucket_flips / max(1, n - 1)

    conf_jitter = 0.0
    if crange is not None:
        conf_jitter = min(0.35, crange * 0.8)

    esc_bonus = 0.0
    esc_titles: list[str] = []
    if repo_root is not None:
        esc_titles = escalation_repeated_titles(repo_root, product_id)
        esc_bonus = min(0.2, 0.04 * len(esc_titles))

    churn_raw = (
        change_rate * 0.42
        + flip_rate * 0.22
        + min(0.2, triple_osc * 0.07)
        + conf_jitter
        + esc_bonus
    )
    churn_score = round(max(0.0, min(1.0, churn_raw)), 3)
    # Longer runs of the same top action slightly increase stability.
    streak_bonus = min(0.12, (max_same / max(1, n)) * 0.08)
    stability_score = round(
        max(0.0, min(1.0, 1.0 - min(1.0, churn_raw) + streak_bonus)),
        3,
    )

    lines: list[str] = []
    lines.append(f"Top recommended action changed {changes} times in {n} recorded run(s).")
    if max_same >= 2:
        lines.append(f"Same top action text repeated for up to {max_same} consecutive run(s).")
    for bucket in ("hold", "deprecate", "improve"):
        streak = _max_consecutive_bucket(entries, bucket)
        if streak >= 3:
            lines.append(
                f"'{bucket}'-class recommendation persisted for {streak} consecutive run(s)."
            )
    if triple_osc > 0:
        lines.append(f"Intent bucket oscillation patterns detected ({triple_osc} flip-backs).")
    if crange is not None and crange >= 0.25:
        lines.append(f"Top confidence spanned {crange:.2f} (min={cmin:.2f}, max={cmax:.2f}).")
    if esc_titles:
        lines.append(
            "Repeated escalation titles: " + "; ".join(t[:80] for t in esc_titles[:4])
        )

    streaks = {
        "hold": _max_consecutive_bucket(entries, "hold"),
        "deprecate": _max_consecutive_bucket(entries, "deprecate"),
        "improve": _max_consecutive_bucket(entries, "improve"),
    }

    return DecisionChurnReport(
        product_id=product_id,
        run_count=n,
        top_action_change_count=changes,
        max_consecutive_same_top=max_same,
        intent_bucket_flips=bucket_flips,
        confidence_min=cmin,
        confidence_max=cmax,
        confidence_range=crange,
        churn_score=churn_score,
        stability_score=stability_score,
        summary_lines=lines,
        intent_streaks=streaks,
        escalation_theme_hits=esc_titles,
    )


def churn_report_to_jsonable(r: DecisionChurnReport) -> dict[str, Any]:
    return {
        "product_id": r.product_id,
        "run_count": r.run_count,
        "top_action_change_count": r.top_action_change_count,
        "max_consecutive_same_top": r.max_consecutive_same_top,
        "intent_bucket_flips": r.intent_bucket_flips,
        "confidence_min": r.confidence_min,
        "confidence_max": r.confidence_max,
        "confidence_range": r.confidence_range,
        "churn_score": r.churn_score,
        "stability_score": r.stability_score,
        "summary_lines": list(r.summary_lines),
        "intent_streaks": dict(r.intent_streaks),
        "escalation_theme_hits": list(r.escalation_theme_hits),
    }
