"""
Kill justification report: assemble explainable evidence from local Argus artifacts.

Produces Markdown (human review) and a structured JSON payload (audit / tooling).
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from argus.core.models.product import ProductNode
from argus.core.serialize import dumps_json, to_jsonable
from argus.decision.history.analyze import analyze_churn, churn_report_to_jsonable
from argus.decision.history.models import DecisionMemoryEntry
from argus.decision.history.store import load_product_decision_history
from argus.economics.analyze import analyze_product_economics
from argus.escalation.packet import list_packets
from argus.experiments.store import list_experiments
from argus.findings.persistence import (
    generations_dir,
    load_findings_file,
    load_latest_findings,
)
from argus.lifecycle.kill import KillScoreResult, compute_kill_score_for_product
from argus.signals.persistence import load_latest_bundle

_SCHEMA = "argus.kill_justification_report.v1"

_FINDINGS_GEN = re.compile(r"^(\d{8}T\d{6}Z)_(.+)\.json$")


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _product_summary(node: ProductNode) -> dict[str, Any]:
    ti = node.type_info
    return {
        "id": node.id,
        "name": node.name,
        "product_root": node.product_root,
        "lifecycle_stage": node.lifecycle.stage.value,
        "owner_team": node.owner.team,
        "tags": list(node.tags),
        "type": ti.type if ti else None,
        "status": ti.status if ti else None,
        "constraints": {
            "max_monthly_cost_usd": node.constraints.max_monthly_cost_usd,
        },
    }


def _signal_metrics(repo: Path, product_id: str) -> dict[str, Any]:
    b = load_latest_bundle(repo, product_id)
    if b is None:
        return {
            "bundle_present": False,
            "record_count": 0,
            "collected_at_utc": None,
            "by_signal_type": {},
            "custom_business_signals": [],
        }
    by_type: dict[str, int] = {}
    business: list[str] = []
    for r in b.records:
        k = r.signal_type.value
        by_type[k] = by_type.get(k, 0) + 1
        p = r.payload or {}
        bs = p.get("business_signal")
        if bs:
            business.append(str(bs))
    return {
        "bundle_present": True,
        "record_count": len(b.records),
        "collected_at_utc": b.collected_at_utc,
        "by_signal_type": by_type,
        "custom_business_signals_sample": sorted(set(business))[:24],
    }


def _findings_history(repo: Path, product_id: str) -> list[dict[str, Any]]:
    base = generations_dir(repo.resolve())
    if not base.is_dir():
        return []
    rows: list[tuple[str, Path]] = []
    for p in base.glob("*.json"):
        m = _FINDINGS_GEN.match(p.name)
        if not m or m.group(2) != product_id:
            continue
        rows.append((m.group(1), p))
    rows.sort(key=lambda x: x[0])
    out: list[dict[str, Any]] = []
    for ts, path in rows[-20:]:
        try:
            bundle = load_findings_file(path)
        except (OSError, ValueError):
            continue
        kinds: dict[str, int] = {}
        sev: dict[str, int] = {}
        for f in bundle.findings:
            kinds[f.kind.value] = kinds.get(f.kind.value, 0) + 1
            sev[f.severity.value] = sev.get(f.severity.value, 0) + 1
        out.append(
            {
                "generation_timestamp": ts,
                "generated_at_utc": bundle.generated_at_utc,
                "source_path": str(path),
                "finding_count": len(bundle.findings),
                "by_kind": kinds,
                "by_severity": sev,
            }
        )
    return out


def _findings_latest_snapshot(repo: Path, product_id: str) -> dict[str, Any]:
    b = load_latest_findings(repo, product_id)
    if b is None:
        return {"present": False, "findings": []}
    return {
        "present": True,
        "generated_at_utc": b.generated_at_utc,
        "finding_count": len(b.findings),
        "findings": [to_jsonable(f) for f in b.findings],
    }


def _experiments_block(repo: Path, product_id: str) -> list[dict[str, Any]]:
    exps = list_experiments(repo, product_id=product_id)
    rows: list[dict[str, Any]] = []
    for e in exps:
        rows.append(
            {
                "id": e.id,
                "hypothesis": e.hypothesis,
                "type": e.type.value,
                "status": e.status.value,
                "confidence": e.confidence,
                "start_at": e.start_at,
                "end_at": e.end_at,
                "success_metrics": list(e.success_metrics),
                "last_evaluation_verdict": e.last_evaluation_verdict,
                "last_evaluation_at": e.last_evaluation_at,
                "last_evaluation_summary": e.last_evaluation_summary,
            }
        )
    return rows


def _cost_block(repo: Path, product_id: str, node: ProductNode) -> dict[str, Any]:
    bundle = load_latest_bundle(repo, product_id)
    records = bundle.records if bundle is not None else []
    econ = analyze_product_economics(node, records)
    return {
        "monthly_cost_usd": econ.monthly_cost,
        "estimated_revenue_usd": econ.estimated_revenue,
        "burn_rate_usd": econ.burn_rate,
        "roi_estimate": econ.roi_estimate,
        "growth_signal": econ.growth_signal.value,
        "revenue_source": econ.revenue_source,
        "observed_monthly_cost_usd": econ.observed_monthly_cost,
        "max_monthly_cost_cap_usd": node.constraints.max_monthly_cost_usd,
        "interpretation": _cost_interpretation(econ.monthly_cost, econ.estimated_revenue, econ.burn_rate),
    }


def _cost_interpretation(cost: float, rev: float, burn: float) -> str:
    if cost <= 0 and rev <= 0:
        return "No declared monthly cost and no MRR observed in local signals; economics signal is weak."
    if rev <= 0 < cost:
        return (
            f"Declared spend ~${cost:.2f}/mo with no estimated revenue from ingested snapshots — "
            "value delivery is unproven vs. cost."
        )
    if burn > 0:
        return f"Net burn ~${burn:.2f}/mo (cost minus estimated revenue)."
    return "Estimated revenue meets or exceeds declared monthly cost in local data."


def _decision_history_block(repo: Path, product_id: str) -> dict[str, Any]:
    entries = load_product_decision_history(repo, product_id)
    churn = analyze_churn(product_id, entries, repo_root=repo)
    serialized = [_entry_summary(e) for e in entries]
    return {
        "run_count": len(entries),
        "entries": serialized,
        "churn": churn_report_to_jsonable(churn),
    }


def _entry_summary(e: DecisionMemoryEntry) -> dict[str, Any]:
    return {
        "generated_at_utc": e.generated_at_utc,
        "top_intent": e.top_intent,
        "top_recommended_action_excerpt": (e.top_recommended_action or "")[:400],
        "compared_to_previous": e.compared_to_previous,
        "lifecycle_stage": e.lifecycle_stage,
        "kill_candidate": e.kill_candidate,
        "confidence": e.confidence,
    }


def _escalations_block(repo: Path, product_id: str) -> list[dict[str, Any]]:
    rows = [r for r in list_packets(repo, limit=400) if str(r.get("product_id")) == product_id]
    return list(rows)


def _dimension_narratives(ks: KillScoreResult) -> list[dict[str, str]]:
    """Short, defensible lines tied to each kill-score dimension."""
    d = ks.dimensions
    notes = ks.notes
    lines: list[dict[str, str]] = [
        {
            "dimension": "inactivity",
            "score_0_1": f"{d.get('inactivity', 0):.3f}",
            "why": (
                f"Signal freshness: ~{float(notes.get('inactivity_days', 0)):.1f} days since newest "
                f"observation; no_usage in snapshots={notes.get('no_usage_signal', False)}. "
                "Stale or absent activity increases wind-down risk."
            ),
        },
        {
            "dimension": "findings_trend",
            "score_0_1": f"{d.get('findings_trend', 0):.3f}",
            "why": (
                f"Latest findings count={notes.get('findings_count', 0)}; "
                f"trend flags={notes.get('trend_flags') or []}. "
                "Higher severity / risk-kind findings and adverse trends raise this score."
            ),
        },
        {
            "dimension": "cost_vs_value",
            "score_0_1": f"{d.get('cost_vs_value', 0):.3f}",
            "why": (
                f"Declared cost ${float(notes.get('monthly_cost_usd', 0)):.2f}/mo vs "
                f"estimated revenue ${float(notes.get('estimated_revenue_usd', 0)):.2f} from local signals. "
                "Poor unit economics increase kill pressure."
            ),
        },
        {
            "dimension": "experiment_failure",
            "score_0_1": f"{d.get('experiment_failure', 0):.3f}",
            "why": (
                "Proxy from finding kinds and severities (risk / deprecation / inactivity). "
                "Represents product health and failed improvement signals, not lab A/B tooling alone."
            ),
        },
        {
            "dimension": "decision_churn",
            "score_0_1": f"{d.get('decision_churn', 0):.3f}",
            "why": (
                f"Decision history runs={notes.get('decision_runs', 0)}. "
                "Oscillating recommendations reduce confidence in sustained investment."
            ),
        },
        {
            "dimension": "escalation",
            "score_0_1": f"{d.get('escalation', 0):.3f}",
            "why": (
                f"Escalation packets recorded for product={notes.get('escalation_packets', 0)}. "
                "Repeated human handoffs suggest elevated operational or policy risk."
            ),
        },
    ]
    return lines


def _executive_summary(
    product_id: str,
    ks: KillScoreResult,
    cost: dict[str, Any],
) -> str:
    rec = ks.recommendation.value
    lines = [
        f"**{product_id}** — Argus kill score **{ks.kill_score}/100** "
        f"(recommendation: **{rec}**). Higher scores mean stronger evidence for winding down.",
        "",
        "This report is assembled from **local repository data only** (manifests, runs/signals, "
        "runs/findings, runs/decisions, runs/escalations, experiments). It is a structured aid for "
        "human review, not an automated action.",
        "",
        f"- **Economics (local):** {cost.get('interpretation', '')}",
        f"- **Coarse next step:** interpret `{rec}` alongside your org’s policy; "
        f"deprecate/kill suggestions require explicit owner approval outside Argus.",
    ]
    return "\n".join(lines)


def build_kill_justification_report(
    repo_root: Path,
    product_id: str,
    node: ProductNode,
) -> dict[str, Any]:
    """Full JSON-serializable report for one product."""
    root = repo_root.resolve()
    ks = compute_kill_score_for_product(root, product_id, node)
    cost = _cost_block(root, product_id, node)

    report: dict[str, Any] = {
        "schema": _SCHEMA,
        "generated_at_utc": _utc_iso(),
        "product_id": product_id,
        "product_summary": _product_summary(node),
        "key_metrics": {
            "signals": _signal_metrics(root, product_id),
            "findings_latest": _findings_latest_snapshot(root, product_id),
        },
        "findings_history": _findings_history(root, product_id),
        "experiments": _experiments_block(root, product_id),
        "cost_analysis": cost,
        "decision_history": _decision_history_block(root, product_id),
        "escalations_index": _escalations_block(root, product_id),
        "kill_score": ks.to_jsonable(),
        "narrative": {
            "executive_summary_markdown": _executive_summary(product_id, ks, cost),
            "dimension_explanations": _dimension_narratives(ks),
        },
    }
    return report


def render_kill_justification_markdown(report: dict[str, Any]) -> str:
    """Render the JSON report structure as review-friendly Markdown."""
    pid = str(report.get("product_id", ""))
    lines: list[str] = [
        f"# Kill justification: `{pid}`",
        "",
        report.get("narrative", {}).get("executive_summary_markdown", ""),
        "",
        "## Product summary",
        "",
    ]
    ps = report.get("product_summary") or {}
    lines.append(
        f"| Field | Value |\n| --- | --- |\n"
        f"| Name | {ps.get('name', '')} |\n"
        f"| Lifecycle | {ps.get('lifecycle_stage', '')} |\n"
        f"| Owner team | {ps.get('owner_team', '')} |\n"
        f"| Max monthly cost cap (USD) | {ps.get('constraints', {}).get('max_monthly_cost_usd')} |\n"
    )

    lines.extend(["", "## Key metrics (signals & latest findings)", ""])
    km = report.get("key_metrics") or {}
    sig = km.get("signals") or {}
    lines.append(f"- **Signal records (latest bundle):** {sig.get('record_count', 0)}")
    lines.append(f"- **Collected at:** {sig.get('collected_at_utc', 'n/a')}")
    if sig.get("by_signal_type"):
        lines.append(f"- **By signal type:** `{sig.get('by_signal_type')}`")
    fl = km.get("findings_latest") or {}
    lines.append(f"- **Latest findings count:** {fl.get('finding_count', 0)}")
    lines.append("")

    lines.append("## Findings history (recent generations)")
    lines.append("")
    fh = report.get("findings_history") or []
    if not fh:
        lines.append("*No findings generation files found under `runs/findings/generations/`.*")
    else:
        lines.append("| Generation (file ts) | Count | Kinds |")
        lines.append("| --- | ---: | --- |")
        for row in fh[-12:]:
            kinds = row.get("by_kind") or {}
            ks = ", ".join(f"{k}:{v}" for k, v in sorted(kinds.items())[:8])
            lines.append(
                f"| `{row.get('generation_timestamp', '')}` | {row.get('finding_count', 0)} | {ks or '—'} |"
            )
    lines.append("")

    lines.append("## Experiments")
    lines.append("")
    exps = report.get("experiments") or []
    if not exps:
        lines.append("*No experiments under `runs/experiments/` for this product.*")
    else:
        lines.append("| ID | Type | Status | Verdict | Hypothesis (excerpt) |")
        lines.append("| --- | --- | --- | --- | --- |")
        for e in exps[-15:]:
            hyp = (e.get("hypothesis") or "")[:80]
            lines.append(
                f"| `{e.get('id', '')}` | {e.get('type')} | {e.get('status')} | "
                f"{e.get('last_evaluation_verdict') or '—'} | {hyp} |"
            )
    lines.append("")

    lines.append("## Cost analysis (local)")
    lines.append("")
    ca = report.get("cost_analysis") or {}
    lines.append(f"- {ca.get('interpretation', '')}")
    lines.append(
        f"- Monthly cost (declared): **${ca.get('monthly_cost_usd', 0):.2f}** · "
        f"Estimated revenue (signals): **${ca.get('estimated_revenue_usd', 0):.2f}** · "
        f"Burn: **${ca.get('burn_rate_usd', 0):.2f}** · Growth: **{ca.get('growth_signal')}**"
    )
    lines.append("")

    lines.append("## Decision history")
    lines.append("")
    dh = report.get("decision_history") or {}
    ch = dh.get("churn") or {}
    lines.append(f"- **Runs recorded:** {dh.get('run_count', 0)}")
    lines.append(f"- **Churn score:** {ch.get('churn_score')} (stability {ch.get('stability_score')})")
    for s in (ch.get("summary_lines") or [])[:8]:
        lines.append(f"  - {s}")
    lines.append("")
    lines.append("| When (UTC) | Intent | vs prev | kill_candidate |")
    lines.append("| --- | --- | --- | --- |")
    for e in (dh.get("entries") or [])[-12:]:
        lines.append(
            f"| {e.get('generated_at_utc', '')[:19]} | `{e.get('top_intent', '')}` | "
            f"{e.get('compared_to_previous')} | {e.get('kill_candidate')} |"
        )
    lines.append("")

    lines.append("## Escalations (index)")
    lines.append("")
    esc = report.get("escalations_index") or []
    if not esc:
        lines.append("*No escalation packets in `runs/escalations/latest/` for this product.*")
    else:
        for r in esc[:12]:
            lines.append(
                f"- `{r.get('packet_id')}` — {r.get('created_at')} — {r.get('risk_level')}"
            )
    lines.append("")

    lines.append("## Kill score breakdown")
    lines.append("")
    ks = report.get("kill_score") or {}
    lines.append(f"- **Score:** {ks.get('kill_score')} / 100 · **Recommendation:** `{ks.get('recommendation')}`")
    lines.append("")
    lines.append("| Dimension (0–1, higher=worse) | Weight | Contribution |")
    lines.append("| --- | ---: | ---: |")
    weights = ks.get("weights") or {}
    dims = ks.get("dimensions") or {}
    total = 0.0
    for name in sorted(weights.keys()):
        w = float(weights.get(name) or 0)
        dv = float(dims.get(name) or 0)
        c = w * dv
        total += c
        lines.append(f"| {name} | {w:.2f} | {c:.3f} |")
    lines.append(f"| **Weighted sum** | 1.00 | **{total:.3f}** → **{ks.get('kill_score')}**/100 |")
    lines.append("")
    for block in report.get("narrative", {}).get("dimension_explanations") or []:
        lines.append(f"### {block.get('dimension')}")
        lines.append("")
        lines.append(f"*{block.get('why', '')}*")
        lines.append("")

    lines.append("---")
    lines.append("")
    lines.append(
        f"*Generated at {report.get('generated_at_utc', '')} · schema `{report.get('schema', '')}`*"
    )
    return "\n".join(lines)


def report_to_json_string(report: dict[str, Any]) -> str:
    return dumps_json(report)
