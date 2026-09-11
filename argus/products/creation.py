"""
Product creation proposals — deterministic, mission-grounded identification of portfolio gaps.

Reads current inventory, portfolio outcomes/patterns/intervention, operator queue, and the
**creation mission** (global fallback) to propose candidate products that would address
observable gaps. Proposal-only: does **not** scaffold or write ``products/`` directories.
"""

from __future__ import annotations

import hashlib
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from argus.core.serialize import dumps_json
from argus.mission.mission import load_mission_registry, resolve_creation_mission
from argus.portfolio.strategy_influence import (
    apply_proposal_strategy_influence,
    creation_influence_for_posture,
    load_latest_strategic_posture,
)
from argus.products.inventory import build_inventory

PRODUCT_CREATION_PROPOSALS_SCHEMA = "argus.product_creation_proposals.v1"
INITIAL_SUGGESTED_MISSION_SCHEMA = "argus.initial_suggested_mission.v1"

_RISK_ENUM = frozenset({"conservative", "moderate", "aggressive"})
# When choosing complementary profile ids (excluding objective), prefer this order for determinism.
_PROFILE_PRIORITY = ("education", "engagement", "revenue")


def creation_proposals_dir(repo_root: Path) -> Path:
    return Path(repo_root).resolve() / "runs" / "products" / "creation"


def _load_json(path: Path) -> dict[str, Any] | None:
    import json

    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _proposal_id(concept_title: str, mission_id: str, salt: str) -> str:
    h = hashlib.sha256(f"{concept_title}|{mission_id}|{salt}".encode()).hexdigest()[:12]
    return f"creation_{h}"


# ---------------------------------------------------------------------------
# Portfolio evidence collection (read-only)
# ---------------------------------------------------------------------------

def _load_portfolio_outcomes(repo_root: Path) -> dict[str, Any] | None:
    return _load_json(repo_root / "runs" / "portfolio" / "outcomes" / "latest.json")


def _load_portfolio_patterns(repo_root: Path) -> dict[str, Any] | None:
    return _load_json(repo_root / "runs" / "portfolio" / "patterns" / "latest.json")


def _load_operator_queue(repo_root: Path) -> dict[str, Any] | None:
    return _load_json(repo_root / "runs" / "portfolio" / "operator_queue" / "latest.json")


def _load_operator_summary(repo_root: Path) -> dict[str, Any] | None:
    return _load_json(repo_root / "runs" / "dashboard" / "operator_summary" / "latest.json")


def _lifecycle_distribution(inventory: dict[str, Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for _pid, rec in inventory.items():
        stage = "unknown"
        node = rec.node if hasattr(rec, "node") else None
        if node is not None and hasattr(node, "lifecycle"):
            stage = str(node.lifecycle.stage.value) if node.lifecycle else "unknown"
        counts[stage] = counts.get(stage, 0) + 1
    return counts


def _product_types(inventory: dict[str, Any]) -> dict[str, list[str]]:
    by_type: dict[str, list[str]] = {}
    for pid, rec in inventory.items():
        node = rec.node if hasattr(rec, "node") else None
        ptype = "unknown"
        if node is not None and hasattr(node, "type_info") and node.type_info:
            ptype = str(node.type_info.type or "unknown")
        by_type.setdefault(ptype, []).append(pid)
    return by_type


def _mission_drivers(mission: dict[str, Any]) -> list[str]:
    profile = mission.get("mission") or {}
    return list(profile.get("drivers") or [])


def _mission_objective(mission: dict[str, Any]) -> str:
    profile = mission.get("mission") or {}
    return str(profile.get("primary_objective") or "")


def _mission_risk_posture(mission: dict[str, Any]) -> str:
    profile = mission.get("mission") or {}
    return str(profile.get("risk_posture") or "moderate")


def _normalize_registry_risk_posture(mission: dict[str, Any]) -> str:
    rp = str(_mission_risk_posture(mission)).strip().lower()
    return rp if rp in _RISK_ENUM else "moderate"


def _creation_mission_driver_phrases(mission: dict[str, Any]) -> list[str]:
    """Human lines from the creation mission YAML (not valid product.yaml mission profile ids)."""
    profile = mission.get("mission") or {}
    if not isinstance(profile, dict):
        return []
    return [str(x).strip() for x in (profile.get("drivers") or []) if isinstance(x, str) and str(x).strip()]


def _registry_objective_and_others(repo_root: Path, mission: dict[str, Any]) -> tuple[str, set[str]]:
    """Resolved mission profile id as objective, plus other profile ids in the registry."""
    reg = load_mission_registry(repo_root.resolve())
    profs = reg.get("profiles") or {}
    if not isinstance(profs, dict):
        profs = {}
    reg_ids = {str(k).strip().lower() for k in profs.keys() if str(k).strip()}
    mid = str(mission.get("resolved_mission_id") or "").strip().lower()
    if mid not in reg_ids:
        mid = str(reg.get("default_mission_id") or "revenue").strip().lower()
    if mid not in reg_ids:
        mid = sorted(reg_ids)[0] if reg_ids else mid
    others = reg_ids - {mid}
    return mid, others


def _pick_driver_profile_ids(others: set[str], opportunity_type: str) -> list[str]:
    """Complementary registry profile ids for ``mission.drivers`` (deterministic)."""
    if not others:
        return []
    opp = str(opportunity_type or "").strip().lower()
    if opp == "mission_alignment":
        n = min(3, len(others))
    elif opp in ("greenfield",):
        n = min(1, len(others))
    else:
        n = min(2, len(others))
    picked: list[str] = []
    for key in _PROFILE_PRIORITY:
        if key in others and len(picked) < n:
            picked.append(key)
    for key in sorted(others):
        if len(picked) >= n:
            break
        if key not in picked:
            picked.append(key)
    return picked[:n]


def _pick_guardrail_profile_ids(
    *,
    others_after_drivers: set[str],
    gap: dict[str, Any],
    opportunity_type: str,
) -> list[str]:
    """Registry profile ids for ``mission.guardrails`` — disjoint from drivers + objective."""
    if not others_after_drivers:
        return []
    opp = str(opportunity_type or "").strip().lower()
    sev = str(gap.get("severity") or "").strip().lower()
    gid = str(gap.get("gap_id") or "")
    gr: list[str] = []
    if sev == "high" and "education" in others_after_drivers:
        gr.append("education")
    if opp in ("structural_remedy", "growth_injection") and "education" in others_after_drivers:
        if "education" not in gr:
            gr.append("education")
    if opp == "structural_remedy" and "engagement" in others_after_drivers:
        if "engagement" not in gr:
            gr.append("engagement")
    if gid == "gap.portfolio_stagnation" and "engagement" in others_after_drivers:
        if "engagement" not in gr:
            gr.append("engagement")
    out = sorted({x for x in gr if x in others_after_drivers})
    return out[:2]


def _build_initial_suggested_mission(
    repo_root: Path,
    mission: dict[str, Any],
    gap: dict[str, Any],
    opportunity_type: str,
) -> dict[str, Any]:
    """
    Structured mission suggestion using **registry profile ids** only (machine-usable for scaffolding).

    Human prose from the creation mission profile lives in ``mission_human_context`` on the proposal,
    not in ``drivers`` / ``guardrails`` here.
    """
    objective, others = _registry_objective_and_others(repo_root, mission)
    opp = str(opportunity_type or "").strip().lower()
    drivers = _pick_driver_profile_ids(others, opp)
    driver_set = set(drivers)
    guardrails = _pick_guardrail_profile_ids(
        others_after_drivers=others - driver_set,
        gap=gap,
        opportunity_type=opp,
    )
    rp = _normalize_registry_risk_posture(mission)
    return {
        "schema": INITIAL_SUGGESTED_MISSION_SCHEMA,
        "objective": objective,
        "drivers": drivers,
        "guardrails": guardrails,
        "risk_posture": rp,
        "mission_profile_fields_are_registry_ids": True,
        "note": (
            "Structured mission: objective/drivers/guardrails are mission registry profile ids "
            "(argus.initial_suggested_mission.v1); see mission_human_context for creation-mission prose."
        ),
    }


# ---------------------------------------------------------------------------
# Gap detection heuristics (deterministic, from existing artifacts only)
# ---------------------------------------------------------------------------

def _detect_gaps(
    *,
    valid_products: dict[str, Any],
    lifecycle_dist: dict[str, int],
    product_types: dict[str, list[str]],
    outcomes: dict[str, Any] | None,
    patterns: dict[str, Any] | None,
    queue: dict[str, Any] | None,
    mission_drivers: list[str],
    mission_objective: str,
) -> list[dict[str, Any]]:
    """Return a list of detected gap dicts, each with gap_id, title, evidence, severity."""
    gaps: list[dict[str, Any]] = []

    total_products = len(valid_products)

    # Gap: empty portfolio
    if total_products == 0:
        gaps.append({
            "gap_id": "gap.empty_portfolio",
            "title": "Portfolio has no valid products",
            "severity": "high",
            "evidence": {"valid_product_count": 0},
            "opportunity_type": "greenfield",
        })
        return gaps

    # Gap: portfolio too concentrated (single product)
    if total_products == 1:
        pid = next(iter(valid_products))
        gaps.append({
            "gap_id": "gap.single_product_portfolio",
            "title": "Portfolio depends on a single product",
            "severity": "medium",
            "evidence": {"product_id": pid, "total": 1},
            "opportunity_type": "diversification",
        })

    # Gap: all products in late lifecycle (maintain/decline/kill)
    late_stages = {"maintain", "decline", "kill"}
    late_count = sum(lifecycle_dist.get(s, 0) for s in late_stages)
    early_stages = {"idea", "build", "validate"}
    early_count = sum(lifecycle_dist.get(s, 0) for s in early_stages)
    if total_products >= 2 and late_count == total_products:
        gaps.append({
            "gap_id": "gap.no_early_stage_products",
            "title": "No products in early lifecycle stages (idea/build/validate)",
            "severity": "high",
            "evidence": {"lifecycle_distribution": lifecycle_dist},
            "opportunity_type": "pipeline_renewal",
        })
    elif total_products >= 3 and early_count == 0 and late_count >= total_products * 0.6:
        gaps.append({
            "gap_id": "gap.pipeline_aging",
            "title": "Portfolio skews toward late-stage products with no early pipeline",
            "severity": "medium",
            "evidence": {"lifecycle_distribution": lifecycle_dist, "late_ratio": round(late_count / total_products, 2)},
            "opportunity_type": "pipeline_renewal",
        })

    # Gap: all products stagnant or regressing (from outcomes)
    if outcomes is not None:
        per_product = outcomes.get("per_product") or []
        if isinstance(per_product, list) and per_product:
            trajectories = [str(p.get("overall_trajectory") or "") for p in per_product if isinstance(p, dict)]
            improving = sum(1 for t in trajectories if t == "improving")
            regressing = sum(1 for t in trajectories if t == "regressing")
            stable = sum(1 for t in trajectories if t == "stable")
            if len(trajectories) >= 2 and improving == 0 and regressing >= max(1, len(trajectories) // 2):
                gaps.append({
                    "gap_id": "gap.portfolio_stagnation",
                    "title": "No products improving; majority regressing",
                    "severity": "high",
                    "evidence": {
                        "improving": improving,
                        "stable": stable,
                        "regressing": regressing,
                        "total_tracked": len(trajectories),
                    },
                    "opportunity_type": "growth_injection",
                })

    # Gap: systemic patterns suggest structural issues (from patterns)
    if patterns is not None:
        detected = patterns.get("detected_patterns") or []
        if isinstance(detected, list):
            high_sev = [p for p in detected if isinstance(p, dict) and p.get("severity") == "high"]
            if len(high_sev) >= 2:
                gaps.append({
                    "gap_id": "gap.systemic_pattern_pressure",
                    "title": "Multiple high-severity systemic patterns detected across portfolio",
                    "severity": "medium",
                    "evidence": {
                        "high_severity_pattern_count": len(high_sev),
                        "pattern_ids": [p.get("pattern_id") for p in high_sev[:6]],
                    },
                    "opportunity_type": "structural_remedy",
                })

    # Gap: mission driver coverage
    if mission_drivers:
        driver_product_coverage: dict[str, int] = {}
        for drv in mission_drivers:
            driver_product_coverage[drv] = 0
        uncovered = [d for d in mission_drivers if driver_product_coverage.get(d, 0) == 0]
        if uncovered:
            gaps.append({
                "gap_id": "gap.mission_driver_uncovered",
                "title": "Mission drivers have no explicit product coverage",
                "severity": "low",
                "evidence": {
                    "uncovered_drivers": uncovered,
                    "total_drivers": len(mission_drivers),
                    "note": "Driver coverage is heuristic — products may implicitly serve drivers without tagging.",
                },
                "opportunity_type": "mission_alignment",
            })

    # Gap: type concentration
    if len(product_types) == 1 and total_products >= 2:
        the_type = next(iter(product_types))
        gaps.append({
            "gap_id": "gap.type_monoculture",
            "title": f"All products are type '{the_type}' — no diversity",
            "severity": "low",
            "evidence": {"product_type": the_type, "count": total_products},
            "opportunity_type": "diversification",
        })

    return gaps


# ---------------------------------------------------------------------------
# Proposal generation (from gaps + mission)
# ---------------------------------------------------------------------------

_OPPORTUNITY_TO_CONCEPT: dict[str, dict[str, str]] = {
    "greenfield": {
        "title_template": "Initial product aligned with {objective_short}",
        "role": "seed",
        "rationale_prefix": "Portfolio is empty; this product would establish the first foothold toward",
    },
    "diversification": {
        "title_template": "Complementary product for portfolio diversification",
        "role": "complement",
        "rationale_prefix": "Portfolio is concentrated; a complementary product would reduce single-point risk and",
    },
    "pipeline_renewal": {
        "title_template": "Early-stage product to renew portfolio pipeline",
        "role": "pipeline",
        "rationale_prefix": "Portfolio has no early-stage products; a new idea/validate-stage product would",
    },
    "growth_injection": {
        "title_template": "Growth-oriented product to counter portfolio stagnation",
        "role": "growth",
        "rationale_prefix": "Portfolio is stagnating or regressing; a fresh product with growth potential would",
    },
    "structural_remedy": {
        "title_template": "Product addressing systemic portfolio issues",
        "role": "remedy",
        "rationale_prefix": "Systemic patterns suggest structural gaps; a dedicated product could address",
    },
    "mission_alignment": {
        "title_template": "Mission-aligned product for uncovered drivers",
        "role": "mission_coverage",
        "rationale_prefix": "Mission drivers lack explicit product coverage; a new product could serve",
    },
}


def _build_proposals(
    *,
    gaps: list[dict[str, Any]],
    mission: dict[str, Any],
    existing_product_ids: list[str],
    run_salt: str,
    repo_root: Path,
) -> list[dict[str, Any]]:
    mission_id = str(mission.get("resolved_mission_id") or "unknown")
    objective = _mission_objective(mission)
    objective_short = objective[:80] + ("…" if len(objective) > 80 else "")
    driver_phrases = _creation_mission_driver_phrases(mission)

    proposals: list[dict[str, Any]] = []
    for gap in gaps:
        opp = str(gap.get("opportunity_type") or "")
        tmpl = _OPPORTUNITY_TO_CONCEPT.get(opp, _OPPORTUNITY_TO_CONCEPT.get("greenfield", {}))
        concept_title = tmpl.get("title_template", "New product proposal").format(
            objective_short=objective_short
        )
        role = tmpl.get("role", "unspecified")
        rationale_prefix = tmpl.get("rationale_prefix", "")

        rationale_parts = [rationale_prefix]
        if objective:
            rationale_parts.append(f"the mission objective: {objective_short}.")
        uncovered = gap.get("evidence", {}).get("uncovered_drivers")
        if isinstance(uncovered, list) and uncovered:
            rationale_parts.append(f"Uncovered drivers: {', '.join(uncovered[:6])}.")

        rationale = " ".join(p for p in rationale_parts if p).strip()
        if not rationale:
            rationale = f"Gap detected: {gap.get('title', 'unspecified')}."

        pid = _proposal_id(concept_title, mission_id, f"{gap.get('gap_id', '')}|{run_salt}")

        related = []
        gap_affected = gap.get("evidence", {}).get("product_id")
        if isinstance(gap_affected, str):
            related.append(gap_affected)
        if not related and existing_product_ids:
            related = existing_product_ids[:5]

        suggested_mission = _build_initial_suggested_mission(
            repo_root,
            mission,
            gap,
            opp,
        )

        evidence_summary: dict[str, Any] = {
            "gap_id": gap.get("gap_id"),
            "gap_title": gap.get("title"),
            "gap_severity": gap.get("severity"),
            "opportunity_type": opp,
        }

        proposals.append({
            "proposal_id": pid,
            "concept_title": concept_title,
            "creation_mission_used": mission_id,
            "rationale": rationale,
            "related_products": sorted(set(related)),
            "expected_role_in_portfolio": role,
            "initial_suggested_mission": suggested_mission,
            "mission_human_context": {
                "creation_mission_driver_phrases": driver_phrases,
                "creation_mission_primary_objective_text": objective,
                "structured_mission_ruleset": "creation_gap_mapping_v1",
            },
            "confidence": "low" if gap.get("severity") == "low" else "medium",
            "evidence_summary": evidence_summary,
        })

    return proposals


# ---------------------------------------------------------------------------
# Top-level evaluator
# ---------------------------------------------------------------------------

def evaluate_creation_proposals(repo_root: Path) -> dict[str, Any]:
    root = repo_root.resolve()
    evaluated_at = datetime.now(timezone.utc).isoformat()
    run_salt = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    inv = build_inventory(root)
    valid = inv.valid
    existing_ids = sorted(valid.keys())

    mission = resolve_creation_mission(root)
    mission_id = str(mission.get("resolved_mission_id") or "unknown")
    drivers = _mission_drivers(mission)
    objective = _mission_objective(mission)

    lifecycle_dist = _lifecycle_distribution(valid)
    prod_types = _product_types(valid)

    outcomes = _load_portfolio_outcomes(root)
    patterns = _load_portfolio_patterns(root)
    queue = _load_operator_queue(root)

    gaps = _detect_gaps(
        valid_products=valid,
        lifecycle_dist=lifecycle_dist,
        product_types=prod_types,
        outcomes=outcomes,
        patterns=patterns,
        queue=queue,
        mission_drivers=drivers,
        mission_objective=objective,
    )

    proposals = _build_proposals(
        gaps=gaps,
        mission=mission,
        existing_product_ids=existing_ids,
        run_salt=run_salt,
        repo_root=root,
    )

    strat_posture, _strat_payload = load_latest_strategic_posture(root)
    portfolio_strategy_influence = creation_influence_for_posture(
        strat_posture,
        proposal_count=len(proposals),
    )
    proposals_influenced = apply_proposal_strategy_influence(proposals, portfolio_strategy_influence)

    return {
        "schema": PRODUCT_CREATION_PROPOSALS_SCHEMA,
        "evaluated_at_utc": evaluated_at,
        "creation_mission_id": mission_id,
        "creation_mission_objective": objective,
        "creation_mission_risk_posture": _mission_risk_posture(mission),
        "mission_machine_contract": {
            "initial_suggested_mission_schema": INITIAL_SUGGESTED_MISSION_SCHEMA,
            "fields_are_registry_profile_ids": True,
            "human_readable_mission_phrases_field": "mission_human_context.creation_mission_driver_phrases",
        },
        "portfolio_strategy_influence": portfolio_strategy_influence,
        "portfolio_snapshot": {
            "valid_product_count": len(valid),
            "existing_product_ids": existing_ids,
            "lifecycle_distribution": lifecycle_dist,
            "product_types": {k: sorted(v) for k, v in prod_types.items()},
        },
        "detected_gaps": gaps,
        "proposals": proposals_influenced,
        "proposal_count": len(proposals_influenced),
        "inputs": {
            "outcomes_present": outcomes is not None,
            "patterns_present": patterns is not None,
            "operator_queue_present": queue is not None,
            "strategic_posture_loaded": strat_posture is not None,
            "strategic_posture": strat_posture,
        },
    }


# ---------------------------------------------------------------------------
# Markdown renderer
# ---------------------------------------------------------------------------

def render_creation_proposals_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Product creation proposals",
        "",
        f"**Schema:** `{payload.get('schema')}`",
        f"**Evaluated (UTC):** {payload.get('evaluated_at_utc')}",
        f"**Creation mission:** `{payload.get('creation_mission_id')}` — {payload.get('creation_mission_objective', '')}",
        f"**Risk posture:** `{payload.get('creation_mission_risk_posture')}`",
        "",
        "## Portfolio snapshot",
        "",
        f"- **Valid products:** {payload.get('portfolio_snapshot', {}).get('valid_product_count', 0)}",
    ]
    ld = payload.get("portfolio_snapshot", {}).get("lifecycle_distribution") or {}
    if ld:
        parts = [f"{k}={v}" for k, v in sorted(ld.items())]
        lines.append(f"- **Lifecycle:** {', '.join(parts)}")
    psi = payload.get("portfolio_strategy_influence") or {}
    if psi:
        lines.extend(
            [
                "",
                "## Portfolio strategy influence (soft)",
                "",
                f"- **Posture (if loaded):** `{psi.get('strategic_posture')}`",
                f"- **Creation appetite:** {psi.get('creation_appetite')}",
                f"- **Emphasis score:** {psi.get('creation_emphasis_score')}",
                f"- **Default proposal surfacing:** {psi.get('default_proposal_surfacing')}",
            ]
        )
        for n in psi.get("notes") or []:
            lines.append(f"- {n}")
    lines.extend(["", "## Detected gaps", ""])
    for g in payload.get("detected_gaps") or []:
        lines.append(f"- **{g.get('gap_id')}** ({g.get('severity')}): {g.get('title')}")
    if not (payload.get("detected_gaps") or []):
        lines.append("No portfolio gaps detected.")
    lines.extend(["", "## Proposals", ""])
    for p in payload.get("proposals") or []:
        lines.append(f"### `{p.get('proposal_id')}`")
        lines.append("")
        lines.append(f"**{p.get('concept_title')}**")
        lines.append("")
        lines.append(f"- **Role:** {p.get('expected_role_in_portfolio')}")
        lines.append(f"- **Confidence:** {p.get('confidence')}")
        sinf = p.get("strategy_influence") or {}
        if sinf:
            lines.append(
                f"- **Strategy surfacing:** `{sinf.get('surfacing')}` — {sinf.get('note', '')}"
            )
        lines.append(f"- **Mission:** `{p.get('creation_mission_used')}`")
        lines.append(f"- **Related products:** {', '.join(f'`{x}`' for x in (p.get('related_products') or [])) or '—'}")
        lines.append(f"- **Rationale:** {p.get('rationale')}")
        sm = p.get("initial_suggested_mission") or {}
        if sm:
            lines.append(
                f"- **Suggested mission (machine):** schema `{sm.get('schema')}` · "
                f"objective=`{sm.get('objective')}` · "
                f"drivers={sm.get('drivers', [])} · "
                f"guardrails={sm.get('guardrails', [])} · "
                f"risk_posture=`{sm.get('risk_posture')}`"
            )
        mh = p.get("mission_human_context") or {}
        phrases = mh.get("creation_mission_driver_phrases") or []
        if phrases:
            lines.append(
                "- **Creation mission driver phrases (human, not product.yaml ids):** "
                + "; ".join(str(x) for x in phrases[:8])
            )
        lines.append("")
    if not (payload.get("proposals") or []):
        lines.append("No proposals generated — portfolio appears well-covered.")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


# ---------------------------------------------------------------------------
# Artifact writer
# ---------------------------------------------------------------------------

def write_creation_proposals_artifacts(
    repo_root: Path,
    payload: dict[str, Any],
    *,
    run_id: str | None = None,
) -> tuple[Path, Path, Path, Path]:
    root = repo_root.resolve()
    rid = run_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    d = creation_proposals_dir(root)
    d.mkdir(parents=True, exist_ok=True)
    stamped_json = d / f"{rid}.json"
    stamped_md = d / f"{rid}.md"
    latest_json = d / "latest.json"
    latest_md = d / "latest.md"
    pl = dict(payload)
    pl["run_id"] = rid
    stamped_json.write_text(dumps_json(pl) + "\n", encoding="utf-8")
    stamped_md.write_text(render_creation_proposals_markdown(pl), encoding="utf-8")
    shutil.copyfile(stamped_json, latest_json)
    shutil.copyfile(stamped_md, latest_md)
    return stamped_json, stamped_md, latest_json, latest_md


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def run_creation_proposals(
    repo_root: Path,
    *,
    write_artifacts: bool = True,
) -> dict[str, Any]:
    payload = evaluate_creation_proposals(repo_root)
    if write_artifacts:
        write_creation_proposals_artifacts(repo_root, payload)
    return payload
