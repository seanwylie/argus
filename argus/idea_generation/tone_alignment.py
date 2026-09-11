"""
Align idea ranking with decision context and findings severity (no text rewrites).

Uses ``runs/decisions/latest/<product>.json`` (``decision_context``) and findings severity
to down-rank ideas whose wording implies high-confidence rollout or urgency when the
governance context is cautious or findings are low-severity.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from argus.core.models.enums import SeverityLevel
from argus.core.models.finding import Finding
from argus.decision.persistence import load_latest_product_decisions
from argus.findings.persistence import load_latest_findings
from argus.idea_generation.models import Idea

_SEVERITY_ORDINAL: dict[str, int] = {
    SeverityLevel.INFO.value: 0,
    SeverityLevel.LOW.value: 1,
    SeverityLevel.MEDIUM.value: 2,
    SeverityLevel.HIGH.value: 3,
    SeverityLevel.CRITICAL.value: 4,
}

# High-confidence / rollout language (cautious decisions → down-rank).
_AGGRESSIVE_ROLLOUT_RES = (
    re.compile(r"\b(full[\s-]?scale|at scale|roll[\s-]?out|production\s+ready|ship\s+to\s+production)\b", re.I),
    re.compile(r"\b(enterprise[\s-]?wide|company[\s-]?wide|org[\s-]?wide|nationwide)\b", re.I),
    re.compile(r"\b(commit\s+fully|all[\s-]?in|double\s+down|scale\s+fast|10x|hypergrowth)\b", re.I),
    re.compile(r"\b(launch\s+hard|go\s+big|big\s+bang)\b", re.I),
)

# Urgency / large motion when evidence is only low-severity.
_URGENCY_LARGE_RES = (
    re.compile(r"\b(urgent|asap|immediately|drop\s+everything|this\s+week|ship\s+now)\b", re.I),
    re.compile(r"\b(critical\s+priority|p0|sev[\s-]?0)\b", re.I),
    re.compile(r"\b(massive\s+pivot|large[\s-]?scale|transform\s+the\s+whole|rewrite\s+everything)\b", re.I),
    re.compile(r"\b(escalate\s+to\s+exec|board[\s-]?level)\b", re.I),
)


def _severity_ordinal(sev: str | None) -> int:
    if not sev:
        return 0
    return _SEVERITY_ORDINAL.get(str(sev).lower(), 1)


def max_finding_severity(findings: list[Finding]) -> tuple[str | None, int]:
    """Return (label, ordinal) for the strongest finding in the list."""
    if not findings:
        return None, -1
    best_o = -1
    best_l: str | None = None
    for f in findings:
        v = f.severity.value if hasattr(f.severity, "value") else str(f.severity)
        o = _severity_ordinal(v)
        if o > best_o:
            best_o = o
            best_l = v
    return best_l, best_o


def derive_decision_tone(decision_context: dict[str, Any] | None) -> str:
    """
    Map ``decision_context`` (``DecisionContextAssessment`` JSON) to coarse tone.

    Returns ``cautious`` | ``balanced`` | ``assertive``.
    """
    if not isinstance(decision_context, dict) or not decision_context:
        return "balanced"
    cb = str(decision_context.get("confidence_bucket") or "medium").lower()
    unc = float(decision_context.get("uncertainty_score") or 0.0)
    risk = float(decision_context.get("risk_score") or 0.0)
    esc = str(decision_context.get("escalation_recommendation") or "").lower()
    esc_p = float(decision_context.get("escalation_pressure") or 0.0)
    exploratory = bool(decision_context.get("exploratory_action_recommended"))
    conf = float(decision_context.get("confidence_score") or 0.0)

    cautious_hits = 0
    if cb == "low":
        cautious_hits += 2
    if unc >= 0.48:
        cautious_hits += 1
    if risk >= 0.48:
        cautious_hits += 1
    if esc in ("delay_review", "gather_data", "escalate_human"):
        cautious_hits += 1
    if exploratory:
        cautious_hits += 1
    if esc_p >= 0.52:
        cautious_hits += 1
    if conf < 0.35:
        cautious_hits += 1

    assertive_hits = 0
    if cb == "high" and unc < 0.4 and risk < 0.45:
        assertive_hits += 2
    if esc == "proceed" and esc_p < 0.38 and not exploratory:
        assertive_hits += 1
    if conf >= 0.72 and unc < 0.38:
        assertive_hits += 1

    if cautious_hits >= 3 and assertive_hits < 2:
        return "cautious"
    if assertive_hits >= 2 and cautious_hits < 2:
        return "assertive"
    return "balanced"


def _pattern_hit_count(text: str, patterns: tuple[re.Pattern[str], ...]) -> int:
    return sum(1 for rx in patterns if rx.search(text))


def _aggressive_score(text: str) -> float:
    """0–1 heuristic from rollout / scale language."""
    if not text.strip():
        return 0.0
    hits = _pattern_hit_count(text, _AGGRESSIVE_ROLLOUT_RES)
    return min(1.0, hits * 0.34)


def _urgency_large_score(text: str) -> float:
    """0–1 urgency / large-change heuristic."""
    if not text.strip():
        return 0.0
    hits = _pattern_hit_count(text, _URGENCY_LARGE_RES)
    return min(1.0, hits * 0.32)


def _idea_scan_text(idea: Idea) -> str:
    return f"{idea.title}\n{idea.description}\n{idea.rationale}"


def compute_tone_alignment_for_idea(
    idea: Idea,
    *,
    decision_tone: str,
    top_candidate_confidence: float | None,
    max_finding_severity_label: str | None,
    max_finding_severity_ordinal: int,
    has_findings: bool,
) -> dict[str, Any]:
    """
    Produce ``tone_alignment`` metadata and a ``rank_multiplier`` in (0, 1].

    Does not rewrite title/description; only ranking and tagging.
    """
    text = _idea_scan_text(idea)
    aggressive = _aggressive_score(text)
    urgency = _urgency_large_score(text)

    mult = 1.0
    reasons: list[str] = []

    if decision_tone == "cautious" and aggressive >= 0.34:
        # Strong down-rank: sounds like confident rollout under a cautious decision context.
        pen = 0.45 + 0.4 * (1.0 - min(1.0, aggressive))
        mult *= max(0.35, pen)
        reasons.append("cautious_decision_aggressive_rollout_language")

    if decision_tone == "cautious" and aggressive >= 0.1 and aggressive < 0.34:
        mult *= 0.78
        reasons.append("cautious_decision_mild_rollout_language")

    # Low-severity evidence: do not imply urgency or huge change (only when findings exist).
    low_evidence = (
        has_findings
        and max_finding_severity_ordinal >= 0
        and max_finding_severity_ordinal <= _SEVERITY_ORDINAL[SeverityLevel.LOW.value]
    )
    if low_evidence and urgency >= 0.32:
        mult *= max(0.4, 0.55 - 0.2 * urgency)
        reasons.append("low_severity_findings_urgency_or_large_scale_language")

    if low_evidence and urgency >= 0.1 and urgency < 0.32:
        mult *= 0.82
        reasons.append("low_severity_findings_mild_urgency_language")

    tag = "aligned"
    if reasons:
        tag = "overstated"

    out: dict[str, Any] = {
        "tag": tag,
        "rank_multiplier": round(max(0.05, min(1.0, mult)), 4),
        "decision_tone": decision_tone,
        "signals": {
            "aggressive_rollout_score": round(aggressive, 4),
            "urgency_large_scale_score": round(urgency, 4),
        },
        "context": {
            "top_candidate_confidence": top_candidate_confidence,
            "max_finding_severity": max_finding_severity_label,
            "max_finding_severity_ordinal": max_finding_severity_ordinal,
        },
    }
    if reasons:
        out["reasons"] = reasons
    return out


def load_tone_inputs(
    repo_root: Path,
    product_id: str,
    *,
    findings_rows: list[Finding] | None = None,
) -> dict[str, Any]:
    """Load decisions bundle + findings summary for tone rules."""
    raw = load_latest_product_decisions(repo_root, product_id)
    dc: dict[str, Any] | None = None
    top_conf: float | None = None
    if isinstance(raw, dict):
        dc = raw.get("decision_context")
        if not isinstance(dc, dict):
            dc = None
        cands = raw.get("candidates") or []
        confs: list[float] = []
        if isinstance(cands, list):
            for c in cands:
                if isinstance(c, dict) and c.get("confidence") is not None:
                    try:
                        confs.append(float(c["confidence"]))
                    except (TypeError, ValueError):
                        pass
        if confs:
            top_conf = max(confs)

    findings: list[Finding] = findings_rows if findings_rows is not None else []
    if not findings and product_id:
        fb = load_latest_findings(repo_root, product_id)
        if fb is not None:
            findings = list(fb.findings)

    sev_label, sev_ord = max_finding_severity(findings)
    return {
        "decision_context": dc,
        "decision_tone": derive_decision_tone(dc),
        "top_candidate_confidence": top_conf,
        "max_finding_severity": sev_label,
        "max_finding_severity_ordinal": sev_ord,
        "finding_count": len(findings),
    }


def attach_tone_alignment_to_ideas(
    ideas: list[Idea],
    repo_root: Path,
    product_id: str | None,
    *,
    findings_rows: list[Finding] | None = None,
) -> dict[str, Any]:
    """
    Set ``idea.tone_alignment`` on each idea (rank multiplier + tag). No rewrites.

    When ``product_id`` is None, returns a no-op summary (multiplier 1.0).
    """
    if not product_id:
        for idea in ideas:
            idea.tone_alignment = {
                "tag": "aligned",
                "rank_multiplier": 1.0,
                "decision_tone": "balanced",
                "reason": "no_product_scope",
            }
        return {"applied": False, "reason": "no_product_id"}

    inputs = load_tone_inputs(repo_root, product_id, findings_rows=findings_rows)
    dt = inputs["decision_tone"]
    top_conf = inputs.get("top_candidate_confidence")
    sev_l = inputs.get("max_finding_severity")
    sev_o = int(inputs.get("max_finding_severity_ordinal") or 0)

    overstated_n = 0
    for idea in ideas:
        idea.tone_alignment = compute_tone_alignment_for_idea(
            idea,
            decision_tone=dt,
            top_candidate_confidence=top_conf,
            max_finding_severity_label=sev_l if isinstance(sev_l, str) else None,
            max_finding_severity_ordinal=sev_o,
            has_findings=int(inputs.get("finding_count") or 0) > 0,
        )
        if idea.tone_alignment.get("tag") == "overstated":
            overstated_n += 1

    return {
        "applied": True,
        "decision_tone": dt,
        "top_candidate_confidence": top_conf,
        "max_finding_severity": sev_l,
        "max_finding_severity_ordinal": sev_o,
        "finding_count": inputs.get("finding_count"),
        "ideas_tagged_overstated": overstated_n,
    }
