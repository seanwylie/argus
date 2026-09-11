"""Resolve council profiles and legacy refinement council entries."""

from __future__ import annotations

from typing import Any

from argus.council.models import (
    BackendType,
    CouncilMemberProfile,
    CouncilProfile,
    profile_from_dict,
)
from argus.council.profiles import default_council_profile
from argus.refinement.models import ArtifactType, StakeholderType

# Mirrors refinement.routing.CouncilEntry without importing that module (avoid cycles).
CouncilEntryTuple = tuple[StakeholderType, float, bool]


def member_profiles_for_artifact(artifact_type: ArtifactType) -> list[CouncilMemberProfile]:
    return list(default_council_profile(artifact_type).members)


def council_entries_for_refinement(artifact_type: ArtifactType) -> list[CouncilEntryTuple]:
    """(stakeholder, weight, required) tuples for refinement.session compatibility."""
    return [(m.stakeholder_type, m.weight, m.required) for m in member_profiles_for_artifact(artifact_type)]


def council_profile_for_artifact(artifact_type: ArtifactType) -> CouncilProfile:
    return default_council_profile(artifact_type)


def validate_council_profile(cp: CouncilProfile) -> list[str]:
    """Return human-readable errors (empty if ok)."""
    errs: list[str] = []
    if not cp.members:
        errs.append("council has no members")
    seen: set[str] = set()
    for m in cp.members:
        if m.member_id in seen:
            errs.append(f"duplicate member_id {m.member_id!r}")
        seen.add(m.member_id)
        if m.backend_type not in BackendType:
            errs.append(f"unknown backend {m.backend_type!r} for {m.member_id}")
    grounded = [m for m in cp.members if m.counts_toward_convergence_gate]
    if not grounded:
        errs.append("no grounded members — convergence would be outsider-only (invalid)")
    # implementation plans should stay grounded-heavy; warn-style handled in doctor
    if cp.artifact_type == ArtifactType.IMPLEMENTATION_PLAN:
        outsiders = [m for m in cp.members if not m.counts_toward_convergence_gate]
        if outsiders and not cp.outsider_influence.outsider_can_hard_approve:
            pass  # allowed if explicitly added later
    return errs


def profiles_from_session_meta(meta: dict[str, Any] | None) -> list[CouncilMemberProfile] | None:
    raw = (meta or {}).get("council_profiles")
    if not isinstance(raw, list):
        return None
    out: list[CouncilMemberProfile] = []
    for x in raw:
        if not isinstance(x, dict):
            continue
        p = profile_from_dict(x)
        if p:
            out.append(p)
    return out if out else None
