"""Default council profiles by artifact type (grounded vs outsider composition)."""

from __future__ import annotations

from argus.council.models import (
    BackendType,
    ContextPolicy,
    CouncilMemberProfile,
    CouncilMode,
    CouncilProfile,
    OutsiderInfluencePolicy,
    ReasoningDepth,
    ReviewDimension,
)
from argus.refinement.models import ArtifactType, StakeholderType


def _g(
    mid: str,
    st: StakeholderType,
    *,
    weight: float,
    required: bool,
    policy: ContextPolicy,
    depth: ReasoningDepth = ReasoningDepth.STANDARD,
    backend: BackendType = BackendType.CURSOR,
    dims: tuple[ReviewDimension, ...] = (),
    notes: str = "",
) -> CouncilMemberProfile:
    return CouncilMemberProfile(
        member_id=mid,
        stakeholder_type=st,
        council_mode=CouncilMode.GROUNDED,
        backend_type=backend,
        reasoning_depth=depth,
        required=required,
        can_block=True,
        weight=weight,
        context_policy=policy,
        counts_toward_convergence_gate=True,
        dimensions_hint=dims,
        notes=notes,
    )


def _o(
    mid: str,
    st: StakeholderType,
    *,
    weight: float,
    required: bool,
    policy: ContextPolicy,
    backend: BackendType = BackendType.OPENAI,
    dims: tuple[ReviewDimension, ...] = (),
) -> CouncilMemberProfile:
    return CouncilMemberProfile(
        member_id=mid,
        stakeholder_type=st,
        council_mode=CouncilMode.OUTSIDER,
        backend_type=backend,
        reasoning_depth=ReasoningDepth.STANDARD,
        required=required,
        can_block=False,
        weight=weight,
        context_policy=policy,
        counts_toward_convergence_gate=False,
        dimensions_hint=dims,
        notes="outsider: limited context; not authoritative for repo truth",
    )


def profile_for_idea() -> CouncilProfile:
    """Grounded feasibility + outsider interest/clarity pressure."""
    members = (
        _g(
            "grounded_product",
            StakeholderType.PRODUCT,
            weight=1.0,
            required=True,
            policy=ContextPolicy.COMPACT_GROUNDED,
            dims=(ReviewDimension.GROUNDED_FEASIBILITY,),
        ),
        _g(
            "grounded_finance",
            StakeholderType.FINANCE,
            weight=1.0,
            required=True,
            policy=ContextPolicy.COMPACT_GROUNDED,
            dims=(ReviewDimension.GROUNDED_FEASIBILITY,),
        ),
        _g(
            "grounded_technical",
            StakeholderType.TECHNICAL,
            weight=0.75,
            required=True,
            policy=ContextPolicy.COMPACT_GROUNDED,
            depth=ReasoningDepth.LIGHT,
            dims=(ReviewDimension.GROUNDED_TECHNICAL_CONFIDENCE,),
        ),
        _o(
            "outsider_investor",
            StakeholderType.INVESTOR,
            weight=0.85,
            required=True,
            policy=ContextPolicy.OUTSIDER_PITCH_ONLY,
            dims=(ReviewDimension.OUTSIDER_INTEREST,),
        ),
        _o(
            "outsider_marketer",
            StakeholderType.MARKETER,
            weight=0.85,
            required=True,
            policy=ContextPolicy.OUTSIDER_MARKET_ONLY,
            dims=(ReviewDimension.OUTSIDER_CLARITY,),
        ),
        _o(
            "outsider_creative",
            StakeholderType.CREATIVE,
            weight=0.8,
            required=False,
            policy=ContextPolicy.OUTSIDER_PITCH_ONLY,
            dims=(ReviewDimension.OUTSIDER_NOVELTY_PRESSURE,),
        ),
    )
    return CouncilProfile(
        artifact_type=ArtifactType.IDEA,
        phase="review",
        members=members,
        required_roles=(StakeholderType.PRODUCT, StakeholderType.FINANCE, StakeholderType.TECHNICAL),
        outsider_influence=OutsiderInfluencePolicy(
            human_review_if_outsider_blocking_count=None,
        ),
    )


def profile_for_product_spec() -> CouncilProfile:
    """Shape + doctrine fit + selective outsider voice."""
    members = (
        _g(
            "grounded_product",
            StakeholderType.PRODUCT,
            weight=1.0,
            required=True,
            policy=ContextPolicy.FULL_GROUNDED,
            dims=(ReviewDimension.GROUNDED_FEASIBILITY,),
        ),
        _g(
            "grounded_doctrine",
            StakeholderType.DOCTRINE,
            weight=1.0,
            required=True,
            policy=ContextPolicy.FULL_GROUNDED,
            dims=(ReviewDimension.GROUNDED_DOCTRINE_FIT,),
        ),
        _g(
            "grounded_ux",
            StakeholderType.UX,
            weight=1.0,
            required=True,
            policy=ContextPolicy.FULL_GROUNDED,
            dims=(ReviewDimension.GROUNDED_FEASIBILITY,),
        ),
        _g(
            "grounded_technical",
            StakeholderType.TECHNICAL,
            weight=0.75,
            required=False,
            policy=ContextPolicy.COMPACT_GROUNDED,
            depth=ReasoningDepth.LIGHT,
            dims=(ReviewDimension.GROUNDED_TECHNICAL_CONFIDENCE,),
        ),
        _g(
            "grounded_finance",
            StakeholderType.FINANCE,
            weight=0.75,
            required=False,
            policy=ContextPolicy.COMPACT_GROUNDED,
        ),
        _o(
            "outsider_marketer",
            StakeholderType.MARKETER,
            weight=0.75,
            required=False,
            policy=ContextPolicy.OUTSIDER_MARKET_ONLY,
        ),
        _o(
            "outsider_customer",
            StakeholderType.CUSTOMER_PROXY,
            weight=0.75,
            required=False,
            policy=ContextPolicy.OUTSIDER_PITCH_ONLY,
        ),
    )
    return CouncilProfile(
        artifact_type=ArtifactType.PRODUCT_SPEC,
        phase="review",
        members=members,
        required_roles=(StakeholderType.PRODUCT, StakeholderType.DOCTRINE, StakeholderType.UX),
        outsider_influence=OutsiderInfluencePolicy(),
    )


def profile_for_implementation_plan() -> CouncilProfile:
    """Grounded-only by default; outsiders omitted (optional extension via config later)."""
    members = (
        _g(
            "grounded_technical",
            StakeholderType.TECHNICAL,
            weight=1.0,
            required=True,
            policy=ContextPolicy.IMPLEMENTATION_GROUNDED,
            dims=(ReviewDimension.GROUNDED_TECHNICAL_CONFIDENCE,),
        ),
        _g(
            "grounded_architecture",
            StakeholderType.ARCHITECTURE,
            weight=1.0,
            required=True,
            policy=ContextPolicy.IMPLEMENTATION_GROUNDED,
        ),
        _g(
            "grounded_bones",
            StakeholderType.BONES,
            weight=1.0,
            required=True,
            policy=ContextPolicy.IMPLEMENTATION_GROUNDED,
        ),
        _g(
            "grounded_product",
            StakeholderType.PRODUCT,
            weight=0.9,
            required=True,
            policy=ContextPolicy.IMPLEMENTATION_GROUNDED,
        ),
    )
    return CouncilProfile(
        artifact_type=ArtifactType.IMPLEMENTATION_PLAN,
        phase="review",
        members=members,
        required_roles=(
            StakeholderType.TECHNICAL,
            StakeholderType.ARCHITECTURE,
            StakeholderType.BONES,
            StakeholderType.PRODUCT,
        ),
        outsider_influence=OutsiderInfluencePolicy(),
    )


def default_council_profile(artifact_type: ArtifactType) -> CouncilProfile:
    if artifact_type == ArtifactType.IDEA:
        return profile_for_idea()
    if artifact_type == ArtifactType.PRODUCT_SPEC:
        return profile_for_product_spec()
    if artifact_type == ArtifactType.IMPLEMENTATION_PLAN:
        return profile_for_implementation_plan()
    raise ValueError(f"unsupported artifact_type: {artifact_type!r}")
