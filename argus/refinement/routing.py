"""Route artifact types to stakeholder councils (weights, required vs optional, hard-block rules)."""

from __future__ import annotations

from argus.council.routing import council_entries_for_refinement
from argus.refinement.models import ArtifactType, StakeholderType

# (stakeholder, weight, required)
CouncilEntry = tuple[StakeholderType, float, bool]


def council_for(artifact_type: ArtifactType) -> list[CouncilEntry]:
    """Return ordered council from routed ``argus.council`` profiles (grounded + outsider)."""
    return council_entries_for_refinement(artifact_type)


def can_hard_block(artifact_type: ArtifactType, stakeholder: StakeholderType) -> bool:
    """
    Whether a FAIL from this stakeholder may force rejection / human review.

    Creative concerns on ideas are non-blocking by default; doctrine is always a potential hard gate.
    Outsider lenses never hard-block feasibility.
    """
    if stakeholder in (
        StakeholderType.INVESTOR,
        StakeholderType.MARKETER,
        StakeholderType.CUSTOMER_PROXY,
    ):
        return False
    if stakeholder == StakeholderType.DOCTRINE:
        return artifact_type in (ArtifactType.PRODUCT_SPEC, ArtifactType.IDEA)
    if stakeholder == StakeholderType.BONES:
        return artifact_type == ArtifactType.IMPLEMENTATION_PLAN
    if stakeholder == StakeholderType.ARCHITECTURE:
        return artifact_type == ArtifactType.IMPLEMENTATION_PLAN
    if artifact_type == ArtifactType.IDEA and stakeholder == StakeholderType.CREATIVE:
        return False
    if artifact_type == ArtifactType.IDEA and stakeholder == StakeholderType.FINANCE:
        return True
    if artifact_type == ArtifactType.PRODUCT_SPEC and stakeholder == StakeholderType.UX:
        return True
    if artifact_type == ArtifactType.IMPLEMENTATION_PLAN and stakeholder == StakeholderType.TECHNICAL:
        return True
    return stakeholder in (
        StakeholderType.FINANCE,
        StakeholderType.PRODUCT,
        StakeholderType.GROWTH,
    )


def refinement_question(artifact_type: ArtifactType) -> str:
    if artifact_type == ArtifactType.IDEA:
        return "Should this be pursued?"
    if artifact_type == ArtifactType.PRODUCT_SPEC:
        return "Is this the right product shape?"
    if artifact_type == ArtifactType.IMPLEMENTATION_PLAN:
        return "Is this build plan sound?"
    return ""
