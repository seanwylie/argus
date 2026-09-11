"""
Portfolio-facing Builder activity rollup — coordination / visibility only.

Derived from :func:`argus.builder.status.compute_builder_status` (same truth as CLI ``builder status``).
Does **not** encode learning, strategy outcomes, or product success claims.

**Refresh:** ``argus portfolio builder-activity`` writes this artifact explicitly. The same files are also
updated when you run **``argus dashboard summary``** (observational reporting bundle — not wired into
portfolio outcomes, strategy, or the autonomous runner).

**Operator summary:** :func:`build_operator_summary_builder_snapshot` derives a compact attention/recent-runs
view for :mod:`argus.dashboard.operator_summary` (embedded JSON; markdown section in ``latest.md``).
Attention rows include ``attention_products`` (flat) and ``attention_products_grouped`` (scope/safety,
blocked/failed, needs review) — same ``reasons`` codes; grouping is presentation-only.
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from argus.builder.history_rollup import (
    builder_history_row_from_status,
    format_recent_run_compact_line,
)
from argus.builder.multi_product_view import (
    builder_row_has_visibility,
    list_inventory_builder_candidate_ids,
)
from argus.builder.outcome import (
    BUILDER_OUTCOME_SCHEMA,
    build_builder_outcome_payload,
    outcome_summary_for_portfolio,
    write_builder_outcome_artifact,
)
from argus.builder.status import compute_builder_status
from argus.core.serialize import dumps_json
from argus.portfolio.builder_activity_attention import (
    group_attention_products_by_category,
)
from argus.products.inventory import build_inventory

PORTFOLIO_BUILDER_ACTIVITY_SCHEMA = "argus.portfolio_builder_activity.v1"

_SOURCE_REFS = [
    "products/<product_id>/content/next_expansion.json",
    "products/<product_id>/generated/builder_task.json",
    "runs/builder/invoke/<product_id>/latest.json",
    "runs/builder/reconcile/<product_id>/latest.json",
    "runs/builder/outcome/<product_id>/latest.json (argus.builder_outcome.v1 — Phase 3 bridge)",
    "runs/builder/outcome/<product_id>/history_tail.json (short rolling prior outcomes for Phase 3B rollup)",
    "runs/signals/latest/<product_id>.json (optional signal continuity)",
    "runs/escalations/latest/esc_*.json (metadata.builder_escalation)",
]

_DISCLAIMER = (
    "Coordination snapshot only: latest Builder invoke/reconcile signals as-of generation time. "
    "Embedded `builder_outcome_summaries` are a minimal Phase 3 feedback bridge (artifact-grounded, "
    "non-causal). Not portfolio strategy, autonomous learning, or causal product-success claims."
)


def portfolio_builder_activity_dir(repo_root: Path) -> Path:
    return Path(repo_root).resolve() / "runs" / "portfolio" / "builder_activity"


def public_builder_activity_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Strip internal keys (e.g. embedded outcome payloads) before JSON to stdout or logs."""
    return {k: v for k, v in payload.items() if not str(k).startswith("_")}


def product_row_from_builder_status(payload: dict[str, Any]) -> dict[str, Any]:
    """Single portfolio row — compact rollup from status (see :func:`builder_history_row_from_status`)."""
    return builder_history_row_from_status(payload)


def build_portfolio_builder_activity_payload(
    repo_root: Path,
    *,
    products_dir: Path | None = None,
    max_inventory_products: int = 512,
) -> dict[str, Any]:
    root = repo_root.resolve()
    now = datetime.now(timezone.utc)
    generated = now.isoformat().replace("+00:00", "Z")
    run_id = now.strftime("%Y%m%dT%H%M%SZ")

    inv = build_inventory(root, products_dir=products_dir)
    ids = sorted(inv.valid.keys())[:max_inventory_products]
    candidates = list_inventory_builder_candidate_ids(root, ids)
    paired: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for pid in candidates:
        st_payload = compute_builder_status(root, pid, products_dir=products_dir)
        if not builder_row_has_visibility(st_payload):
            continue
        row = product_row_from_builder_status(st_payload)
        outcome = build_builder_outcome_payload(
            root,
            pid,
            products_dir=products_dir,
            status_payload=st_payload,
        )
        paired.append((row, outcome))

    paired.sort(
        key=lambda x: str(x[0].get("updated_at_utc") or ""),
        reverse=True,
    )
    products = [p[0] for p in paired]
    outcome_payloads = [p[1] for p in paired]
    summaries = [outcome_summary_for_portfolio(o, root) for o in outcome_payloads]

    return {
        "schema": PORTFOLIO_BUILDER_ACTIVITY_SCHEMA,
        "run_id": run_id,
        "generated_at_utc": generated,
        "products_dir": str(products_dir) if products_dir is not None else None,
        "inventory_products_scanned": len(ids),
        "builder_candidates_considered": len(candidates),
        "product_count": len(products),
        "source_refs": list(_SOURCE_REFS),
        "disclaimer": _DISCLAIMER,
        "products": products,
        "builder_outcome_schema": BUILDER_OUTCOME_SCHEMA,
        "builder_outcome_summaries": summaries,
        "_builder_outcome_payloads": outcome_payloads,
    }


def _attention_reasons_from_history_row(row: dict[str, Any]) -> list[str]:
    """Deterministic codes for operator-summary attention (artifact-grounded only)."""
    out: list[str] = []
    if row.get("scope_breach") is True:
        out.append("scope_breach")
    if row.get("path_scope_breach") is True:
        out.append("path_scope_breach")
    if row.get("semantic_scope_breach") is True:
        out.append("semantic_scope_breach")
    rs = str(row.get("review_status") or "").strip().lower()
    if rs in ("unsafe", "blocked"):
        out.append(f"review_{rs}")
    if str(row.get("invocation_status") or "").strip().lower() == "failed":
        out.append("invoke_failed")
    eo = str(row.get("execution_outcome") or "").strip().lower()
    if eo in ("breached", "blocked"):
        out.append(f"outcome_{eo}")
    if row.get("operator_visible_escalation") is True:
        out.append("escalation_inbox_visible")
    emit = row.get("reconcile_escalation_emit")
    if isinstance(emit, dict) and emit.get("emitted") is True:
        out.append("reconcile_escalation_emitted")
    th = row.get("trust_highlights")
    if isinstance(th, list) and len(th) > 0:
        out.append("trust_flags")
    return out


def _summarize_portfolio_builder_activity_for_operator_summary(pl: dict[str, Any]) -> dict[str, Any]:
    products = [x for x in (pl.get("products") or []) if isinstance(x, dict)]
    attention: list[dict[str, Any]] = []
    routine = 0
    for row in products:
        reasons = _attention_reasons_from_history_row(row)
        if reasons:
            pid = str(row.get("product_id") or "?")
            attention.append(
                {
                    "product_id": pid,
                    "reasons": reasons[:8],
                    "headline": f"{pid}: " + ", ".join(reasons[:4]),
                }
            )
        else:
            routine += 1
    recent = sorted(
        products,
        key=lambda r: str(r.get("updated_at_utc") or ""),
        reverse=True,
    )[:12]
    sums = [x for x in (pl.get("builder_outcome_summaries") or []) if isinstance(x, dict)]
    outcome_by_pid = {
        str(s.get("product_id") or "").strip(): s
        for s in sums
        if str(s.get("product_id") or "").strip()
    }
    recent_compact = []
    for r in recent:
        cl = r.get("compact_line")
        if not isinstance(cl, str) or not cl.strip():
            cl = format_recent_run_compact_line(r)
        pid_r = str(r.get("product_id") or "").strip()
        oc_row = outcome_by_pid.get(pid_r)
        recent_compact.append(
            {
                "product_id": r.get("product_id"),
                "updated_at": r.get("updated_at_utc"),
                "invoke": r.get("invocation_status"),
                "merge": r.get("merge_readiness"),
                "outcome": r.get("execution_outcome"),
                "trust": r.get("trust_posture"),
                "cleanup_hint": r.get("cleanup_hint"),
                "recommended_next_action": r.get("recommended_next_action"),
                "recommended_next_label": r.get("recommended_next_label"),
                "contract": r.get("execution_contract_kind") or r.get("declared_target_type"),
                "compact_line": cl,
                "builder_outcome_compact": (oc_row or {}).get("outcome_operator_one_liner")
                or (oc_row or {}).get("outcome_compact_line"),
                "comparison_evidence_strength": (oc_row or {}).get("comparison_evidence_strength"),
            }
        )
    if attention:
        next_hint = (
            "Review attention rows (scope, failed invoke, outcomes, or escalation signals) before merge."
        )
    elif products:
        next_hint = "No attention flags in this rollup — routine monitoring."
    else:
        next_hint = "No products in rollup."
    trust_line = (
        f"{len(products)} product(s) in rollup · {len(attention)} need attention · {routine} routine"
    )
    attention_slice = attention[:12]
    grouped = group_attention_products_by_category(attention_slice)
    preview_lines = [
        str(
            s.get("outcome_operator_one_liner")
            or s.get("outcome_compact_line")
            or ""
        ).strip()
        for s in sums
        if str(s.get("outcome_operator_one_liner") or s.get("outcome_compact_line") or "").strip()
    ][:12]
    return {
        "artifact_present": True,
        "schema": "argus.operator_summary_builder_snapshot.v1",
        "generated_at_utc": pl.get("generated_at_utc"),
        "run_id": pl.get("run_id"),
        "source_artifact": "runs/portfolio/builder_activity/latest.json",
        "product_count": len(products),
        "attention_count": len(attention),
        "routine_count": routine,
        "attention_products": attention_slice,
        "attention_products_grouped": grouped,
        "recent_runs": recent_compact,
        "trust_summary_line": trust_line,
        "next_action_hint": next_hint,
        "builder_outcome_summaries": pl.get("builder_outcome_summaries") or [],
        "builder_outcome_lines_preview": preview_lines,
        "builder_outcome_schema": pl.get("builder_outcome_schema"),
    }


def build_operator_summary_builder_snapshot(repo_root: Path) -> dict[str, Any]:
    """
    Compact Builder rollup for :func:`argus.dashboard.operator_summary.evaluate_operator_summary`.

    Reads ``runs/portfolio/builder_activity/latest.json`` when present; otherwise a graceful empty
    object (no I/O beyond that file).
    """
    p = portfolio_builder_activity_dir(repo_root) / "latest.json"
    if not p.is_file():
        return {
            "artifact_present": False,
            "operator_hint": (
                "No `runs/portfolio/builder_activity/latest.json` yet — run "
                "`argus portfolio builder-activity` or `argus dashboard summary`."
            ),
        }
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {
            "artifact_present": False,
            "artifact_unreadable": True,
            "operator_hint": "Builder activity file exists but could not be parsed as JSON.",
        }
    if not isinstance(raw, dict) or raw.get("schema") != PORTFOLIO_BUILDER_ACTIVITY_SCHEMA:
        return {
            "artifact_present": False,
            "schema_mismatch": True,
            "operator_hint": "Builder activity file is not a valid portfolio Builder activity payload.",
        }
    return _summarize_portfolio_builder_activity_for_operator_summary(raw)


def render_portfolio_builder_activity_markdown(payload: dict[str, Any]) -> str:
    """Short markdown for ``latest.md`` alongside JSON."""
    lines = [
        "# Portfolio Builder activity",
        "",
        f"**Generated:** `{payload.get('generated_at_utc')}` · **run_id:** `{payload.get('run_id')}`",
        "",
    ]
    sums_by = {
        str(s.get("product_id") or "").strip(): s
        for s in (payload.get("builder_outcome_summaries") or [])
        if isinstance(s, dict) and str(s.get("product_id") or "").strip()
    }
    for p in payload.get("products") or []:
        if not isinstance(p, dict):
            continue
        pid = p.get("product_id") or "?"
        ct = p.get("execution_contract_kind") or p.get("declared_target_type") or "—"
        lines.append(
            f"- **{pid}** · updated `{p.get('updated_at_utc') or '—'}` · contract/target `{ct}` · "
            f"invoke `{p.get('invocation_status')}` · merge `{p.get('merge_readiness')}` · "
            f"outcome `{p.get('execution_outcome')}` · scope `{p.get('scope_breach')}` · "
            f"trust `{p.get('trust_posture')}`"
        )
        ocs = sums_by.get(str(pid).strip())
        oline = (ocs.get("outcome_operator_one_liner") or ocs.get("outcome_compact_line")) if isinstance(
            ocs, dict
        ) else None
        if isinstance(ocs, dict) and oline:
            lines.append(f"  - **Phase 3 builder_outcome** (non-causal): {oline}")
        if p.get("escalation_summary_line"):
            lines.append(f"  - escalation: {p.get('escalation_summary_line')}")
        emit = p.get("reconcile_escalation_emit")
        if isinstance(emit, dict) and emit.get("emitted"):
            lines.append(
                f"  - reconcile emit: `{emit.get('packet_id')}` — `{emit.get('path_repo')}`"
            )
        if p.get("escalation_packet_path_repo"):
            lines.append(f"  - escalation packet: `{p.get('escalation_packet_path_repo')}`")
    lines.extend(["", str(payload.get("disclaimer") or _DISCLAIMER), ""])
    return "\n".join(lines)


def write_portfolio_builder_activity_artifacts(
    repo_root: Path,
    *,
    products_dir: Path | None = None,
    payload: dict[str, Any] | None = None,
) -> tuple[Path, Path, Path]:
    """
    Write stamped JSON, ``latest.json`` (copy), and ``latest.md``.

    Returns ``(stamped_json, latest_json, latest_md)``.
    """
    pl = payload if payload is not None else build_portfolio_builder_activity_payload(
        repo_root,
        products_dir=products_dir,
    )
    embedded = pl.pop("_builder_outcome_payloads", None)
    if isinstance(embedded, list):
        for oc in embedded:
            if isinstance(oc, dict) and oc.get("schema") == BUILDER_OUTCOME_SCHEMA:
                write_builder_outcome_artifact(repo_root, oc)
    else:
        for row in pl.get("products") or []:
            if not isinstance(row, dict):
                continue
            pid = str(row.get("product_id") or "").strip()
            if not pid:
                continue
            oc = build_builder_outcome_payload(repo_root, pid, products_dir=products_dir)
            write_builder_outcome_artifact(repo_root, oc)
    d = portfolio_builder_activity_dir(repo_root)
    d.mkdir(parents=True, exist_ok=True)
    rid = str(pl.get("run_id") or "").strip()
    if not rid:
        raise ValueError("portfolio builder activity payload missing run_id")
    text = dumps_json(pl) + "\n"
    stamped = d / f"{rid}.json"
    latest = d / "latest.json"
    stamped.write_text(text, encoding="utf-8")
    shutil.copyfile(stamped, latest)
    md = render_portfolio_builder_activity_markdown(pl)
    latest_md = d / "latest.md"
    latest_md.write_text(md, encoding="utf-8")
    # Lazy import avoids circular / partial init when ``dashboard`` loads ``operator_summary`` early.
    from argus.portfolio.builder_outcome_validation import write_builder_outcome_validation_artifact

    write_builder_outcome_validation_artifact(repo_root)
    return stamped, latest, latest_md
