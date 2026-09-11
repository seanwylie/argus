"""Global advisor definitions and per-product overrides."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from argus.advisors.models import Advisor, AdvisorArchetype


def global_advisors() -> list[Advisor]:
    """Default advisor set for any product (weights sum arbitrarily; consensus normalizes)."""
    return [
        Advisor(
            id="adv.finance",
            archetype=AdvisorArchetype.FINANCE,
            description="Cost, runway, and unit economics framing.",
            prompt_template=(
                "Given product context:\n{context}\n"
                "Assess financial sustainability and spending tradeoffs."
            ),
            weight=1.0,
        ),
        Advisor(
            id="adv.investor",
            archetype=AdvisorArchetype.INVESTOR,
            description="Portfolio fit, growth vs risk, milestone clarity.",
            prompt_template=(
                "Given product context:\n{context}\n"
                "Evaluate as a portfolio bet: upside, dilution of focus, exit path realism."
            ),
            weight=1.0,
        ),
        Advisor(
            id="adv.technical",
            archetype=AdvisorArchetype.TECHNICAL,
            description="Reliability, maintainability, and delivery risk.",
            prompt_template=(
                "Given product context:\n{context}\n"
                "Stress technical debt, operational risk, and observability gaps."
            ),
            weight=1.1,
        ),
        Advisor(
            id="adv.marketing",
            archetype=AdvisorArchetype.MARKETING,
            description="Positioning, funnel, and message-market fit.",
            prompt_template=(
                "Given product context:\n{context}\n"
                "Comment on acquisition narrative and measurable traction signals."
            ),
            weight=0.9,
        ),
        Advisor(
            id="adv.product",
            archetype=AdvisorArchetype.PRODUCT,
            description="User value, roadmap coherence, and scope discipline.",
            prompt_template=(
                "Given product context:\n{context}\n"
                "Assess product strategy fit to lifecycle stage and user outcomes."
            ),
            weight=1.0,
        ),
        Advisor(
            id="adv.creative",
            archetype=AdvisorArchetype.CREATIVE,
            description="Brand, UX differentiation, and narrative.",
            prompt_template=(
                "Given product context:\n{context}\n"
                "Note differentiation and creative risk without blocking pragmatism."
            ),
            weight=0.8,
        ),
    ]


def _product_advisors_path(repo_root: Path, product_id: str) -> Path:
    return repo_root.resolve() / "products" / product_id / "advisors.json"


def _load_product_override(repo_root: Path, product_id: str) -> dict[str, Any] | None:
    p = _product_advisors_path(repo_root, product_id)
    if not p.is_file():
        return None
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else None
    except (OSError, json.JSONDecodeError, TypeError):
        return None


def resolve_advisors(repo_root: Path, product_id: str) -> list[Advisor]:
    """
    Merge global advisors with optional ``products/<id>/advisors.json``:

    - ``include_only``: list of advisor ids to keep (if present, others dropped).
    - ``exclude``: list of advisor ids to remove.
    - ``weight_overrides``: advisor id -> float.
    """
    base = list(global_advisors())
    ov = _load_product_override(repo_root, product_id)
    if not ov:
        return base

    if isinstance(ov.get("include_only"), list) and ov["include_only"]:
        allowed = {str(x) for x in ov["include_only"]}
        base = [a for a in base if a.id in allowed]

    if isinstance(ov.get("exclude"), list):
        drop = {str(x) for x in ov["exclude"]}
        base = [a for a in base if a.id not in drop]

    wov = ov.get("weight_overrides")
    if isinstance(wov, dict):
        out: list[Advisor] = []
        for a in base:
            w = wov.get(a.id)
            if w is None:
                out.append(a)
            else:
                try:
                    nw = float(w)
                except (TypeError, ValueError):
                    out.append(a)
                    continue
                out.append(
                    Advisor(
                        id=a.id,
                        archetype=a.archetype,
                        description=a.description,
                        prompt_template=a.prompt_template,
                        weight=max(0.0, nw),
                    )
                )
        base = out

    return base
