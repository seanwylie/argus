"""Markdown and terminal rendering for weekly plans."""

from __future__ import annotations

from pathlib import Path

from argus.core.serialize import dumps_json, to_jsonable
from argus.planning.models import WeeklyPlan


def render_markdown(plan: WeeklyPlan) -> str:
    lines: list[str] = [
        "# Argus weekly portfolio plan",
        "",
        f"**Generated:** {plan.generated_at_utc}",
        f"**Window:** {plan.planning_window}",
        f"**Repo:** `{plan.repo_root}`",
        "",
        "## Portfolio state",
        "",
    ]
    lines.append(
        f"- Valid products: **{plan.inventory_valid_count}** · "
        f"invalid manifests: **{plan.inventory_invalid_count}**"
    )
    for b in plan.top_portfolio_priorities:
        lines.append(f"- {b}")
    lines.extend(["", "## This week's focus (top 3)", ""])
    if plan.focus_products:
        for pid in plan.focus_products:
            lines.append(f"- **{pid}**")
    else:
        lines.append("- *(none — empty or insufficient portfolio data)*")
    lines.extend(["", "## Risk focus (top 3)", ""])
    if plan.risk_focus:
        for pid in plan.risk_focus:
            lines.append(f"- **{pid}**")
    else:
        lines.append("- *(none)*")
    lines.extend(["", "## Watchlist (leave alone unless signals change)", ""])
    for pid in plan.watchlist:
        lines.append(f"- {pid}")
    if not plan.watchlist:
        lines.append("- *(none)*")
    lines.extend(["", "## Hold / stable (no urgent pull)", ""])
    for pid in plan.products_to_hold[:15]:
        lines.append(f"- {pid}")
    if not plan.products_to_hold:
        lines.append("- *(none)*")
    lines.extend(["", "## Kill / deprecate review", ""])
    for pid in plan.products_to_deprecate_review:
        lines.append(f"- **{pid}**")
    if not plan.products_to_deprecate_review:
        lines.append("- *(none flagged)*")
    lines.extend(["", "## Incubate further (early stage, low noise)", ""])
    for pid in plan.products_to_incubate_further:
        lines.append(f"- {pid}")
    if not plan.products_to_incubate_further:
        lines.append("- *(none)*")
    lines.extend(["", "## Cost / risk notes", ""])
    for n in plan.cost_risk_notes:
        lines.append(f"- {n}")
    if not plan.cost_risk_notes:
        lines.append("- *(no budget breaches detected from manifests)*")
    lines.extend(["", "## Historical trends (snapshot history)", ""])
    for n in plan.trend_notes:
        lines.append(f"- {n}")
    if not plan.trend_notes:
        lines.append(
            "- *(no drift-style trend signals from history — run `argus history snapshot` "
            "periodically, then `argus trends summary`)*"
        )
    lines.extend(["", "## Escalations needing judgment", ""])
    for e in plan.unresolved_escalations[:25]:
        pid = e.get("product_id", "?")
        title = (e.get("title") or "")[:100]
        lines.append(f"- `{e.get('packet_id')}` · **{pid}** · {e.get('risk_level')} — {title}")
    if not plan.unresolved_escalations:
        lines.append("- *(no packets in latest escalation index)*")
    lines.extend(["", "## Suggested low-effort experiments", ""])
    for x in plan.low_effort_experiments:
        lines.append(f"- {x}")
    lines.extend(["", "## Per-product next step", ""])
    for pl in plan.per_product:
        lines.append(f"### {pl.product_id}")
        lines.append(f"- **Next:** {pl.recommended_next_step}")
        lines.append(f"- *Context:* {pl.rationale}")
        lines.append("")
    md = "\n".join(lines).rstrip() + "\n"
    try:
        from argus.self_improvement.planning import append_self_improvement_weekly_section

        md = append_self_improvement_weekly_section(md, Path(plan.repo_root))
    except (ImportError, OSError, TypeError, ValueError):
        pass
    return md


def render_terminal_summary(plan: WeeklyPlan) -> str:
    lines = [
        "=== Argus weekly plan ===",
        f"generated: {plan.generated_at_utc}",
        f"inventory: {plan.inventory_valid_count} valid",
        "",
        "Focus (3): " + (", ".join(plan.focus_products) or "—"),
        "Risks (3): " + (", ".join(plan.risk_focus) or "—"),
        "Watchlist: " + (", ".join(plan.watchlist) or "—"),
        "Deprecate review: " + (", ".join(plan.products_to_deprecate_review) or "—"),
        f"Escalations listed: {len(plan.unresolved_escalations)}",
        f"Trend notes: {len(plan.trend_notes)}",
        "",
        "Experiments:",
    ]
    for x in plan.low_effort_experiments[:5]:
        lines.append(f"  - {x}")
    lines.append("")
    return "\n".join(lines) + "\n"


def plan_to_json(plan: WeeklyPlan) -> str:
    return dumps_json(to_jsonable(plan))
