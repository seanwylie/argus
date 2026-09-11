"""
Merge human input into findings, lifecycle assessment, decision scores, and planning signals.

Default behavior is weighted nudging; ``structured_fields.hard_override`` (or ``mode: "hard"``)
selects stronger application for supported knobs.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from argus.core.models.decision import DecisionCandidate
from argus.core.models.enums import FindingKind
from argus.core.models.finding import Finding
from argus.input.models import HumanInput
from argus.input.store import list_inputs
from argus.lifecycle.model import LifecycleAssessment


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _parse_expiry(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def is_active(inp: HumanInput, *, now: datetime | None = None) -> bool:
    if not inp.expires_at:
        return True
    n = now or _now_utc()
    try:
        return n <= _parse_expiry(inp.expires_at)
    except ValueError:
        return False


def merged_inputs_for_product(repo_root: Path, product_id: str) -> list[HumanInput]:
    """Active global inputs plus active inputs for ``product_id``, deterministic order."""
    out: list[HumanInput] = []
    for inp in list_inputs(repo_root):
        if not is_active(inp):
            continue
        if inp.scope == "global":
            out.append(inp)
        elif inp.scope == "product" and inp.product_id == product_id:
            out.append(inp)
    out.sort(key=lambda x: (x.created_at, x.id))
    return out


def _is_hard(inp: HumanInput) -> bool:
    sf = inp.structured_fields
    return bool(sf.get("hard_override") is True or sf.get("mode") == "hard")


def _weight(inp: HumanInput) -> float:
    return max(0.0, min(3.0, float(inp.priority_weight)))


def _wants_no_kill(inp: HumanInput) -> bool:
    sf = inp.structured_fields
    if sf.get("no_kill") is True or sf.get("suppress_kill") is True:
        return True
    t = (inp.content or "").lower()
    return "do not kill" in t or "don't kill" in t or "no kill" in t


def _wants_growth_over_cost(inp: HumanInput) -> bool:
    sf = inp.structured_fields
    if sf.get("prefer_growth_over_cost") is True:
        return True
    t = (inp.content or "").lower()
    return ("growth" in t and "cost" in t) or "growth over cost" in t


def _focus_products_from_input(inp: HumanInput) -> list[str]:
    sf = inp.structured_fields
    raw = sf.get("focus_products") or sf.get("focus_product_ids")
    if isinstance(raw, list):
        return [str(x).strip() for x in raw if str(x).strip()]
    if isinstance(raw, str) and raw.strip():
        return [x.strip() for x in raw.split(",") if x.strip()]
    one = sf.get("focus_product")
    if isinstance(one, str) and one.strip():
        return [one.strip()]
    return []


def _lifecycle_deltas(inp: HumanInput) -> dict[str, float]:
    sf = inp.structured_fields
    out: dict[str, float] = {}
    target = sf.get("target_lifecycle_stage") or sf.get("target_stage")
    if isinstance(target, str):
        t = target.lower().strip()
        if t in ("grow", "growth"):
            out["move_forward"] = out.get("move_forward", 0.0) + 0.05 * _weight(inp)
        elif t in ("validate", "validation"):
            out["move_forward"] = out.get("move_forward", 0.0) + 0.04 * _weight(inp)
        elif t in ("maintain", "hold"):
            out["hold"] = out.get("hold", 0.0) + 0.05 * _weight(inp)
    return out


def _clip_conf(x: float | None) -> float:
    base = 0.55 if x is None else float(x)
    return max(0.05, min(0.99, base))


def _source_confidence(f: Finding) -> float:
    """Baseline rule confidence; preserved across re-applies when human input changes."""
    ev = f.evidence or {}
    hi = ev.get("human_input")
    if isinstance(hi, dict) and hi.get("source_confidence") is not None:
        return _clip_conf(float(hi["source_confidence"]))
    return _clip_conf(f.confidence)


def apply_to_findings(
    repo_root: Path | None,
    product_id: str,
    findings: list[Finding],
) -> list[Finding]:
    """Adjust confidence (interpretation) and annotate evidence; no new findings invented."""
    if repo_root is None or not findings:
        return findings
    inputs = merged_inputs_for_product(repo_root, product_id)
    if not inputs:
        return findings

    hard_no = any(_is_hard(i) and _wants_no_kill(i) for i in inputs)
    soft_no_strength = sum(_weight(i) for i in inputs if _wants_no_kill(i) and not _is_hard(i))
    growth_strength = sum(_weight(i) for i in inputs if _wants_growth_over_cost(i) and not _is_hard(i))

    out: list[Finding] = []
    for f in findings:
        source = _source_confidence(f)
        mult = 1.0
        kinds_deprec = (FindingKind.DEPRECATION_CANDIDATE, FindingKind.INACTIVITY, FindingKind.RETENTION_PROBLEM)
        if f.kind in kinds_deprec:
            if hard_no:
                mult *= 0.15
            elif soft_no_strength > 0:
                mult *= max(0.5, 1.0 - 0.12 * min(1.5, soft_no_strength))
        if f.kind == FindingKind.GROWTH_OPPORTUNITY and growth_strength > 0:
            mult *= 1.0 + 0.06 * min(1.5, growth_strength)
        if f.kind == FindingKind.COST_RISK and growth_strength > 0:
            mult *= max(0.55, 1.0 - 0.06 * min(1.5, growth_strength))
        new_conf = _clip_conf(source * mult)
        ev = dict(f.evidence)
        ev["human_input"] = {
            "applied": True,
            "source_confidence": source,
            "inputs": [i.id for i in inputs],
            "confidence_multiplier": round(mult, 4),
        }
        out.append(
            replace(
                f,
                confidence=new_conf,
                evidence=ev,
            )
        )
    return out


def _kill_candidate_from_scores(
    move_forward: float,
    kill: float,
    *,
    kill_min: float = 0.65,
    mf_max: float = 0.38,
) -> bool:
    return kill >= kill_min and move_forward <= mf_max


def apply_to_assessment(
    repo_root: Path | None,
    product_id: str,
    assessment: LifecycleAssessment,
    *,
    kill_thresholds: tuple[float, float] | None = None,
) -> LifecycleAssessment:
    k_min, mf_max = (
        kill_thresholds if kill_thresholds is not None else (0.65, 0.38)
    )

    if repo_root is None:
        return assessment
    inputs = merged_inputs_for_product(repo_root, product_id)
    if not inputs:
        return assessment

    md = dict(assessment.metadata)
    md["human_input"] = {"ids": [i.id for i in inputs]}

    constraints: dict[str, Any] = {}
    for i in inputs:
        if i.type != "constraint_update":
            continue
        for k in ("aws_monthly_cap_usd", "total_monthly_cap_usd", "monthly_spend_cap_usd"):
            if k in i.structured_fields:
                constraints[k] = i.structured_fields[k]
    if constraints:
        md["human_input_constraints"] = constraints

    hard_no = any(_is_hard(i) and _wants_no_kill(i) for i in inputs)
    soft_no = sum(_weight(i) for i in inputs if _wants_no_kill(i) and not _is_hard(i))

    mf = assessment.move_forward
    hold = assessment.hold
    imp = assessment.improve
    dep = assessment.deprecate
    kill = assessment.kill

    for inp in inputs:
        if inp.type == "lifecycle_override" or inp.structured_fields.get("target_lifecycle_stage"):
            for k, d in _lifecycle_deltas(inp).items():
                if k == "move_forward":
                    mf = min(1.0, mf + d)
                elif k == "hold":
                    hold = min(1.0, hold + d)

    if hard_no:
        kill = min(kill, 0.12)
        dep = dep * 0.75
        mf = max(mf, 0.22)
        md["human_input"]["no_kill"] = "hard"
    elif soft_no > 0:
        damp = max(0.45, 1.0 / (1.0 + 0.35 * min(soft_no, 2.5)))
        kill *= damp
        dep *= 0.85 + 0.15 * (1.0 - min(1.0, soft_no / 2.0))
        md["human_input"]["no_kill"] = f"soft:{soft_no:.2f}"

    mf = max(0.0, min(1.0, mf))
    hold = max(0.0, min(1.0, hold))
    imp = max(0.0, min(1.0, imp))
    dep = max(0.0, min(1.0, dep))
    kill = max(0.0, min(1.0, kill))

    kc = _kill_candidate_from_scores(mf, kill, kill_min=k_min, mf_max=mf_max)
    if hard_no:
        kc = False
    elif soft_no >= 1.0:
        kc = False

    return replace(
        assessment,
        move_forward=mf,
        hold=hold,
        improve=imp,
        deprecate=dep,
        kill=kill,
        kill_candidate=kc,
        metadata=md,
    )


def _intent_str(c: DecisionCandidate) -> str | None:
    raw = (c.metadata or {}).get("intent")
    return str(raw) if raw else None


def _focus_boost_map(inputs: list[HumanInput]) -> dict[str, float]:
    m: dict[str, float] = {}
    for inp in inputs:
        if inp.type not in ("priority_override", "strategy"):
            continue
        w = _weight(inp)
        boost = 1.0 + 0.12 * min(w, 2.0)
        if _is_hard(inp):
            boost = 1.0 + 0.22 * min(w, 2.0)
        for pid in _focus_products_from_input(inp):
            m[pid] = max(m.get(pid, 1.0), boost)
    return m


def apply_to_candidates(
    repo_root: Path | None,
    product_id: str,
    candidates: list[DecisionCandidate],
) -> list[DecisionCandidate]:
    if repo_root is None or not candidates:
        return candidates
    inputs = merged_inputs_for_product(repo_root, product_id)
    if not inputs:
        return candidates

    hard_no = any(_is_hard(i) and _wants_no_kill(i) for i in inputs)
    soft_no = sum(_weight(i) for i in inputs if _wants_no_kill(i) and not _is_hard(i))
    growth = sum(_weight(i) for i in inputs if _wants_growth_over_cost(i) and not _is_hard(i))
    focus_local = _focus_boost_map(inputs)

    killish = frozenset({"kill_product", "deprecate_product", "escalate_to_human"})
    growthish = frozenset({"launch_experiment", "improve_product"})

    out: list[DecisionCandidate] = []
    for c in candidates:
        score = float(c.priority_score or 0.0)
        intent = _intent_str(c)
        md = dict(c.metadata or {})

        pid_boost = focus_local.get(product_id)
        if pid_boost is not None:
            score *= pid_boost
            md["human_input_focus_boost"] = pid_boost

        if intent in killish:
            if hard_no:
                score *= 0.08
                md["human_input_no_kill"] = "hard"
            elif soft_no > 0:
                score *= max(0.2, 1.0 - 0.25 * min(soft_no, 2.5))
                md["human_input_no_kill"] = f"soft:{soft_no:.2f}"

        if intent == "reduce_cost" and growth > 0:
            score *= max(0.35, 1.0 - 0.1 * min(growth, 2.0))
            md["human_input_growth_bias"] = True
        if intent in growthish and growth > 0:
            score *= 1.0 + 0.06 * min(growth, 2.0)
            md["human_input_growth_bias"] = True

        out.append(replace(c, priority_score=round(score, 2), metadata=md))

    out.sort(key=lambda x: (x.priority_score or 0.0), reverse=True)
    return out


def apply_planning_priority_nudge(
    repo_root: Path | None,
    product_id: str,
    base_priority_score: float,
) -> float:
    """Used by weekly planning to nudge portfolio rank for focus products."""
    if repo_root is None:
        return base_priority_score
    inputs = merged_inputs_for_product(repo_root, product_id)
    if not inputs:
        return base_priority_score
    m = _focus_boost_map(inputs)
    boost = m.get(product_id, 1.0)
    return round(float(base_priority_score) * boost, 4)


def planning_suppress_kill_flag(
    repo_root: Path | None,
    product_id: str,
    kill_candidate: bool,
) -> bool:
    """Soft/hard no-kill inputs suppress kill-style planning flags when active."""
    if repo_root is None or not kill_candidate:
        return kill_candidate
    inputs = merged_inputs_for_product(repo_root, product_id)
    hard_no = any(_is_hard(i) and _wants_no_kill(i) for i in inputs)
    if hard_no:
        return False
    soft_no = sum(_weight(i) for i in inputs if _wants_no_kill(i) and not _is_hard(i))
    if soft_no >= 1.0:
        return False
    return kill_candidate
