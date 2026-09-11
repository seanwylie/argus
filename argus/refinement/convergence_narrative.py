"""Deterministic human-readable convergence narrative (display-only; does not affect gating)."""

from __future__ import annotations

from collections import Counter
from typing import Any

from argus.refinement.convergence import WEIGHTED_CONF_THRESHOLD
from argus.refinement.models import (
    ConvergenceResult,
    ReviewSynthesis,
    ReviewVerdict,
    StakeholderReview,
)

_REASON_LEGIBLE: dict[str, str] = {
    "quality_gate_pass": "Grounded gate passed (pass ratio and weighted confidence thresholds met).",
    "continue_refining": "Continue refining: grounded metrics below thresholds or blocking issues remain.",
    "max_rounds_exhausted": "Maximum refinement rounds reached without passing the grounded gate.",
    "blocking_threshold": "Blocking failures exceeded the rejection threshold.",
    "outsider_pressure:human_review": "Outsider pressure policy: route to human review.",
    "outsider_human_review_policy": "Grounded gate passed; outsider policy requires human review.",
    "doctrine_fail:human_review": "Doctrine failure: routed to human review (product spec).",
    "legacy_all_reviews_treated_grounded": "No council_mode on reviews; all counted as grounded for this round.",
}


def _legible_reason(code: str) -> str:
    """Stable explanation for a machine ``reasons`` entry (display-only)."""
    if code in _REASON_LEGIBLE:
        return _REASON_LEGIBLE[code]
    if code.startswith("outsider_fail_count:"):
        n = code.split(":", 1)[1]
        return (
            f"Outsider FAIL verdicts (count {n}); advisory only and does not override grounded gate math."
        )
    if code.startswith("required_fail:"):
        seat = code.split(":", 1)[1]
        return f"Required grounded seat {seat!r} reported FAIL with blocking."
    if code.startswith("hard_block_stakeholder:"):
        seat = code.split(":", 1)[1]
        return f"Hard-blocking seat {seat!r} reported FAIL."
    return f"Reason code: {code}"


def _reason_stakeholder_hint(code: str) -> str | None:
    """Optional parse of stakeholder id from reason codes (for cross-links)."""
    for prefix in ("required_fail:", "hard_block_stakeholder:"):
        if code.startswith(prefix):
            return code.split(":", 1)[1].strip() or None
    return None


def _is_outsider_review(r: StakeholderReview) -> bool:
    return (r.council_mode or "") == "outsider"


def build_convergence_narrative(
    *,
    reviews: list[StakeholderReview],
    convergence: ConvergenceResult,
    synthesis: ReviewSynthesis,
) -> dict[str, Any]:
    """
    Summarize verdict patterns, confidence, objection categories, and synthesis echo.

    Mirrors grounded vs outsider the same way as convergence (outsider excluded from gate math).
    """
    grounded = [r for r in reviews if not _is_outsider_review(r)]
    if not grounded:
        grounded = list(reviews)

    g_pass = sum(1 for r in grounded if r.verdict == ReviewVerdict.PASS)
    g_concern = sum(1 for r in grounded if r.verdict == ReviewVerdict.CONCERN)
    g_fail = sum(1 for r in grounded if r.verdict == ReviewVerdict.FAIL)
    grounded_verdict_counts = {"pass": g_pass, "concern": g_concern, "fail": g_fail}

    grounded_stakeholders = [
        {
            "stakeholder_type": r.stakeholder_type.value,
            "verdict": r.verdict.value,
            "confidence_score": r.confidence_score,
            "blocking": r.blocking,
        }
        for r in sorted(grounded, key=lambda x: x.stakeholder_type.value)
    ]

    cat_counter: Counter[str] = Counter()
    for r in grounded:
        for c in r.objection_categories:
            cat_counter[c.value] += 1
    objection_categories_grounded = [
        {"category": k, "count": v}
        for k, v in sorted(cat_counter.items(), key=lambda kv: (-kv[1], kv[0]))
    ][:12]

    outsider = [r for r in reviews if _is_outsider_review(r)]
    oc = Counter(r.verdict.value for r in outsider)
    outsider_verdict_counts = (
        {"pass": oc.get("pass", 0), "concern": oc.get("concern", 0), "fail": oc.get("fail", 0)}
        if outsider
        else {}
    )

    all_grounded_pass = all(r.verdict == ReviewVerdict.PASS for r in grounded)
    any_concern_in_round = any(r.verdict == ReviewVerdict.CONCERN for r in reviews)
    meets_gate = float(convergence.weighted_confidence) >= WEIGHTED_CONF_THRESHOLD

    themes_head = list(synthesis.themes[:5])

    reasons_detail = [{"code": c, "legible": _legible_reason(c)} for c in convergence.reasons]
    reason_hints: list[dict[str, str]] = []
    for c in convergence.reasons:
        h = _reason_stakeholder_hint(c)
        if h is not None:
            reason_hints.append({"code": c, "grounded_stakeholder_hint": h})

    parts = [
        f"status={convergence.final_status.value}",
        f"converged={convergence.converged}",
        f"grounded_pass={g_pass}/{len(grounded)}",
    ]
    if g_concern:
        parts.append(f"grounded_concern={g_concern}")
    if g_fail:
        parts.append(f"grounded_fail={g_fail}")
    parts.append(f"weighted_confidence={convergence.weighted_confidence}>={WEIGHTED_CONF_THRESHOLD}={meets_gate}")
    parts.append(f"pass_ratio={convergence.pass_ratio}")
    if outsider:
        parts.append(f"outsider_verdicts={dict(sorted(oc.items()))}")
    parts.append(f"synthesis_signal={synthesis.overall_signal}")
    summary_line = "; ".join(parts)

    inspection_lines = [
        f"Outcome: {convergence.final_status.value} (converged={convergence.converged})",
        (
            f"Grounded gate: pass_ratio={convergence.pass_ratio}, "
            f"weighted_confidence={convergence.weighted_confidence} "
            f"(threshold={WEIGHTED_CONF_THRESHOLD}, met={meets_gate})"
        ),
    ]
    inspection_lines.extend(f"- {d['legible']}" for d in reasons_detail)
    if grounded_stakeholders:
        inspection_lines.append("Grounded seats (verdict / confidence / blocking):")
        for row in grounded_stakeholders:
            inspection_lines.append(
                f"  - {row['stakeholder_type']}: {row['verdict']} "
                f"(confidence={row['confidence_score']}, blocking={row['blocking']})"
            )

    return {
        "summary_line": summary_line,
        "grounded_verdict_counts": grounded_verdict_counts,
        "grounded_stakeholders": grounded_stakeholders,
        "objection_categories_grounded": objection_categories_grounded,
        "outsider_verdict_counts": outsider_verdict_counts,
        "flags": {
            "all_grounded_pass": all_grounded_pass,
            "any_concern_in_round": any_concern_in_round,
            "meets_weighted_confidence_gate": meets_gate,
        },
        "synthesis_echo": {
            "overall_signal": synthesis.overall_signal,
            "themes_head": themes_head,
        },
        "reasons_detail": reasons_detail,
        "reason_stakeholder_hints": reason_hints,
        "inspection_lines": inspection_lines,
    }
