"""Persist decision context assessments under ``runs/decision_assessment/``."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from argus.core.serialize import dumps_json
from argus.decision_assessment.evaluate import evaluate_decision_context
from argus.decision_assessment.models import DecisionContextAssessment


def assessment_dir(repo_root: Path) -> Path:
    return repo_root.resolve() / "runs" / "decision_assessment"


def latest_path(repo_root: Path, product_id: str) -> Path:
    return assessment_dir(repo_root) / "latest" / f"{product_id}.json"


def save_assessment(repo_root: Path, assessment: DecisionContextAssessment) -> Path:
    root = repo_root.resolve()
    p = latest_path(root, assessment.product_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(dumps_json(assessment.to_jsonable()) + "\n", encoding="utf-8")
    return p


def load_latest_assessment(repo_root: Path, product_id: str) -> DecisionContextAssessment | None:
    p = latest_path(repo_root.resolve(), product_id)
    if not p.is_file():
        return None
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict):
        return None
    return assessment_from_dict(raw)


def assessment_from_dict(d: dict[str, Any]) -> DecisionContextAssessment:
    from argus.decision_assessment.models import (
        ConfidenceBucket,
        EscalationRecommendation,
        FactorContribution,
    )

    def _opt_float(x: Any) -> float | None:
        if x is None:
            return None
        try:
            return float(x)
        except (TypeError, ValueError):
            return None

    def _fc(x: dict[str, Any]) -> FactorContribution:
        return FactorContribution(
            factor_id=str(x.get("factor_id", "")),
            label=str(x.get("label", "")),
            contribution=float(x.get("contribution", 0.0)),
            direction=str(x.get("direction", "")),
        )

    def _fclist(key: str) -> list[FactorContribution]:
        raw = d.get(key)
        if not isinstance(raw, list):
            return []
        out: list[FactorContribution] = []
        for item in raw:
            if isinstance(item, dict):
                out.append(_fc(item))
        return out

    cb = str(d.get("confidence_bucket", "medium"))
    try:
        bucket = ConfidenceBucket(cb)
    except ValueError:
        bucket = ConfidenceBucket.MEDIUM

    er = str(d.get("escalation_recommendation", "gather_data"))
    try:
        esc = EscalationRecommendation(er)
    except ValueError:
        esc = EscalationRecommendation.GATHER_DATA

    return DecisionContextAssessment(
        product_id=str(d.get("product_id", "")),
        assessed_at_utc=str(d.get("assessed_at_utc", "")),
        decision_id=str(d["decision_id"]) if d.get("decision_id") else None,
        confidence_score=float(d.get("confidence_score", 0.0)),
        confidence_bucket=bucket,
        uncertainty_score=float(d.get("uncertainty_score", 0.0)),
        uncertainty_factors=_fclist("uncertainty_factors"),
        risk_score=float(d.get("risk_score", 0.0)),
        risk_factors=_fclist("risk_factors"),
        recurrence_risk_score=float(d.get("recurrence_risk_score", 0.0)),
        momentum_score=float(d.get("momentum_score", 0.0)),
        friction_score=float(d.get("friction_score", 0.0)),
        escalation_pressure=float(d.get("escalation_pressure", 0.0)),
        escalation_recommendation=esc,
        evidence_density_score=float(d.get("evidence_density_score", 0.0)),
        exploratory_action_recommended=bool(d.get("exploratory_action_recommended")),
        exploratory_action_reason=str(d.get("exploratory_action_reason", "")),
        exploratory_guardrails=[str(x) for x in (d.get("exploratory_guardrails") or []) if str(x).strip()],
        rationale=str(d.get("rationale", "")),
        confidence_factors=_fclist("confidence_factors"),
        momentum_factors=_fclist("momentum_factors"),
        friction_factors=_fclist("friction_factors"),
        recurrence_factors=_fclist("recurrence_factors"),
        advisor_alignment_score=_opt_float(d.get("advisor_alignment_score")),
        advisor_conflict_flag=bool(d.get("advisor_conflict_flag", False)),
        advisor_summary=str(d.get("advisor_summary", "")),
    )


def evaluate_and_save(repo_root: Path, product_id: str) -> DecisionContextAssessment:
    a = evaluate_decision_context(repo_root, product_id)
    save_assessment(repo_root, a)
    return a
