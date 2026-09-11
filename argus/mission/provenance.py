"""
Mission provenance for operator-facing artifacts.

Per-product artifacts embed a single ``mission_context`` (``argus.mission_provenance.v1``).
Portfolio-scoped artifacts use ``portfolio_mission_provenance`` (``argus.portfolio_mission_provenance.v1``)
with per-product maps and a mix summary — Argus does not assume one global mission for the repo.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from argus.mission.mission import resolve_global_mission, resolve_product_mission

MISSION_PROVENANCE_SCHEMA = "argus.mission_provenance.v1"
PORTFOLIO_MISSION_PROVENANCE_SCHEMA = "argus.portfolio_mission_provenance.v1"


def build_mission_context_for_product(repo_root: Path, product_id: str) -> dict[str, Any]:
    """
    Mission identity for one product (canonical: ``product.yaml`` mission objective / legacy ``mission_id``).

    Use on per-product artifacts (e.g. operator snapshot).
    """
    root = repo_root.resolve()
    eff = resolve_product_mission(root, str(product_id).strip())
    mis = eff.get("mission") if isinstance(eff.get("mission"), dict) else {}
    sm = eff.get("structured_mission") if isinstance(eff.get("structured_mission"), dict) else {}
    chain = eff.get("resolution_chain")
    if not isinstance(chain, list):
        chain = []
    rp_eff = sm.get("effective_risk_posture") if sm else None
    out: dict[str, Any] = {
        "schema": MISSION_PROVENANCE_SCHEMA,
        "resolved_mission_id": eff.get("resolved_mission_id"),
        "mission_profile_id": mis.get("id"),
        "risk_posture": rp_eff or mis.get("risk_posture"),
        "primary_objective": mis.get("primary_objective"),
        "resolution_chain": [str(x) for x in chain],
        "effective_mission_evaluated_at_utc": eff.get("resolved_at_utc"),
        "product_id": str(product_id).strip(),
        "resolution_scope": eff.get("resolution_scope"),
    }
    if sm:
        out["mission_objective"] = sm.get("objective")
        out["mission_drivers"] = list(sm.get("drivers") or [])
        out["mission_guardrails"] = list(sm.get("guardrails") or [])
        if sm.get("risk_posture") is not None:
            out["mission_risk_posture_override"] = sm.get("risk_posture")
    return out


def build_repository_mission_context(repo_root: Path) -> dict[str, Any]:
    """
    Repository-level mission (env / ``runs/mission/current.json`` / registry default).

    Use when no product scope exists — **not** a substitute for per-product canonical mission.
    """
    root = repo_root.resolve()
    eff = resolve_global_mission(root)
    mis = eff.get("mission") if isinstance(eff.get("mission"), dict) else {}
    chain = eff.get("resolution_chain")
    if not isinstance(chain, list):
        chain = []
    return {
        "schema": MISSION_PROVENANCE_SCHEMA,
        "resolved_mission_id": eff.get("resolved_mission_id"),
        "mission_profile_id": mis.get("id"),
        "risk_posture": mis.get("risk_posture"),
        "primary_objective": mis.get("primary_objective"),
        "resolution_chain": [str(x) for x in chain],
        "effective_mission_evaluated_at_utc": eff.get("resolved_at_utc"),
        "resolution_scope": eff.get("resolution_scope"),
    }


def build_portfolio_mission_provenance(repo_root: Path, product_ids: list[str]) -> dict[str, Any]:
    """
    Mixed-mission view for portfolio-scoped artifacts (queue, cycle, outcomes, dashboard, policy aggregates).

    Avoids a single misleading ``resolved_mission_id`` for the whole portfolio.
    """
    root = repo_root.resolve()
    ids = sorted({str(x).strip() for x in product_ids if str(x).strip()})
    by_p: dict[str, Any] = {}
    counts: dict[str, int] = {}
    driver_counts: dict[str, int] = {}
    guardrail_counts: dict[str, int] = {}
    repo_fb: list[str] = []
    for pid in ids:
        mc = build_mission_context_for_product(root, pid)
        by_p[pid] = mc
        mid = str(mc.get("resolved_mission_id") or "")
        counts[mid] = counts.get(mid, 0) + 1
        for d in mc.get("mission_drivers") or []:
            ds = str(d).strip()
            if ds:
                driver_counts[ds] = driver_counts.get(ds, 0) + 1
        for g in mc.get("mission_guardrails") or []:
            gs = str(g).strip()
            if gs:
                guardrail_counts[gs] = guardrail_counts.get(gs, 0) + 1
        if mc.get("resolution_scope") == "repository_fallback":
            repo_fb.append(pid)
    return {
        "schema": PORTFOLIO_MISSION_PROVENANCE_SCHEMA,
        "evaluated_at_utc": datetime.now(timezone.utc).isoformat(),
        "mission_context_by_product": by_p,
        "mission_mix_summary": {
            "distinct_mission_ids": sorted(counts.keys()),
            "counts_by_mission_id": counts,
            "driver_profile_counts": driver_counts,
            "guardrail_profile_counts": guardrail_counts,
            "products_using_repository_fallback": sorted(repo_fb),
        },
    }


def portfolio_mission_markdown_lines_from_payload(payload: dict[str, Any]) -> list[str]:
    """Prefer ``portfolio_mission_provenance``; fall back to legacy ``mission_context`` (single repo snapshot)."""
    pmp = payload.get("portfolio_mission_provenance")
    if isinstance(pmp, dict) and str(pmp.get("schema") or "") == PORTFOLIO_MISSION_PROVENANCE_SCHEMA:
        return portfolio_mission_markdown_lines(pmp)
    legacy = payload.get("mission_context")
    if isinstance(legacy, dict) and legacy.get("resolved_mission_id"):
        return [
            "## Mission (legacy stamp)",
            "",
            f"- **Single snapshot:** `{legacy.get('resolved_mission_id')}` · **risk posture:** `{legacy.get('risk_posture') or '—'}`",
            "",
        ]
    return []


def portfolio_mission_markdown_lines(portfolio: dict[str, Any] | None) -> list[str]:
    """Human-readable lines for Markdown renderers (portfolio payloads)."""
    if not isinstance(portfolio, dict):
        return []
    if str(portfolio.get("schema") or "") != PORTFOLIO_MISSION_PROVENANCE_SCHEMA:
        return []
    mix = portfolio.get("mission_mix_summary") or {}
    distinct = mix.get("distinct_mission_ids") or []
    counts = mix.get("counts_by_mission_id") or {}
    fb = mix.get("products_using_repository_fallback") or []
    parts = [f"`{m}`×{counts.get(m, 0)}" for m in distinct]
    lines = [
        "## Mission mix (portfolio)",
        "",
        f"- **Missions present:** {', '.join(parts) if parts else '—'}",
    ]
    if isinstance(fb, list) and fb:
        lines.append(
            f"- **Repo fallback (no product mission):** {', '.join(f'`{x}`' for x in fb[:16])}"
            + (" …" if len(fb) > 16 else "")
        )
    lines.append("")
    return lines


def mission_context_markdown_lines(mc: dict[str, Any] | None) -> list[str]:
    """Lines for a single-product ``mission_context`` block."""
    if not isinstance(mc, dict):
        return []
    mid = mc.get("resolved_mission_id") or "—"
    rp = mc.get("risk_posture") or "—"
    pid = mc.get("product_id")
    scope = mc.get("resolution_scope") or "—"
    lines = [
        "## Mission context",
        "",
        f"- **Mission:** `{mid}` · **risk posture:** `{rp}`",
    ]
    if pid:
        lines.append(f"- **Product:** `{pid}` · **resolution:** `{scope}`")
    lines.append("")
    return lines
