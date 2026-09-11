"""Validate raw ``doctrine.yaml`` mappings before building :class:`ProductDoctrine`."""

from __future__ import annotations

from typing import Any

from argus.doctrine.models import DoctrineConstraints, DoctrineScoring, ProductDoctrine

_EXPECTED_SCHEMA = "argus.doctrine.v1"


class DoctrineValidationError(ValueError):
    """Raised when doctrine YAML is structurally invalid."""


def _req_str(d: dict[str, Any], key: str) -> str:
    v = d.get(key)
    if v is None or not isinstance(v, str) or not str(v).strip():
        raise DoctrineValidationError(f"doctrine.{key} must be a non-empty string")
    return str(v).strip()


def _opt_str(d: dict[str, Any], key: str) -> str | None:
    v = d.get(key)
    if v is None:
        return None
    if not isinstance(v, str):
        raise DoctrineValidationError(f"doctrine.{key} must be a string when set")
    s = v.strip()
    return s or None


def _opt_float(name: str, v: Any) -> float | None:
    if v is None:
        return None
    if isinstance(v, bool):
        raise DoctrineValidationError(f"{name} must be a number, not bool")
    try:
        x = float(v)
    except (TypeError, ValueError) as e:
        raise DoctrineValidationError(f"{name} must be a number") from e
    if x < 0:
        raise DoctrineValidationError(f"{name} must be >= 0")
    return x


def validate_doctrine_raw(raw: dict[str, Any]) -> ProductDoctrine:
    """Parse a top-level mapping from ``doctrine.yaml`` into :class:`ProductDoctrine`."""
    if not isinstance(raw, dict):
        raise DoctrineValidationError("doctrine root must be a mapping")
    schema = _req_str(raw, "schema")
    if schema != _EXPECTED_SCHEMA:
        raise DoctrineValidationError(
            f"unsupported doctrine schema {schema!r}; expected {_EXPECTED_SCHEMA!r}"
        )
    summary = _opt_str(raw, "summary")
    principles_raw = raw.get("principles")
    principles: tuple[str, ...] = ()
    if principles_raw is not None:
        if not isinstance(principles_raw, list):
            raise DoctrineValidationError("principles must be a list of strings when set")
        out_p: list[str] = []
        for i, p in enumerate(principles_raw):
            if not isinstance(p, str) or not p.strip():
                raise DoctrineValidationError(f"principles[{i}] must be a non-empty string")
            out_p.append(p.strip())
        principles = tuple(out_p)

    cons_raw = raw.get("constraints")
    if cons_raw is None:
        cons_raw = {}
    if not isinstance(cons_raw, dict):
        raise DoctrineValidationError("constraints must be a mapping when set")
    max_cost = _opt_float("constraints.max_monthly_cost_usd", cons_raw.get("max_monthly_cost_usd"))
    kill_review = cons_raw.get("require_human_review_when_kill_candidate")
    if kill_review is None:
        kill_review = False
    if not isinstance(kill_review, bool):
        raise DoctrineValidationError("constraints.require_human_review_when_kill_candidate must be a boolean")

    score_raw = raw.get("scoring")
    if score_raw is None:
        score_raw = {}
    if not isinstance(score_raw, dict):
        raise DoctrineValidationError("scoring must be a mapping when set")
    intent_mult: dict[str, float] = {}
    im = score_raw.get("intent_priority_multiplier")
    if im is not None:
        if not isinstance(im, dict):
            raise DoctrineValidationError("scoring.intent_priority_multiplier must be a mapping")
        for k, v in im.items():
            if not isinstance(k, str) or not k.strip():
                raise DoctrineValidationError("intent_priority_multiplier keys must be non-empty strings")
            mult = _opt_float(f"scoring.intent_priority_multiplier[{k!r}]", v)
            if mult is None:
                continue
            if mult <= 0:
                raise DoctrineValidationError(f"multiplier for {k!r} must be > 0")
            intent_mult[k.strip()] = mult
    boost = score_raw.get("experiment_score_boost")
    if boost is None:
        exp_boost = 0.0
    else:
        eb = _opt_float("scoring.experiment_score_boost", boost)
        exp_boost = float(eb) if eb is not None else 0.0
    if exp_boost > 0.5:
        raise DoctrineValidationError("scoring.experiment_score_boost must be <= 0.5")

    return ProductDoctrine(
        schema_id=schema,
        summary=summary,
        principles=principles,
        constraints=DoctrineConstraints(
            max_monthly_cost_usd=max_cost,
            require_human_review_when_kill_candidate=kill_review,
        ),
        scoring=DoctrineScoring(
            intent_priority_multiplier=intent_mult,
            experiment_score_boost=exp_boost,
        ),
    )
