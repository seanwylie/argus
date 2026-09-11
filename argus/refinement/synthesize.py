"""Merge stakeholder reviews into a single synthesis artifact."""

from __future__ import annotations

from datetime import datetime, timezone

from argus.refinement.models import ReviewSynthesis, ReviewVerdict, StakeholderReview


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _source_label(r: StakeholderReview) -> str:
    return "outsider" if (r.council_mode or "") == "outsider" else "grounded"


def synthesize_reviews(
    session_id: str,
    draft_id: str,
    round_number: int,
    reviews: list[StakeholderReview],
) -> ReviewSynthesis:
    themes: list[str] = []
    theme_items: list[dict[str, str]] = []
    blocking_issues: list[str] = []
    non_blocking_issues: list[str] = []
    required_changes: list[str] = []
    optional_improvements: list[str] = []

    for r in reviews:
        src = _source_label(r)
        label = f"[{src}:{r.stakeholder_type.value}]"
        if r.verdict == ReviewVerdict.FAIL or (r.blocking and r.verdict != ReviewVerdict.PASS):
            blocking_issues.extend(f"{label} {o}" for o in r.objections[:8])
            required_changes.extend(f"{label} {s}" for s in r.suggestions[:6])
            for o in r.objections[:4]:
                theme_items.append(
                    {"source": src, "stakeholder": r.stakeholder_type.value, "issue": str(o)[:500]}
                )
        elif r.verdict == ReviewVerdict.CONCERN:
            non_blocking_issues.extend(f"{label} {o}" for o in r.objections[:6])
            optional_improvements.extend(f"{label} {s}" for s in r.suggestions[:4])
            for o in r.objections[:3]:
                theme_items.append(
                    {"source": src, "stakeholder": r.stakeholder_type.value, "issue": str(o)[:500]}
                )
        else:
            optional_improvements.extend(f"{label} {s}" for s in r.suggestions[:2])

    # Themes: dedupe stakeholder labels + verdict distribution
    vc = {v.value: sum(1 for x in reviews if x.verdict == v) for v in ReviewVerdict}
    themes.append(
        "verdict_mix: "
        + ", ".join(f"{k}={vc.get(k, 0)}" for k in ("pass", "concern", "fail"))
    )
    gc = sum(1 for x in reviews if _source_label(x) == "grounded")
    oc = len(reviews) - gc
    themes.append(f"reviewer_mix: grounded={gc}, outsider={oc}")
    if blocking_issues:
        themes.append("blocking_objections_present")
    if non_blocking_issues:
        themes.append("non_blocking_concerns_present")

    overall = "mixed"
    if not blocking_issues and not non_blocking_issues:
        overall = "clean"
    elif blocking_issues:
        overall = "blocked"
    elif non_blocking_issues:
        overall = "concerns_only"

    return ReviewSynthesis(
        session_id=session_id,
        draft_id=draft_id,
        round_number=round_number,
        themes=themes[:24],
        blocking_issues=blocking_issues[:48],
        non_blocking_issues=non_blocking_issues[:48],
        required_changes=required_changes[:48],
        optional_improvements=optional_improvements[:48],
        overall_signal=overall,
        created_at_utc=_now(),
        theme_items=theme_items[:64],
    )
