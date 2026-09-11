"""Orchestrate idea generation from all sources and persist bundles."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from argus.core.models.finding import Finding
from argus.core.serialize import dumps_json, to_jsonable
from argus.idea_generation.audit_gating import apply_audit_to_ideas
from argus.idea_generation.classify import classify_type
from argus.idea_generation.diversity import portfolio_diversity_meta
from argus.idea_generation.generate import (
    generate_from_findings,
    generate_from_signals,
    generate_mutation,
    generate_synthesis,
)
from argus.idea_generation.mechanical_cleanup import mechanical_cleanup
from argus.idea_generation.models import Idea, IdeasBundle, IdeaSource, IdeaType, new_idea_id
from argus.idea_generation.score import finalize_batch_scores, rank_key
from argus.idea_generation.tone_alignment import attach_tone_alignment_to_ideas


def ideas_dir(repo_root: Path) -> Path:
    return repo_root.resolve() / "runs" / "ideas"


def latest_path(repo_root: Path) -> Path:
    return ideas_dir(repo_root) / "latest.json"


def run_pipeline(
    repo_root: Path,
    product_id: str | None,
    *,
    seed: str = "",
    include_mutation: bool = True,
    max_per_source: int | None = None,
    max_ideas: int = 12,
    max_per_diversity_bucket: int = 3,
    advisor_expansion: bool = True,
    max_advisor_expansion_ideas: int = 8,
    llm_idea_expansion: bool = True,
    max_llm_expansion_ideas: int = 24,
    findings_rows: list[Finding] | None = None,
) -> tuple[Path, IdeasBundle]:
    """
    Aggregate ideas from signals, findings, synthesis, and mutation.

    When ``product_id`` is set, advisor **expansion** (critique/variations/risks/monetization)
    attaches to bundle metadata only — advisors do not add net-new primary ideas.

    When ``None``, only portfolio-level synthesis (+ mutation of those ideas) runs.
    """
    root = repo_root.resolve()
    all_ideas: list[Idea] = []

    if product_id:
        all_ideas.extend(generate_from_signals(root, product_id, seed=seed))
        all_ideas.extend(
            generate_from_findings(root, product_id, seed=seed, findings_rows=findings_rows)
        )
        all_ideas.extend(generate_synthesis(root, product_id, seed=seed))
    else:
        all_ideas.extend(generate_synthesis(root, None, seed=seed))

    if max_per_source is not None:
        all_ideas = all_ideas[: max(1, max_per_source * 5)]

    if include_mutation:
        all_ideas.extend(
            generate_mutation(
                root,
                product_id,
                all_ideas,
                seed=seed,
                count=4 if product_id else 2,
            )
        )

    ideas_before_cleanup = len(all_ideas)
    tone_meta = attach_tone_alignment_to_ideas(
        all_ideas,
        root,
        product_id,
        findings_rows=findings_rows,
    )
    # Hard cap default 12, ceiling 15 (see ``max_ideas``).
    effective_max = max(1, min(15, max_ideas))
    deduped, mechanical_meta = mechanical_cleanup(
        all_ideas,
        max_ideas=effective_max,
        max_per_bucket=max_per_diversity_bucket,
    )
    mechanical_meta["tone_alignment"] = tone_meta

    # 2) Classification (exploit / explore / invent) — deterministic, after dedupe
    for idea in deduped:
        idea.type = classify_type(idea.title, idea.description, source=idea.source)

    # 3) Optional LLM expansion (additive metadata only; does not change scoring inputs)
    if product_id and llm_idea_expansion:
        from argus.llm.idea_expander import expand_ideas_if_enabled

        expand_ideas_if_enabled(
            deduped,
            product_id=product_id,
            max_ideas=max_llm_expansion_ideas,
            repo_root=root,
        )

    # 4) Scoring — novelty / diversity (canonical title/description only)
    finalize_batch_scores(deduped, root, seed=seed)

    audit_ideas_meta: dict[str, Any] = apply_audit_to_ideas(deduped, root, product_id)

    doctrine_ideas_meta: dict[str, Any] = {"applied": False}
    if product_id:
        from argus.doctrine.load import load_doctrine_for_product
        from argus.doctrine.scoring import apply_doctrine_to_ideas
        from argus.products.inventory import build_inventory

        inv0 = build_inventory(root)
        if product_id in inv0.valid:
            pr = inv0.valid[product_id].node.product_root
            doc, _ = load_doctrine_for_product(root, product_id, product_root=pr)
            doctrine_ideas_meta = apply_doctrine_to_ideas(deduped, doc)

    deduped.sort(key=rank_key, reverse=True)

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    meta: dict[str, Any] = {
        "seed": seed,
        "timestamp_slug": ts,
        "mechanical_cleanup": mechanical_meta,
        "selection_summary": {
            "ideas_before_mechanical_cleanup": ideas_before_cleanup,
            "ideas_after_mechanical_cleanup": len(deduped),
            "rejected_duplicate_exact": mechanical_meta["rejection_counts"]["rejected_exact_duplicate"],
            "rejected_duplicate_near": mechanical_meta["rejection_counts"]["rejected_near_duplicate"],
            "rejected_bucket_cap": mechanical_meta["rejection_counts"]["rejected_bucket_cap"],
            "rejected_ranked_out": mechanical_meta["rejection_counts"]["rejected_ranked_out"],
            "rejected_below_quality_threshold": mechanical_meta["rejection_counts"].get(
                "rejected_below_quality_threshold", 0
            ),
            "stopped_early_for_quality": (mechanical_meta.get("quality_threshold") or {}).get(
                "stopped_early_for_quality", False
            ),
            "quality_threshold_rule": (mechanical_meta.get("quality_threshold") or {}).get(
                "quality_threshold_rule", ""
            ),
        },
        "portfolio_diversity": portfolio_diversity_meta(deduped),
        "audit_ideas": audit_ideas_meta,
        "doctrine_ideas": doctrine_ideas_meta,
    }
    if product_id and findings_rows is not None:
        surfaced_n = sum(
            1
            for f in findings_rows
            if str(f.id).startswith("exp_surface:")
            or (f.evidence or {}).get("provenance") == "experiment_surfaced"
        )
        meta["ideas_findings_input"] = {
            "mode": "merged_canonical_and_experiment_surfaced",
            "merged_finding_row_count": len(findings_rows),
            "surfaced_findings_used_count": surfaced_n,
        }
    if product_id and advisor_expansion and deduped:
        from argus.advisors.idea_expansion import attach_advisor_expansions
        from argus.llm.client import is_llm_enabled, llm_client_from_env

        adv_llm = bool(is_llm_enabled() and llm_client_from_env())
        meta["advisor_idea_expansion"] = attach_advisor_expansions(
            root,
            product_id,
            deduped,
            max_ideas=max_advisor_expansion_ideas,
            use_llm=adv_llm,
        )

    bundle = IdeasBundle(
        generated_at_utc=datetime.now(timezone.utc).isoformat(),
        repo_root=str(root),
        ideas=deduped,
        product_id=product_id,
        meta=meta,
    )
    path = save_bundle(root, bundle, ts)
    return path, bundle


def save_bundle(repo_root: Path, bundle: IdeasBundle, timestamp_slug: str) -> Path:
    """Write ``runs/ideas/<timestamp>.json`` and ``runs/ideas/latest.json``."""
    root = repo_root.resolve()
    base = ideas_dir(root)
    base.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "schema": bundle.schema,
        "generated_at_utc": bundle.generated_at_utc,
        "repo_root": bundle.repo_root,
        "product_id": bundle.product_id,
        "idea_count": len(bundle.ideas),
        "ideas": [to_jsonable(i) for i in bundle.ideas],
        "meta": bundle.meta,
    }
    text = dumps_json(payload)
    out = base / f"{timestamp_slug}.json"
    out.write_text(text, encoding="utf-8")
    latest_path(root).write_text(text, encoding="utf-8")
    return out


def load_latest_bundle(repo_root: Path) -> IdeasBundle | None:
    """Load ``runs/ideas/latest.json`` if present."""
    lp = latest_path(repo_root.resolve())
    if not lp.is_file():
        return None
    import json

    try:
        raw = json.loads(lp.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict) or "ideas" not in raw:
        return None
    ideas: list[Idea] = []
    for row in raw.get("ideas") or []:
        if not isinstance(row, dict):
            continue
        ideas.append(
            Idea(
                idea_id=str(row.get("idea_id", new_idea_id("idea"))),
                title=str(row.get("title", "")),
                description=str(row.get("description", "")),
                type=IdeaType(str(row.get("type", "explore"))),
                source=IdeaSource(str(row.get("source", "synthesis"))),
                novelty_score=float(row.get("novelty_score", 0)),
                adjacency_score=float(row.get("adjacency_score", 0)),
                expected_value_score=float(row.get("expected_value_score", 0)),
                confidence_score=float(row.get("confidence_score", 0)),
                cost_estimate=str(row.get("cost_estimate", "")),
                channel_type=str(row.get("channel_type", "hybrid")),
                monetization_type=str(row.get("monetization_type", "hybrid")),
                rationale=str(row.get("rationale", "")),
                diversity_impact_score=float(row.get("diversity_impact_score", 0)),
                product_id=row.get("product_id"),
                parent_idea_id=row.get("parent_idea_id"),
                llm_expansion=row.get("llm_expansion") if isinstance(row.get("llm_expansion"), dict) else None,
                audit_adjustment=row.get("audit_adjustment") if isinstance(row.get("audit_adjustment"), dict) else None,
                signal_hygiene=row.get("signal_hygiene") if isinstance(row.get("signal_hygiene"), dict) else None,
                signal_provenance=row.get("signal_provenance") if isinstance(row.get("signal_provenance"), dict) else None,
                tone_alignment=row.get("tone_alignment") if isinstance(row.get("tone_alignment"), dict) else None,
                grounding=row.get("grounding") if isinstance(row.get("grounding"), dict) else None,
            )
        )
    return IdeasBundle(
        generated_at_utc=str(raw.get("generated_at_utc", "")),
        repo_root=str(raw.get("repo_root", "")),
        ideas=ideas,
        product_id=raw.get("product_id"),
        meta=raw.get("meta") if isinstance(raw.get("meta"), dict) else {},
    )
