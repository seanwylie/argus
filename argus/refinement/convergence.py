"""Deterministic convergence: pass ratios, weighted confidence, blocking rules, round limits."""

from __future__ import annotations

from argus.council.profiles import default_council_profile
from argus.refinement.models import (
    ArtifactType,
    ConvergenceResult,
    ReviewVerdict,
    SessionStatus,
    StakeholderReview,
)
from argus.refinement.routing import CouncilEntry, can_hard_block

WEIGHTED_CONF_THRESHOLD = 0.72


def _is_outsider_review(r: StakeholderReview) -> bool:
    return (r.council_mode or "") == "outsider"


def evaluate_convergence(
    *,
    session_id: str,
    round_number: int,
    max_rounds: int,
    artifact_type: ArtifactType,
    reviews: list[StakeholderReview],
    council: list[CouncilEntry],
) -> ConvergenceResult:
    """
    Decide whether to stop, approve, reject, require human review, or continue refining.

    Grounded reviewers drive approval math; outsiders supply advisory pressure only.
    Rules are explainable; LLM does not decide outcomes.
    """
    reasons: list[str] = []
    outsider_policy = default_council_profile(artifact_type).outsider_influence

    weight_map = {st: w for st, w, _ in council}
    required_set = {st for st, _, req in council if req}

    grounded_reviews = [r for r in reviews if not _is_outsider_review(r)]
    if not grounded_reviews:
        grounded_reviews = list(reviews)
        reasons.append("legacy_all_reviews_treated_grounded")

    blocking_fail_weight = 0
    hard_block = False
    weighted_sum = 0.0
    weight_den = 0.0
    passes = 0

    for r in grounded_reviews:
        w = weight_map.get(r.stakeholder_type, 0.7)
        weight_den += w
        if r.verdict == ReviewVerdict.PASS:
            passes += 1
            weighted_sum += w * float(r.confidence_score)
        elif r.verdict == ReviewVerdict.CONCERN:
            weighted_sum += w * float(r.confidence_score) * 0.72
        else:
            weighted_sum += w * float(r.confidence_score) * 0.35

        if r.verdict == ReviewVerdict.FAIL and (
            r.blocking or can_hard_block(artifact_type, r.stakeholder_type)
        ):
            blocking_fail_weight += 1
            if can_hard_block(artifact_type, r.stakeholder_type):
                hard_block = True
                reasons.append(f"hard_block_stakeholder:{r.stakeholder_type.value}")

    pass_ratio = passes / max(1, len(grounded_reviews))
    weighted_conf = weighted_sum / weight_den if weight_den > 0 else 0.0

    for r in grounded_reviews:
        if r.stakeholder_type in required_set and r.verdict == ReviewVerdict.FAIL and r.blocking:
            reasons.append(f"required_fail:{r.stakeholder_type.value}")

    outsider_fail_count = sum(
        1 for r in reviews if _is_outsider_review(r) and r.verdict == ReviewVerdict.FAIL
    )
    if outsider_fail_count:
        reasons.append(f"outsider_fail_count:{outsider_fail_count}")

    # Product spec + doctrine hard fail -> human review (not auto-reject)
    if (
        artifact_type == ArtifactType.PRODUCT_SPEC
        and hard_block
        and any(r.stakeholder_type.value == "doctrine" and r.verdict == ReviewVerdict.FAIL for r in grounded_reviews)
    ):
        return ConvergenceResult(
            session_id=session_id,
            round_number=round_number,
            converged=True,
            final_status=SessionStatus.HUMAN_REVIEW_REQUIRED,
            pass_ratio=round(pass_ratio, 4),
            blocking_count=blocking_fail_weight,
            weighted_confidence=round(weighted_conf, 4),
            reasons=reasons + ["doctrine_fail:human_review"],
        )

    if blocking_fail_weight >= 2 or (hard_block and blocking_fail_weight > 0 and artifact_type != ArtifactType.IDEA):
        return ConvergenceResult(
            session_id=session_id,
            round_number=round_number,
            converged=True,
            final_status=SessionStatus.REJECTED,
            pass_ratio=round(pass_ratio, 4),
            blocking_count=blocking_fail_weight,
            weighted_confidence=round(weighted_conf, 4),
            reasons=reasons + ["blocking_threshold"],
        )

    # Outsider cannot overrule grounded failure — if grounded path fails, we never approve below.
    outsider_human_review = False
    lim = outsider_policy.human_review_if_outsider_blocking_count
    if (
        lim is not None
        and outsider_fail_count >= lim
        and pass_ratio >= 0.85
        and weighted_conf >= WEIGHTED_CONF_THRESHOLD
        and blocking_fail_weight == 0
    ):
        outsider_human_review = True
        reasons.append("outsider_pressure:human_review")

    if (
        pass_ratio >= 0.85
        and weighted_conf >= WEIGHTED_CONF_THRESHOLD
        and blocking_fail_weight == 0
    ):
        if outsider_human_review:
            return ConvergenceResult(
                session_id=session_id,
                round_number=round_number,
                converged=True,
                final_status=SessionStatus.HUMAN_REVIEW_REQUIRED,
                pass_ratio=round(pass_ratio, 4),
                blocking_count=0,
                weighted_confidence=round(weighted_conf, 4),
                reasons=reasons + ["outsider_human_review_policy"],
            )
        if any(r.verdict == ReviewVerdict.CONCERN for r in reviews):
            final = SessionStatus.APPROVED_WITH_RISKS
        else:
            final = SessionStatus.APPROVED
        return ConvergenceResult(
            session_id=session_id,
            round_number=round_number,
            converged=True,
            final_status=final,
            pass_ratio=round(pass_ratio, 4),
            blocking_count=0,
            weighted_confidence=round(weighted_conf, 4),
            reasons=reasons + ["quality_gate_pass"],
        )

    if round_number >= max_rounds - 1:
        return ConvergenceResult(
            session_id=session_id,
            round_number=round_number,
            converged=True,
            final_status=SessionStatus.HUMAN_REVIEW_REQUIRED,
            pass_ratio=round(pass_ratio, 4),
            blocking_count=blocking_fail_weight,
            weighted_confidence=round(weighted_conf, 4),
            reasons=reasons + ["max_rounds_exhausted"],
        )

    return ConvergenceResult(
        session_id=session_id,
        round_number=round_number,
        converged=False,
        final_status=SessionStatus.REFINING,
        pass_ratio=round(pass_ratio, 4),
        blocking_count=blocking_fail_weight,
        weighted_confidence=round(weighted_conf, 4),
        reasons=reasons + ["continue_refining"],
    )
