"""Map council ContextPolicy → assemble_context_bundle purpose (reuse packet assembly)."""

from __future__ import annotations

from pathlib import Path

from argus.context.assemble import assemble_context_bundle
from argus.context.caps import ContextCaps
from argus.context.formatting import bundle_to_refinement_prompt_addon
from argus.context.purposes import ContextPurpose
from argus.council.models import ContextPolicy
from argus.refinement.models import ArtifactDraft


def purpose_for_policy(policy: ContextPolicy) -> ContextPurpose:
    if policy == ContextPolicy.FULL_GROUNDED:
        return ContextPurpose.REFINEMENT_GROUNDED
    if policy == ContextPolicy.COMPACT_GROUNDED:
        return ContextPurpose.IDEA_GENERATION
    if policy == ContextPolicy.IMPLEMENTATION_GROUNDED:
        return ContextPurpose.COUNCIL_IMPLEMENTATION_GROUNDED
    if policy == ContextPolicy.OUTSIDER_PITCH_ONLY:
        return ContextPurpose.COUNCIL_OUTSIDER_PITCH
    if policy == ContextPolicy.OUTSIDER_MARKET_ONLY:
        return ContextPurpose.COUNCIL_OUTSIDER_MARKET
    return ContextPurpose.REFINEMENT_GROUNDED


def build_packet_addon_for_member(
    repo_root: Path,
    product_id: str | None,
    draft: ArtifactDraft,
    policy: ContextPolicy,
    *,
    caps: ContextCaps | None = None,
) -> str:
    """Deterministic prompt addon string for one council member."""
    purpose = purpose_for_policy(policy)
    bundle = assemble_context_bundle(
        repo_root,
        purpose,
        product_id,
        draft=draft,
        caps=caps,
    )
    return bundle_to_refinement_prompt_addon(bundle)
