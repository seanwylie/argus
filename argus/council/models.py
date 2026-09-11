"""Council abstractions: grounded vs outsider, backends, policies (extends refinement)."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from argus.refinement.models import ArtifactType, StakeholderType


class CouncilMode(StrEnum):
    """How this member relates to repo truth."""

    GROUNDED = "grounded"
    OUTSIDER = "outsider"
    DETERMINISTIC = "deterministic"


class BackendType(StrEnum):
    """Execution backend (explicit; never silent cross-over)."""

    CURSOR = "cursor"
    OPENAI = "openai"
    DETERMINISTIC = "deterministic"


class ReasoningDepth(StrEnum):
    LIGHT = "light"
    STANDARD = "standard"
    DEEP = "deep"


class ContextPolicy(StrEnum):
    """What context slice the member receives (packet assembly key)."""

    FULL_GROUNDED = "full_grounded"
    COMPACT_GROUNDED = "compact_grounded"
    OUTSIDER_PITCH_ONLY = "outsider_pitch_only"
    OUTSIDER_MARKET_ONLY = "outsider_market_only"
    IMPLEMENTATION_GROUNDED = "implementation_grounded"


class ReviewDimension(StrEnum):
    """Structured review axes (optional scores in review payload)."""

    GROUNDED_FEASIBILITY = "grounded_feasibility"
    GROUNDED_DOCTRINE_FIT = "grounded_doctrine_fit"
    GROUNDED_TECHNICAL_CONFIDENCE = "grounded_technical_confidence"
    OUTSIDER_INTEREST = "outsider_interest"
    OUTSIDER_CLARITY = "outsider_clarity"
    OUTSIDER_NOVELTY_PRESSURE = "outsider_novelty_pressure"


@dataclass(frozen=True)
class OutsiderInfluencePolicy:
    """
    Outsiders never approve feasibility alone; they may nudge human review or narrative risk.

    See docs/councils.md for rules.
    """

    outsider_can_hard_approve: bool = False
    outsider_can_overrule_grounded_fail: bool = False
    human_review_if_outsider_blocking_count: int | None = None
    """If set, require human review when this many outsider reviews are FAIL (grounded gate separate)."""

    outsider_contributes_to_approved_with_risks_narrative: bool = True


@dataclass(frozen=True)
class CouncilMemberProfile:
    """One seat on the council for a refinement round."""

    member_id: str
    stakeholder_type: StakeholderType
    council_mode: CouncilMode
    backend_type: BackendType
    reasoning_depth: ReasoningDepth
    required: bool
    can_block: bool
    weight: float
    context_policy: ContextPolicy
    counts_toward_convergence_gate: bool
    """If False (outsiders), verdicts do not drive deterministic approval/reject math."""
    dimensions_hint: tuple[ReviewDimension, ...] = ()
    notes: str = ""


@dataclass
class CouncilProfile:
    """Routable council for an artifact class + review phase."""

    artifact_type: ArtifactType
    phase: str
    members: tuple[CouncilMemberProfile, ...]
    required_roles: tuple[StakeholderType, ...] = ()
    max_rounds: int = 4
    convergence_policy: str = "grounded_first_default"
    outsider_influence: OutsiderInfluencePolicy = field(default_factory=OutsiderInfluencePolicy)
    schema: str = "argus.council_profile.v1"

    def grounded_count(self) -> int:
        return sum(1 for m in self.members if m.council_mode == CouncilMode.GROUNDED)

    def outsider_count(self) -> int:
        return sum(1 for m in self.members if m.council_mode == CouncilMode.OUTSIDER)


def profile_to_jsonable(p: CouncilMemberProfile) -> dict[str, Any]:
    return {
        "member_id": p.member_id,
        "stakeholder_type": p.stakeholder_type.value,
        "council_mode": p.council_mode.value,
        "backend_type": p.backend_type.value,
        "reasoning_depth": p.reasoning_depth.value,
        "required": p.required,
        "can_block": p.can_block,
        "weight": p.weight,
        "context_policy": p.context_policy.value,
        "counts_toward_convergence_gate": p.counts_toward_convergence_gate,
        "dimensions_hint": [d.value for d in p.dimensions_hint],
        "notes": p.notes,
    }


def profile_from_dict(raw: dict[str, Any]) -> CouncilMemberProfile | None:
    try:
        st = StakeholderType(str(raw["stakeholder_type"]))
        dims_raw = raw.get("dimensions_hint") or []
        dr: list[ReviewDimension] = []
        for x in dims_raw:
            try:
                dr.append(ReviewDimension(str(x)))
            except ValueError:
                continue
        dims = tuple(dr)
        return CouncilMemberProfile(
            member_id=str(raw.get("member_id") or f"mem_{st.value}"),
            stakeholder_type=st,
            council_mode=CouncilMode(str(raw.get("council_mode", "grounded"))),
            backend_type=BackendType(str(raw.get("backend_type", "deterministic"))),
            reasoning_depth=ReasoningDepth(str(raw.get("reasoning_depth", "standard"))),
            required=bool(raw.get("required", True)),
            can_block=bool(raw.get("can_block", True)),
            weight=float(raw.get("weight", 0.8)),
            context_policy=ContextPolicy(str(raw.get("context_policy", "full_grounded"))),
            counts_toward_convergence_gate=bool(raw.get("counts_toward_convergence_gate", True)),
            dimensions_hint=dims,
            notes=str(raw.get("notes", "")),
        )
    except (KeyError, TypeError, ValueError):
        return None
