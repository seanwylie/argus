"""Assemble weekly-style self-improvement plan and persist artifacts."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from argus.self_improvement.findings import generate_self_findings
from argus.self_improvement.models import SelfImprovementPlan
from argus.self_improvement.prioritize import rank_proposals, resolve_strategy_mode
from argus.self_improvement.propose import proposals_from_findings


def self_improvement_dir(repo_root: Path) -> Path:
    return repo_root.resolve() / "runs" / "self_improvement"


def build_self_improvement_plan(repo_root: Path) -> SelfImprovementPlan:
    root = repo_root.resolve()
    mode = resolve_strategy_mode(root)
    findings = generate_self_findings(root)
    proposals = proposals_from_findings(findings)
    ranked = rank_proposals(proposals, strategy_mode=mode)

    top_n = 7
    top_ids = [r.proposal.id for r in ranked[:top_n]]
    defer_ids = [r.proposal.id for r in ranked[top_n:]]

    high_risk = [p.id for p in proposals if p.requires_operator_approval or p.risk.value == "high"]

    notes: list[str] = []
    notes.append("Planning-only output: does not modify Argus or run self-patching.")
    if mode is not None:
        notes.append(f"Strategy mode: {mode.value} (from runs/strategy/current.json).")
    else:
        notes.append("No runs/strategy/current.json — using default alignment weights.")

    plan = SelfImprovementPlan(
        generated_at_utc=datetime.now(timezone.utc).isoformat(),
        repo_root=str(root),
        findings=findings,
        proposals=proposals,
        ranked=ranked,
        top_this_week=top_ids,
        defer=defer_ids,
        high_risk_require_approval=high_risk,
        strategy_mode=mode.value if mode else None,
        notes=notes,
    )
    return plan


def render_plan_markdown(plan: SelfImprovementPlan) -> str:
    lines: list[str] = [
        "# Argus self-improvement plan",
        "",
        f"**Generated:** {plan.generated_at_utc}",
        f"**Repo:** `{plan.repo_root}`",
        "",
        "> This document is **planning only**. It does not apply code changes or execute self-patches.",
        "",
        "## Top self-improvements this week",
        "",
    ]
    id_to_prop = {p.id: p for p in plan.proposals}
    if not plan.top_this_week:
        lines.append("- *(no proposals — run in a populated repo or review findings)*")
    else:
        for i, pid in enumerate(plan.top_this_week, 1):
            p = id_to_prop.get(pid)
            if not p:
                continue
            lines.append(f"{i}. **{p.title}** (`{p.id}`)")
            lines.append(f"   - {p.summary[:500]}")
            lines.append(f"   - kind={p.kind.value} · risk={p.risk.value} · score≈{_score_for(plan, pid):.3f}")
            lines.append("")
    lines.extend(["## Defer (later)", ""])
    for pid in plan.defer[:20]:
        p = id_to_prop.get(pid)
        if p:
            lines.append(f"- {p.title} (`{pid}`)")
    if not plan.defer:
        lines.append("- *(none)*")
    lines.extend(["", "## High-risk self-changes (require explicit approval)", ""])
    for pid in plan.high_risk_require_approval:
        p = id_to_prop.get(pid)
        if p:
            lines.append(f"- **{p.title}** — {p.rationale} (`{pid}`)")
    if not plan.high_risk_require_approval:
        lines.append("- *(none flagged)*")
    lines.extend(["", "## Notes", ""])
    for n in plan.notes:
        lines.append(f"- {n}")
    lines.extend(["", "## Findings snapshot", ""])
    for f in plan.findings[:24]:
        lines.append(f"- [{f.kind.value}] {f.title}")
    if len(plan.findings) > 24:
        lines.append(f"- … {len(plan.findings) - 24} more")
    return "\n".join(lines).rstrip() + "\n"


def _score_for(plan: SelfImprovementPlan, proposal_id: str) -> float:
    for r in plan.ranked:
        if r.proposal.id == proposal_id:
            return r.total_score
    return 0.0


def write_plan_artifacts(repo_root: Path, plan: SelfImprovementPlan) -> dict[str, Path]:
    """Write JSON + Markdown under runs/self_improvement/."""
    root = repo_root.resolve()
    d = self_improvement_dir(root)
    d.mkdir(parents=True, exist_ok=True)
    payload = plan.to_jsonable()
    paths: dict[str, Path] = {}
    latest = d / "plan_latest.json"
    latest.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    paths["plan_latest.json"] = latest
    md = d / "plan_latest.md"
    md.write_text(render_plan_markdown(plan), encoding="utf-8")
    paths["plan_latest.md"] = md
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    snap = d / f"plan_{ts}.json"
    snap.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    paths["snapshot"] = snap
    return paths


def load_latest_plan_json(repo_root: Path) -> dict[str, Any] | None:
    p = self_improvement_dir(repo_root.resolve()) / "plan_latest.json"
    if not p.is_file():
        return None
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return raw if isinstance(raw, dict) else None


def append_self_improvement_weekly_section(markdown: str, repo_root: Path) -> str:
    """Append a short section when ``plan_latest.json`` exists (weekly plan integration)."""
    raw = load_latest_plan_json(repo_root)
    if not raw:
        return markdown
    lines: list[str] = [
        "",
        "## Argus self-improvement (meta, planning-only)",
        "",
        "> From `runs/self_improvement/plan_latest.json`. Does not modify Argus automatically.",
        "",
    ]
    top = raw.get("top_this_week") or []
    props = {p["id"]: p for p in (raw.get("proposals") or []) if isinstance(p, dict)}
    if not top:
        lines.append("- *(no self-improvement plan — run `argus self improve plan`)*")
    else:
        for pid in top[:5]:
            p = props.get(pid)
            if isinstance(p, dict):
                t = str(p.get("title") or pid)
                lines.append(f"- **{t}**")
    hr = raw.get("high_risk_require_approval") or []
    if hr:
        lines.append("")
        lines.append("**High-risk / approval-required:** " + ", ".join(str(x) for x in hr[:8]))
    return markdown.rstrip() + "\n" + "\n".join(lines) + "\n"


def prompt_excerpt_for_advisors(repo_root: Path, *, max_chars: int = 3500) -> str:
    """Short text for advisor prompts (optional section)."""
    raw = load_latest_plan_json(repo_root)
    if not raw:
        return ""
    lines: list[str] = []
    top = raw.get("top_this_week") or []
    props = {p["id"]: p for p in (raw.get("proposals") or []) if isinstance(p, dict)}
    lines.append("Self-improvement (meta, planning-only):")
    for pid in top[:6]:
        p = props.get(pid)
        if isinstance(p, dict):
            lines.append(f"- {p.get('title', pid)}: {(p.get('summary') or '')[:400]}")
    hr = raw.get("high_risk_require_approval") or []
    if hr:
        lines.append("High-risk / approval-required proposal ids: " + ", ".join(str(x) for x in hr[:12]))
    out = "\n".join(lines)
    return out if len(out) <= max_chars else out[: max_chars - 3] + "..."
