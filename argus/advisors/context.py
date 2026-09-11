"""Assemble local product context for advisor prompts (no network)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from argus.advisors.temporal import TemporalGrounding, build_temporal_grounding
from argus.core.serialize import dumps_json
from argus.decision.persistence import load_latest_product_decisions
from argus.doctrine.load import load_doctrine_for_product
from argus.experiments.store import list_experiments
from argus.findings.persistence import load_latest_findings
from argus.products.inventory import build_inventory


@dataclass
class ConsultationContext:
    """Structured slices used by :mod:`argus.advisors.prompts`."""

    product_id: str
    product_summary: dict[str, Any] = field(default_factory=dict)
    findings_section: str = ""
    trends_section: str = ""
    decisions_section: str = ""
    experiments_section: str = ""
    doctrine_section: str = ""
    self_improvement_section: str = ""
    raw_notes: dict[str, Any] = field(default_factory=dict)
    temporal: TemporalGrounding | None = None
    """Filled by :func:`gather_consultation_context` — explicit artifact ages and signal excerpts."""


def _safe_json(obj: Any) -> str:
    try:
        return dumps_json(obj, indent=None)
    except TypeError:
        return str(obj)


def gather_consultation_context(repo_root: Path, product_id: str) -> ConsultationContext:
    """Load findings, trends, decisions, experiments, and product node summary."""
    root = repo_root.resolve()
    inv = build_inventory(root)
    if product_id not in inv.valid:
        raise ValueError(f"Unknown or invalid product: {product_id!r}")
    node = inv.valid[product_id].node

    summary = {
        "id": node.id,
        "name": node.name,
        "lifecycle_stage": node.lifecycle.stage.value,
        "owner_team": node.owner.team,
        "product_root": node.product_root,
        "cost_monthly_usd": node.cost.monthly_usd,
        "constraints": {
            "max_monthly_cost_usd": node.constraints.max_monthly_cost_usd,
        },
        "tags": list(node.tags),
    }

    findings_lines: list[str] = []
    fb = load_latest_findings(root, product_id)
    if fb is None:
        findings_lines.append("No findings bundle at runs/findings/latest/ for this product.")
    else:
        findings_lines.append(f"Generated at (UTC): {fb.generated_at_utc}")
        findings_lines.append(f"Count: {len(fb.findings)}")
        for f in fb.findings[:40]:
            findings_lines.append(
                f"- [{f.kind.value}] {f.severity.value} — {f.title}\n  {f.summary[:400]}"
            )
        if len(fb.findings) > 40:
            findings_lines.append(f"... ({len(fb.findings) - 40} more omitted)")

    trends_path = root / "runs" / "trends" / "latest.json"
    trends_section = ""
    if trends_path.is_file():
        try:
            data = json.loads(trends_path.read_text(encoding="utf-8"))
            summaries = data.get("summaries") if isinstance(data, dict) else None
            if isinstance(summaries, list):
                for s in summaries:
                    if not isinstance(s, dict):
                        continue
                    if str(s.get("product_id", "")) != product_id:
                        continue
                    trends_section = _safe_json(s)
                    break
            if not trends_section:
                trends_section = "(trends file present but no entry for this product_id)"
        except (OSError, json.JSONDecodeError):
            trends_section = "(failed to read runs/trends/latest.json)"
    else:
        trends_section = "No runs/trends/latest.json — run `argus trends analyze` for history-based trends."

    dec_lines: list[str] = []
    raw = load_latest_product_decisions(root, product_id)
    if raw is None:
        dec_lines.append("No saved decisions at runs/decisions/latest/ for this product.")
    else:
        dec_lines.append(_safe_json({k: raw[k] for k in ("product_id", "generated_at_utc", "lifecycle") if k in raw}))
        cands = raw.get("candidates")
        if isinstance(cands, list) and cands:
            top = cands[0]
            if isinstance(top, dict):
                dec_lines.append(f"Top candidate summary: {str(top.get('summary', ''))[:800]}")

    exps = list_experiments(root, product_id=product_id)
    if not exps:
        experiments_section = "No experiments under runs/experiments/ for this product."
    else:
        experiments_section = _safe_json(
            [
                {
                    "id": e.id,
                    "hypothesis": e.hypothesis,
                    "type": e.type.value,
                    "status": e.status.value,
                    "last_evaluation_verdict": e.last_evaluation_verdict,
                    "last_evaluation_summary": (e.last_evaluation_summary or "")[:500],
                }
                for e in exps[-20:]
            ]
        )

    doctrine_section = "No doctrine.yaml in this product directory."
    doc, doc_err = load_doctrine_for_product(root, product_id, product_root=node.product_root)
    if doc_err:
        doctrine_section = f"doctrine.yaml failed to load: {doc_err}"
    elif doc is not None:
        doctrine_section = _safe_json(
            {
                "schema": doc.schema_id,
                "summary": doc.summary,
                "principles": list(doc.principles),
                "constraints": {
                    "max_monthly_cost_usd": doc.constraints.max_monthly_cost_usd,
                    "require_human_review_when_kill_candidate": doc.constraints.require_human_review_when_kill_candidate,
                },
                "scoring": {
                    "intent_priority_multiplier": doc.scoring.intent_priority_multiplier,
                    "experiment_score_boost": doc.scoring.experiment_score_boost,
                },
            }
        )

    si = ""
    try:
        from argus.self_improvement.planning import prompt_excerpt_for_advisors

        si = prompt_excerpt_for_advisors(root)
    except (ImportError, OSError, TypeError, ValueError):
        si = ""

    temporal = build_temporal_grounding(root, product_id)

    return ConsultationContext(
        product_id=product_id,
        product_summary=summary,
        findings_section="\n".join(findings_lines),
        trends_section=trends_section,
        decisions_section="\n".join(dec_lines),
        experiments_section=experiments_section,
        doctrine_section=doctrine_section,
        self_improvement_section=si,
        raw_notes={"inventory_warnings": len(inv.valid[product_id].warnings)},
        temporal=temporal,
    )
