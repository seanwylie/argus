"""Aggregate ``runs/ideas/latest.json`` for the portfolio dashboard."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

logger = __import__("logging").getLogger("argus.dashboard")


def _safe_float(x: Any, default: float = 0.0) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def enrich_idea_for_dashboard(row: dict[str, Any], *, selection: str) -> dict[str, Any]:
    """Add diversity score, risk/reward tier, highlights, and selection for UI."""
    nov = _safe_float(row.get("novelty_score"))
    adj = _safe_float(row.get("adjacency_score"))
    ev = _safe_float(row.get("expected_value_score"))
    conf = _safe_float(row.get("confidence_score"))
    diversity_score = max(0.0, min(1.0, (nov + adj) / 2.0))
    t = str(row.get("type") or "")
    product = ev * conf
    novelty_ev = ev * nov
    high_rr = (novelty_ev >= 0.35 and product >= 0.28) or (t == "invent" and ev >= 0.45 and nov >= 0.35)
    medium_rr = product >= 0.22 or novelty_ev >= 0.22
    if high_rr:
        tier = "high"
    elif medium_rr:
        tier = "medium"
    else:
        tier = "low"
    highlights: list[str] = []
    if t == "invent":
        highlights.append("invent")
    if high_rr:
        highlights.append("high_risk_reward")
    if selection == "rejected_duplicate":
        highlights.append("duplicate_reject")
    out = dict(row)
    out["diversity_score"] = round(diversity_score, 4)
    out["risk_reward_tier"] = tier
    out["selection_status"] = selection
    out["highlights"] = highlights
    out["rationale"] = str(row.get("rationale") or "")
    return out


def _entropy_norm(counts: dict[str, int]) -> float:
    total = sum(counts.values())
    if total <= 0:
        return 0.0
    h = 0.0
    for c in counts.values():
        if c <= 0:
            continue
        p = c / total
        h -= p * math.log(p + 1e-12)
    k = len([x for x in counts.values() if x > 0])
    if k <= 1:
        return 0.0
    return max(0.0, min(1.0, h / math.log(k + 1e-12)))


def build_ideas_dashboard_block(
    repo_root: Path,
    product_ids: list[str],
) -> dict[str, Any]:
    """
    Load ``runs/ideas/latest.json`` and build portfolio + per-product idea summaries.

    Safe when file missing or malformed (returns ``present: false``).
    """
    root = repo_root.resolve()
    path = root / "runs" / "ideas" / "latest.json"
    if not path.is_file():
        return {
            "schema": "argus.dashboard_ideas.v1",
            "present": False,
            "path_repo": "runs/ideas/latest.json",
        }
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("ideas bundle unreadable: %s", e)
        return {
            "schema": "argus.dashboard_ideas.v1",
            "present": False,
            "path_repo": "runs/ideas/latest.json",
            "parse_error": str(e),
        }
    if not isinstance(raw, dict):
        return {"schema": "argus.dashboard_ideas.v1", "present": False, "path_repo": "runs/ideas/latest.json"}

    ideas_raw = raw.get("ideas")
    ideas_list = ideas_raw if isinstance(ideas_raw, list) else []
    meta = raw.get("meta") if isinstance(raw.get("meta"), dict) else {}
    rejected = meta.get("rejected_duplicates")
    rejected_list = rejected if isinstance(rejected, list) else []

    by_type: dict[str, int] = {"exploit": 0, "explore": 0, "invent": 0}
    enriched_selected: list[dict[str, Any]] = []
    by_product: dict[str, list[dict[str, Any]]] = {pid: [] for pid in product_ids}
    portfolio_only_ideas: list[dict[str, Any]] = []
    high_rr = 0
    invent_n = 0

    for row in ideas_list:
        if not isinstance(row, dict):
            continue
        t = str(row.get("type") or "explore")
        if t in by_type:
            by_type[t] += 1
        if t == "invent":
            invent_n += 1
        er = enrich_idea_for_dashboard(row, selection="selected")
        enriched_selected.append(er)
        if er.get("risk_reward_tier") == "high":
            high_rr += 1
        pid = row.get("product_id")
        ps = str(pid) if pid is not None else None
        if ps and ps in by_product:
            by_product[ps].append(er)
        elif ps is None:
            portfolio_only_ideas.append(er)

    enriched_rejected: list[dict[str, Any]] = []
    for rj in rejected_list:
        if not isinstance(rj, dict):
            continue
        slim = {
            "title": rj.get("title"),
            "type": rj.get("type"),
            "source": rj.get("source"),
            "selection_status": "rejected_duplicate",
            "reason": rj.get("reason") or "duplicate_title",
            "product_id": rj.get("product_id"),
        }
        er = enrich_idea_for_dashboard(
            {
                "title": slim["title"],
                "type": slim.get("type"),
                "source": slim.get("source"),
                "novelty_score": 0,
                "adjacency_score": 0,
                "expected_value_score": 0,
                "confidence_score": 0,
                "rationale": "",
            },
            selection="rejected_duplicate",
        )
        slim["highlights"] = er.get("highlights", [])
        slim["risk_reward_tier"] = er.get("risk_reward_tier")
        slim["diversity_score"] = er.get("diversity_score")
        enriched_rejected.append(slim)

    diversity_index = _entropy_norm(by_type)

    per_product: dict[str, Any] = {}
    for pid in product_ids:
        plist = by_product.get(pid, [])
        inv_c = sum(1 for x in plist if x.get("type") == "invent")
        hrr = sum(1 for x in plist if x.get("risk_reward_tier") == "high")
        per_product[pid] = {
            "ideas_count": len(plist),
            "ideas_invent_count": inv_c,
            "ideas_high_risk_reward_count": hrr,
            "ideas": plist[:24],
        }

    return {
        "schema": "argus.dashboard_ideas.v1",
        "present": True,
        "path_repo": "runs/ideas/latest.json",
        "generated_at_utc": raw.get("generated_at_utc"),
        "bundle_scope_product_id": raw.get("product_id"),
        "portfolio": {
            "idea_total": len(enriched_selected),
            "by_type": by_type,
            "rejected_duplicate_total": len(enriched_rejected),
            "diversity_index": round(diversity_index, 4),
            "invent_count": invent_n,
            "high_risk_reward_count": high_rr,
        },
        "rejected_duplicates": enriched_rejected[:48],
        "selected_ideas_sample": enriched_selected[:40],
        "portfolio_only_ideas": portfolio_only_ideas[:20],
        "per_product": per_product,
    }
