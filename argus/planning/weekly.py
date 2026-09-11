"""Build a weekly plan from inventory, latest artifacts, trends, and escalations."""

from __future__ import annotations

import warnings
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from argus.decision.history.analyze import analyze_churn
from argus.decision.history.store import load_product_decision_history
from argus.decision.persistence import load_latest_portfolio, load_latest_product_decisions
from argus.decision.portfolio import build_portfolio_from_inventory
from argus.economics.analyze import analyze_inventory
from argus.economics.models import ProductEconomics
from argus.escalation.packet import list_packets
from argus.findings.persistence import load_latest_findings
from argus.input.apply import apply_planning_priority_nudge, planning_suppress_kill_flag
from argus.planning.models import ProductPlanLine, WeeklyPlan
from argus.planning.prioritize import (
    ProductSignals,
    pick_deprecate_review,
    pick_focus_top3,
    pick_hold,
    pick_incubate,
    pick_risk_top3,
    pick_watchlist,
)
from argus.products.inventory import build_inventory
from argus.trends.analyze import analyze_all_with_history
from argus.trends.models import TrendFlag


def _ranked_rows(repo_root: Path) -> list[dict[str, Any]]:
    raw = load_latest_portfolio(repo_root)
    if raw and isinstance(raw.get("ranked"), list) and raw["ranked"]:
        out: list[dict[str, Any]] = []
        for row in raw["ranked"]:
            if isinstance(row, dict) and row.get("product_id"):
                out.append(dict(row))
        if out:
            return out
    rows, _ = build_portfolio_from_inventory(repo_root)
    return [
        {
            "rank": r.rank,
            "product_id": r.product_id,
            "lifecycle_stage": r.lifecycle_stage,
            "top_intent": r.top_intent,
            "priority_score": r.priority_score,
            "summary": r.summary,
            "kill_candidate": r.assessment.kill_candidate,
        }
        for r in rows
    ]


def _escalation_counts(repo_root: Path) -> dict[str, int]:
    rows = list_packets(repo_root, limit=500)
    counts: dict[str, int] = {}
    for r in rows:
        pid = r.get("product_id")
        if not pid:
            continue
        counts[str(pid)] = counts.get(str(pid), 0) + 1
    return counts


def _product_signals(
    repo_root: Path,
    inv,
    ranked_map: dict[str, dict[str, Any]],
    esc_counts: dict[str, int],
) -> list[ProductSignals]:
    out: list[ProductSignals] = []
    for pid in sorted(inv.valid.keys()):
        rec = inv.valid[pid]
        node = rec.node
        row = ranked_map.get(pid)
        monthly = node.cost.monthly_usd
        cap = node.constraints.max_monthly_cost_usd
        over = monthly is not None and cap is not None and float(monthly) > float(cap)

        fb = load_latest_findings(repo_root, pid)
        findings_count = len(fb.findings) if fb else 0
        has_fa = fb is not None

        if row:
            rank = int(row.get("rank") or 999)
            ps = float(row.get("priority_score") or 0.0)
            intent = str(row.get("top_intent") or "")
            summary = str(row.get("summary") or "")
            kill = bool(row.get("kill_candidate"))
            stage = str(row.get("lifecycle_stage") or node.lifecycle.stage.value)
        else:
            rank = 999
            ps = 0.0
            intent = ""
            summary = ""
            kill = False
            stage = node.lifecycle.stage.value
            dec = load_latest_product_decisions(repo_root, pid)
            if dec:
                lc = dec.get("lifecycle") or {}
                kill = bool(lc.get("kill_candidate"))
                cands = dec.get("candidates") or []
                if isinstance(cands, list) and cands:
                    c0 = cands[0]
                    if isinstance(c0, dict):
                        summary = str(c0.get("summary", ""))
                        md = c0.get("metadata") or {}
                        if isinstance(md, dict):
                            intent = str(md.get("intent", "") or "")
                        pss = c0.get("priority_score")
                        if pss is not None:
                            ps = float(pss)

        churn: float | None = None
        hist = load_product_decision_history(repo_root, pid)
        if len(hist) >= 2:
            churn = analyze_churn(pid, hist, repo_root=repo_root).churn_score

        ps = apply_planning_priority_nudge(repo_root, pid, ps)
        kill = planning_suppress_kill_flag(repo_root, pid, kill)

        out.append(
            ProductSignals(
                product_id=pid,
                rank=rank,
                priority_score=ps,
                top_intent=intent,
                summary=summary,
                lifecycle_stage=stage,
                kill_candidate=kill,
                findings_count=findings_count,
                escalation_count=esc_counts.get(pid, 0),
                monthly_cost_usd=monthly,
                over_budget=over,
                churn_score=churn,
                has_findings_artifact=has_fa,
            )
        )
    return out


def _per_product_lines(
    signals: list[ProductSignals],
    economics: dict[str, ProductEconomics] | None,
) -> list[ProductPlanLine]:
    lines: list[ProductPlanLine] = []
    for s in sorted(signals, key=lambda x: x.product_id):
        step = (s.summary or "").strip()[:200]
        if not step:
            step = "Review latest findings and decisions."
        why = f"stage={s.lifecycle_stage} findings={s.findings_count} esc={s.escalation_count}"
        if s.kill_candidate:
            why += " kill_candidate"
        pe = economics.get(s.product_id) if economics else None
        if pe is not None:
            roi = pe.roi_estimate
            roi_s = f"{roi:.2f}" if roi is not None else "—"
            tp = (pe.metadata or {}).get("traction_profile", "?")
            why += (
                f" | cost=${pe.monthly_cost:.1f}/mo rev~=${pe.estimated_revenue:.1f} "
                f"roi={roi_s} burn=${pe.burn_rate:.1f} traction={tp}"
            )
        lines.append(
            ProductPlanLine(
                product_id=s.product_id,
                recommended_next_step=step,
                rationale=why,
            )
        )
    return lines


def _cost_notes(signals: list[ProductSignals]) -> list[str]:
    notes: list[str] = []
    for s in signals:
        if s.over_budget:
            notes.append(
                f"{s.product_id}: declared monthly cost {s.monthly_cost_usd} exceeds cap "
                f"(see product.yaml constraints)."
            )
    return notes


def _trend_notes(repo_root: Path) -> list[str]:
    """Pull concise trend/drift lines when snapshot history exists."""
    summaries = analyze_all_with_history(repo_root)
    hot_flags = {
        TrendFlag.DRIFTING.value,
        TrendFlag.RISK_INCREASING.value,
        TrendFlag.ACTION_THRASHING.value,
        TrendFlag.LIKELY_ABANDON.value,
        TrendFlag.STAGNATING.value,
    }
    lines: list[str] = []
    for s in sorted(summaries, key=lambda x: x.product_id):
        flags = set(s.trend_flags or [])
        if flags & hot_flags or s.drift_signals:
            clip = (s.summary or "").strip().replace("\n", " ")
            if len(clip) > 160:
                clip = clip[:157] + "..."
            lines.append(f"{s.product_id}: {', '.join(s.trend_flags)} — {clip}")
    return lines[:12]


def _experiments(signals: list[ProductSignals]) -> list[str]:
    ex: list[str] = []
    missing = [s.product_id for s in signals if not s.has_findings_artifact]
    if missing:
        ex.append(
            "Run `argus findings generate` for products without a findings bundle: "
            + ", ".join(missing[:8])
            + ("..." if len(missing) > 8 else "")
        )
    stale_churn = [s.product_id for s in signals if s.churn_score is not None and s.churn_score > 0.5]
    if stale_churn:
        ex.append(
            "Review `argus decisions churn` for: " + ", ".join(stale_churn[:6])
        )
    ex.append("Re-run `argus portfolio refresh` before acting on cost-critical items.")
    return ex


def build_weekly_plan(
    repo_root: Path,
    *,
    products_dir: Path | None = None,
    planning_window: str = "next_7_days",
) -> WeeklyPlan:
    root = repo_root.resolve()
    inv = build_inventory(root, products_dir=products_dir)
    ranked = _ranked_rows(root)
    ranked_map = {str(r["product_id"]): r for r in ranked if r.get("product_id")}
    esc_counts = _escalation_counts(root)
    signals = _product_signals(root, inv, ranked_map, esc_counts)

    econ_by_pid: dict[str, ProductEconomics] = {}
    try:
        products, _ = analyze_inventory(root, products_dir=products_dir)
        econ_by_pid = {p.product_id: p for p in products}
    except Exception as e:
        warnings.warn(
            f"weekly plan: economics slice omitted ({type(e).__name__}: {e})",
            UserWarning,
            stacklevel=1,
        )

    focus = pick_focus_top3(signals)
    risks = pick_risk_top3(signals)
    hot = set(focus) | set(risks)
    watch = [w for w in pick_watchlist(signals) if w not in hot]
    hold = pick_hold(signals, set(focus), set(risks))
    deprec = pick_deprecate_review(signals)
    incub = pick_incubate(signals)

    esc_rows = list_packets(root, limit=40)
    unresolved = [
        {
            "packet_id": r.get("packet_id"),
            "product_id": r.get("product_id"),
            "created_at": r.get("created_at"),
            "risk_level": r.get("risk_level"),
            "title": r.get("title"),
        }
        for r in esc_rows
    ]

    priorities: list[str] = []
    priorities.append(
        f"Valid products: {inv.summary.valid_count}; invalid manifests: {inv.summary.invalid_count}."
    )
    priorities.append(
        f"Portfolio ranked entries: {len(ranked)} (from latest portfolio report or regenerated)."
    )
    trends = _trend_notes(root)
    if trends:
        priorities.append("Snapshot-history trends: see trend_notes / Historical trends section.")
    if focus:
        priorities.append("This week: prioritize " + ", ".join(focus) + ".")
    if risks:
        priorities.append("Risk attention: " + ", ".join(risks) + ".")

    now = datetime.now(timezone.utc).isoformat()

    return WeeklyPlan(
        generated_at_utc=now,
        planning_window=planning_window,
        repo_root=str(root),
        top_portfolio_priorities=priorities,
        per_product=_per_product_lines(signals, econ_by_pid or None),
        focus_products=focus,
        risk_focus=risks,
        watchlist=watch,
        products_to_hold=hold,
        products_to_deprecate_review=deprec,
        products_to_incubate_further=incub,
        unresolved_escalations=unresolved,
        cost_risk_notes=_cost_notes(signals),
        trend_notes=trends,
        low_effort_experiments=_experiments(signals),
        inventory_valid_count=inv.summary.valid_count,
        inventory_invalid_count=inv.summary.invalid_count,
    )
