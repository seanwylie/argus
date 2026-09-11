"""
Autonomous product spawn: synthesize a concept from local signals, then scaffold + register.

Inputs: trends, capability gaps, advisor-style synthesis, experiments (read-only).
Outputs (on apply): product tree, product.yaml, doctrine.yaml, initial experiment plan.

Phase 1 (plan): writes ``runs/autonomy/latest_proposal.json`` — no ``products/`` writes.
Phase 2 (apply): requires ``--approve-spawn``; subject to per-period quota.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from argus.advisors.registry import global_advisors
from argus.advisors.runner import simulate_response
from argus.capabilities.registry import infer_missing_capabilities, suggest_next_build
from argus.core.serialize import dumps_json, loads_json
from argus.doctrine.validate import validate_doctrine_raw
from argus.experiments.store import list_experiments
from argus.products.inventory import build_inventory
from argus.products.scaffold import (
    TEMPLATE_TYPES,
    create_product_scaffold,
    display_name_from_slug,
    normalize_product_slug,
)


def autonomy_dir(repo_root: Path) -> Path:
    return repo_root.resolve() / "runs" / "autonomy"


def spawn_log_path(repo_root: Path) -> Path:
    return autonomy_dir(repo_root) / "spawn_log.jsonl"


def latest_proposal_path(repo_root: Path) -> Path:
    return autonomy_dir(repo_root) / "latest_proposal.json"


def _dump_yaml(data: dict[str, Any]) -> str:
    try:
        import yaml  # type: ignore[import-untyped]
    except ImportError as e:  # pragma: no cover
        raise ImportError("YAML support requires the 'pyyaml' package.") from e
    return yaml.safe_dump(
        data,
        sort_keys=True,
        default_flow_style=False,
        allow_unicode=True,
    )


def load_trends_summaries(repo_root: Path) -> list[dict[str, Any]]:
    p = repo_root.resolve() / "runs" / "trends" / "latest.json"
    if not p.is_file():
        return []
    try:
        raw = loads_json(p.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return []
    if not isinstance(raw, dict):
        return []
    summaries = raw.get("summaries")
    if not isinstance(summaries, list):
        return []
    out: list[dict[str, Any]] = []
    for s in summaries:
        if isinstance(s, dict) and s.get("product_id"):
            out.append(
                {
                    "product_id": str(s.get("product_id")),
                    "trend_flags": list(s.get("trend_flags") or []),
                    "confidence": s.get("confidence"),
                    "summary": (str(s.get("summary") or ""))[:400],
                }
            )
    return out


def gather_spawn_signals(
    repo_root: Path,
    *,
    products_dir: Path | None = None,
) -> dict[str, Any]:
    """Collect trends, gaps, lightweight advisor synthesis, and experiment activity."""
    root = repo_root.resolve()
    trends = load_trends_summaries(root)
    missing = infer_missing_capabilities(root)
    next_gap_id = suggest_next_build(missing)
    gap_rows = [
        {
            "id": m.id,
            "name": m.name,
            "category": m.category,
            "priority": m.priority,
            "description": m.description[:300],
        }
        for m in sorted(missing, key=lambda x: (x.priority, x.id))[:12]
    ]

    exps = sorted(list_experiments(root), key=lambda e: e.id)
    exp_summary = {
        "total": len(exps),
        "by_status": {},
        "sample_hypotheses": [],
    }
    for e in exps:
        exp_summary["by_status"][e.status.value] = exp_summary["by_status"].get(e.status.value, 0) + 1
    for e in exps[-8:]:
        exp_summary["sample_hypotheses"].append(
            {"product_id": e.product_id, "hypothesis": e.hypothesis[:200], "type": e.type.value}
        )

    inv = build_inventory(root, products_dir=products_dir)
    advisor_notes: list[dict[str, str]] = []
    seed_pid = sorted(inv.valid.keys())[0] if inv.valid else "spawn_seed"
    for adv in global_advisors()[:3]:
        resp = simulate_response(adv, seed_pid, context="portfolio_spawn_synthesis")
        md = resp.metadata or {}
        advisor_notes.append(
            {
                "advisor_id": adv.id,
                "recommendation": (resp.recommendation or "")[:280],
                "stance": str(md.get("stance", "")),
            }
        )
    advisor_notes.sort(key=lambda x: x["advisor_id"])

    return {
        "schema": "argus.spawn_signals.v1",
        "trends_summaries": sorted(trends, key=lambda x: x.get("product_id", "")),
        "capability_gaps": gap_rows,
        "suggested_gap_id": next_gap_id,
        "experiments": exp_summary,
        "advisor_synthesis": advisor_notes,
        "inventory_product_count": inv.summary.valid_count,
    }


def _concept_fingerprint(signals: dict[str, Any]) -> str:
    # Exclude wall-clock timestamps so repeated proposals in the same repo state match.
    stable = {k: v for k, v in signals.items() if k != "gathered_at_utc"}
    payload = json.dumps(stable, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()[:10]


def _pick_template(gap_id: str | None, gaps: list[dict[str, Any]]) -> str:
    if gap_id:
        cat = next((g["category"] for g in gaps if g["id"] == gap_id), None)
        if cat == "execution":
            return "utility_api"
        if cat == "ui":
            return "static_site"
        if cat == "ingestion":
            return "micro_saas"
        if cat == "analysis":
            return "content_stream"
    return "micro_saas"


def _thesis_lines(signals: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    gid = signals.get("suggested_gap_id")
    if gid:
        lines.append(f"Primary opportunity signal: address capability gap {gid}.")
    hot = [t for t in signals.get("trends_summaries") or [] if any(
        x in ",".join(t.get("trend_flags") or []).lower()
        for x in ("drift", "risk", "thrash", "abandon")
    )]
    if hot:
        lines.append(
            f"Trend pressure on {hot[0].get('product_id')}: "
            f"{', '.join(hot[0].get('trend_flags') or [])}."
        )
    if (signals.get("experiments") or {}).get("total", 0) > 0:
        lines.append("Active experiment culture — new node should ship measurable hypotheses early.")
    if not lines:
        lines.append("Structural exploration: diversify portfolio with a bounded experimental product node.")
    return lines


@dataclass
class SpawnProposal:
    """Serializable plan before apply."""

    schema: str = "argus.spawn_proposal.v1"
    proposed_slug: str = ""
    display_name: str = ""
    template_type: str = "micro_saas"
    thesis: list[str] = field(default_factory=list)
    signals: dict[str, Any] = field(default_factory=dict)
    fingerprint: str = ""
    doctrine_draft: dict[str, Any] = field(default_factory=dict)
    experiment_plan: dict[str, Any] = field(default_factory=dict)
    requires_approval: bool = True
    quota_note: str = ""


def build_spawn_proposal(
    repo_root: Path,
    *,
    products_dir: Path | None = None,
) -> SpawnProposal:
    signals = gather_spawn_signals(repo_root, products_dir=products_dir)
    fp = _concept_fingerprint(signals)
    slug = normalize_product_slug(f"auto-{fp[:8]}")
    template = _pick_template(signals.get("suggested_gap_id"), signals.get("capability_gaps") or [])
    if template not in TEMPLATE_TYPES:
        template = "micro_saas"
    thesis = _thesis_lines(signals)

    doctrine_draft: dict[str, Any] = {
        "schema": "argus.doctrine.v1",
        "summary": (
            "Spawned node: prioritize learning velocity, bounded spend, and explicit kill criteria "
            f"(fingerprint {fp})."
        ),
        "principles": [
            "Ship the smallest experiment that falsifies the core assumption.",
            "No silent production changes; Argus signals must reflect reality.",
            "Revisit doctrine after first successful experiment window.",
        ],
        "constraints": {
            "max_monthly_cost_usd": 75.0,
            "require_human_review_when_kill_candidate": True,
        },
        "scoring": {
            "intent_priority_multiplier": {
                "launch_experiment": 1.12,
                "gather_more_data": 1.06,
            },
            "experiment_score_boost": 0.04,
        },
    }
    validate_doctrine_raw(doctrine_draft)

    experiment_plan = {
        "schema": "argus.spawn_experiment_plan.v1",
        "product_id": slug,
        "title": f"Initial validation for {slug}",
        "phases": [
            {
                "name": "instrument",
                "goal": "Wire metrics + snapshots so Argus can observe the node.",
                "success_metrics": ["signal_record_count", "first_snapshot_ingested"],
            },
            {
                "name": "hypothesis_test",
                "goal": "Run one bounded experiment tied to the spawn thesis.",
                "success_metrics": ["experiment_status", "evaluation_verdict"],
            },
        ],
        "first_experiment": {
            "hypothesis": thesis[0] if thesis else "The new surface delivers measurable value vs baseline.",
            "type": "growth",
            "success_metrics": ["activation_proxy", "weekly_active_signal"],
        },
    }

    return SpawnProposal(
        proposed_slug=slug,
        display_name=display_name_from_slug(slug),
        template_type=template,
        thesis=thesis,
        signals=signals,
        fingerprint=fp,
        doctrine_draft=doctrine_draft,
        experiment_plan=experiment_plan,
        requires_approval=True,
    )


def quota_remaining(
    repo_root: Path,
    *,
    max_per_period: int,
    period_days: int,
) -> tuple[int, str]:
    """
    Count successful applies in the rolling window from ``spawn_log.jsonl``.

    Returns ``(remaining, note)``.
    """
    root = repo_root.resolve()
    log_path = spawn_log_path(root)
    if not log_path.is_file():
        return max_per_period, f"no prior spawns (limit {max_per_period} per {period_days}d)"

    cutoff = datetime.now(timezone.utc) - timedelta(days=period_days)
    n = 0
    for line in log_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        ts = str(row.get("applied_at_utc") or "")
        try:
            t = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            if t.tzinfo is None:
                t = t.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if t >= cutoff:
            n += 1

    rem = max(0, max_per_period - n)
    return rem, f"{n} spawn(s) in last {period_days}d (limit {max_per_period})"


def _unique_slug(repo_root: Path, base: str, products_dir: Path | None) -> str:
    root = repo_root.resolve()
    pdir = (root / "products") if products_dir is None else products_dir.resolve()
    slug = base
    if not (pdir / slug).exists():
        return slug
    for i in range(2, 50):
        cand = f"{base}-{i}"
        if not (pdir / cand).exists():
            return cand
    return f"{base}-x"


def write_proposal_file(repo_root: Path, proposal: SpawnProposal) -> Path:
    d = autonomy_dir(repo_root)
    d.mkdir(parents=True, exist_ok=True)
    path = latest_proposal_path(repo_root)
    payload = {
        "schema": proposal.schema,
        "gathered_at_utc": datetime.now(timezone.utc).isoformat(),
        "proposed_slug": proposal.proposed_slug,
        "display_name": proposal.display_name,
        "template_type": proposal.template_type,
        "thesis": proposal.thesis,
        "fingerprint": proposal.fingerprint,
        "signals": proposal.signals,
        "doctrine_draft": proposal.doctrine_draft,
        "experiment_plan": proposal.experiment_plan,
        "requires_approval": proposal.requires_approval,
    }
    path.write_text(dumps_json(payload), encoding="utf-8")
    return path


def apply_spawn_proposal(
    repo_root: Path,
    proposal: SpawnProposal,
    *,
    products_dir: Path | None = None,
) -> tuple[int, dict[str, Any]]:
    """
    Create product scaffold, doctrine.yaml, experiment plan; append spawn log.

    Returns ``(exit_code, payload)``.
    """
    root = repo_root.resolve()
    from argus.autonomy.quotas import check_product_spawn_allowed, record_product_spawn

    ok, qmsg = check_product_spawn_allowed(root)
    if not ok:
        return 1, {"error": "spawn_quota", "message": qmsg}

    slug = _unique_slug(root, proposal.proposed_slug, products_dir)

    code, msg, _git = create_product_scaffold(
        root,
        slug,
        template_type=proposal.template_type,
        products_dir=products_dir,
        force=False,
    )
    if code != 0:
        return code, {"error": "scaffold_failed", "message": msg}

    product_root = Path(msg).resolve()
    slug = product_root.name

    doctrine_path = product_root / "doctrine.yaml"
    doctrine_path.write_text(_dump_yaml(proposal.doctrine_draft), encoding="utf-8")

    plan_path = product_root / "experiment_plan.yaml"
    ep = dict(proposal.experiment_plan)
    ep["product_id"] = slug
    plan_path.write_text(_dump_yaml(ep), encoding="utf-8")

    now = datetime.now(timezone.utc).isoformat()
    log_line = {
        "applied_at_utc": now,
        "product_id": slug,
        "template_type": proposal.template_type,
        "fingerprint": proposal.fingerprint,
        "paths": {
            "product_yaml": str(product_root / "product.yaml"),
            "doctrine_yaml": str(doctrine_path),
            "experiment_plan": str(plan_path),
        },
    }
    log_p = spawn_log_path(root)
    log_p.parent.mkdir(parents=True, exist_ok=True)
    with log_p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(log_line, sort_keys=True) + "\n")

    record_product_spawn(root)

    return 0, {
        "ok": True,
        "product_id": slug,
        "product_root": str(product_root),
        "paths": log_line["paths"],
    }


def load_latest_proposal(repo_root: Path) -> SpawnProposal | None:
    p = latest_proposal_path(repo_root)
    if not p.is_file():
        return None
    try:
        raw = loads_json(p.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    if not isinstance(raw, dict):
        return None
    return SpawnProposal(
        proposed_slug=str(raw.get("proposed_slug", "")),
        display_name=str(raw.get("display_name", "")),
        template_type=str(raw.get("template_type", "micro_saas")),
        thesis=list(raw.get("thesis") or []),
        signals=dict(raw.get("signals") or {}),
        fingerprint=str(raw.get("fingerprint", "")),
        doctrine_draft=dict(raw.get("doctrine_draft") or {}),
        experiment_plan=dict(raw.get("experiment_plan") or {}),
        requires_approval=bool(raw.get("requires_approval", True)),
    )
