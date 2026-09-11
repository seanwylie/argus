"""Deterministic lifecycle scoring from stage + findings."""

from __future__ import annotations

from argus.core.models.enums import FindingKind, LifecycleStage, SeverityLevel
from argus.core.models.finding import Finding
from argus.core.models.product import ProductNode
from argus.lifecycle.model import LifecycleAssessment


def _clip(x: float) -> float:
    return max(0.0, min(1.0, x))


def _severity_weight(sev: SeverityLevel) -> float:
    return {
        SeverityLevel.CRITICAL: 1.0,
        SeverityLevel.HIGH: 0.75,
        SeverityLevel.MEDIUM: 0.5,
        SeverityLevel.LOW: 0.3,
        SeverityLevel.INFO: 0.15,
    }.get(sev, 0.4)


# Stage priors: rough default posture before evidence (sum not required to be 1).
_STAGE_PRIOR: dict[LifecycleStage, dict[str, float]] = {
    LifecycleStage.IDEA: {
        "move_forward": 0.35,
        "hold": 0.30,
        "improve": 0.40,
        "deprecate": 0.12,
        "kill": 0.05,
    },
    LifecycleStage.BUILD: {
        "move_forward": 0.45,
        "hold": 0.22,
        "improve": 0.42,
        "deprecate": 0.10,
        "kill": 0.04,
    },
    LifecycleStage.VALIDATE: {
        "move_forward": 0.50,
        "hold": 0.20,
        "improve": 0.38,
        "deprecate": 0.10,
        "kill": 0.05,
    },
    LifecycleStage.GROW: {
        "move_forward": 0.55,
        "hold": 0.18,
        "improve": 0.30,
        "deprecate": 0.12,
        "kill": 0.06,
    },
    LifecycleStage.MAINTAIN: {
        "move_forward": 0.25,
        "hold": 0.40,
        "improve": 0.28,
        "deprecate": 0.22,
        "kill": 0.10,
    },
    LifecycleStage.DECLINE: {
        "move_forward": 0.12,
        "hold": 0.30,
        "improve": 0.18,
        "deprecate": 0.45,
        "kill": 0.28,
    },
    LifecycleStage.KILL: {
        "move_forward": 0.02,
        "hold": 0.15,
        "improve": 0.05,
        "deprecate": 0.35,
        "kill": 0.92,
    },
}


def assess_lifecycle(
    product: ProductNode,
    findings: list[Finding],
    *,
    kill_score_min: float = 0.65,
    move_forward_max: float = 0.38,
) -> LifecycleAssessment:
    """
    Combine lifecycle stage priors with weighted finding kinds to produce five scores.

    This is transparent bookkeeping: each finding nudges relevant dimensions by
    ``0.12 * severity_weight(severity)`` (clipped), with kind-specific routing.
    """
    pid = product.id
    raw_stage = product.lifecycle.stage
    stage = LifecycleStage(raw_stage) if isinstance(raw_stage, str) else raw_stage
    base = dict(_STAGE_PRIOR[stage])
    reasoning: dict[str, list[str]] = {
        "move_forward": [f"stage_prior({stage.value})"],
        "hold": [f"stage_prior({stage.value})"],
        "improve": [f"stage_prior({stage.value})"],
        "deprecate": [f"stage_prior({stage.value})"],
        "kill": [f"stage_prior({stage.value})"],
    }

    for f in findings:
        w = _severity_weight(f.severity)
        delta = 0.12 * w
        k = f.kind

        if k == FindingKind.LAUNCH_CANDIDATE:
            base["move_forward"] += delta * 1.4
            reasoning["move_forward"].append(f"finding:{f.kind.value}")
        elif k == FindingKind.STRUCTURAL_READINESS:
            base["move_forward"] += delta * 0.75
            base["improve"] += delta * 0.55
            reasoning["move_forward"].append(f"finding:{f.kind.value}")
        elif k == FindingKind.VALIDATION_READINESS:
            base["move_forward"] += delta * 1.1
            base["improve"] += delta * 0.45
            reasoning["move_forward"].append(f"finding:{f.kind.value}")
        elif k == FindingKind.VALIDATION_EVIDENCE_GAP:
            base["improve"] += delta * 1.05
            base["hold"] += delta * 0.5
            base["move_forward"] -= delta * 0.35
            reasoning["improve"].append(f"finding:{f.kind.value}")
        elif k == FindingKind.GROWTH_OPPORTUNITY:
            base["move_forward"] += delta * 1.1
            reasoning["move_forward"].append(f"finding:{f.kind.value}")
        elif k == FindingKind.COST_RISK:
            base["improve"] += delta * 1.2
            base["move_forward"] -= delta * 0.5
            reasoning["improve"].append(f"finding:{f.kind.value}")
        elif k == FindingKind.DEPRECATION_CANDIDATE:
            base["deprecate"] += delta * 1.5
            base["kill"] += delta * 0.6
            base["move_forward"] -= delta * 0.6
            reasoning["deprecate"].append(f"finding:{f.kind.value}")
        elif k == FindingKind.INACTIVITY:
            base["improve"] += delta * 0.9
            base["hold"] += delta * 0.5
            base["kill"] += delta * 0.35
            reasoning["improve"].append(f"finding:{f.kind.value}")
        elif k == FindingKind.QUALITY_ISSUE:
            base["improve"] += delta * 1.1
            base["move_forward"] -= delta * 0.25
            reasoning["improve"].append(f"finding:{f.kind.value}")
        elif k == FindingKind.RELIABILITY_PROBLEM:
            base["improve"] += delta * 1.0
            base["hold"] += delta * 0.4
            reasoning["improve"].append(f"finding:{f.kind.value}")
        elif k == FindingKind.RETENTION_PROBLEM:
            base["improve"] += delta * 0.9
            base["deprecate"] += delta * 0.3
            reasoning["improve"].append(f"finding:{f.kind.value}")
        elif k == FindingKind.DOCTRINE_VIOLATION:
            base["hold"] += delta * 0.85
            base["improve"] += delta * 0.55
            base["move_forward"] -= delta * 0.35
            reasoning["hold"].append(f"finding:{f.kind.value}")
        elif k == FindingKind.CURRENT_OPPORTUNITY:
            base["move_forward"] += delta * 1.15
            reasoning["move_forward"].append(f"finding:{f.kind.value}")
        elif k == FindingKind.TRENDING_TOPIC:
            base["move_forward"] += delta * 1.05
            reasoning["move_forward"].append(f"finding:{f.kind.value}")
        elif k == FindingKind.URGENCY_WINDOW:
            base["move_forward"] += delta * 0.95
            base["hold"] += delta * 0.35
            reasoning["move_forward"].append(f"finding:{f.kind.value}")
        elif k == FindingKind.CURRENT_RISK:
            base["improve"] += delta * 1.15
            base["hold"] += delta * 0.45
            base["move_forward"] -= delta * 0.4
            reasoning["improve"].append(f"finding:{f.kind.value}")
        elif k == FindingKind.STALE_CONTEXT:
            base["improve"] += delta * 0.85
            base["hold"] += delta * 0.4
            reasoning["improve"].append(f"finding:{f.kind.value}")
        elif k == FindingKind.NO_RECENT_EVIDENCE:
            base["improve"] += delta * 0.75
            base["hold"] += delta * 0.55
            reasoning["improve"].append(f"finding:{f.kind.value}")

    for key in base:
        base[key] = _clip(base[key])

    kill_candidate = base["kill"] >= kill_score_min and base["move_forward"] <= move_forward_max

    reason_out = {k: "; ".join(v[:6]) for k, v in reasoning.items()}
    md = {
        "model": "argus.lifecycle.v1",
        "kill_score_min": kill_score_min,
        "move_forward_max": move_forward_max,
    }
    return LifecycleAssessment(
        product_id=pid,
        stage=stage,
        move_forward=base["move_forward"],
        hold=base["hold"],
        improve=base["improve"],
        deprecate=base["deprecate"],
        kill=base["kill"],
        reasoning=reason_out,
        kill_candidate=kill_candidate,
        metadata=md,
    )
