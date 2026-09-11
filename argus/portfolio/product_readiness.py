"""
Diagnostic **product readiness** payload for onboarding rehearsal (read-only; no GitHub fetch).

Summarizes durable orchestration view + queue row + evidence maturity for one product.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from argus.builder.work_orders import load_latest_work_order_bundle
from argus.core.serialize import dumps_json
from argus.observability.signal_contract import (
    build_signal_contract_summary_from_evaluation,
    evaluate_signal_contract,
)
from argus.orchestrator.artifact_paths import orchestration_latest_path
from argus.portfolio.evidence_maturity import (
    evidence_maturity_hint_from_snapshot,
    thin_evidence_baseline_from_fingerprint,
)
from argus.portfolio.operator_queue import build_operator_queue_payload, load_operator_view
from argus.portfolio.permission_operator_surface import build_permission_gate_summary
from argus.portfolio.quiescence import _merge_queue_and_snapshot, _snapshot_fingerprint
from argus.project_permissions.gate import phase1_summary_for_product

PRODUCT_READINESS_SCHEMA = "argus.product_readiness_debug.v1"


def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        import json

        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def build_product_readiness_payload(
    repo_root: Path,
    product_id: str,
    *,
    products_dir: Path | None = None,
) -> dict[str, Any]:
    root = repo_root.resolve()
    pid = str(product_id or "").strip()
    evaluated_at = datetime.now(timezone.utc).isoformat()
    orch_path = orchestration_latest_path(root, pid)
    orch = _load_json(orch_path)
    snap, view_src = load_operator_view(root, pid)
    hint = evidence_maturity_hint_from_snapshot(snap) if snap else None

    queue_pl = build_operator_queue_payload(root, products_dir=products_dir)
    entry = None
    for e in queue_pl.get("entries") or []:
        if isinstance(e, dict) and str(e.get("product_id") or "").strip() == pid:
            entry = e
            break

    fp: dict[str, Any] | None = None
    if snap is not None:
        sfp = _snapshot_fingerprint(snap)
        qe = entry if entry else {"product_id": pid}
        fp = _merge_queue_and_snapshot(qe, sfp)
    thin = bool(fp and thin_evidence_baseline_from_fingerprint(fp))

    sc = evaluate_signal_contract(root, pid)
    signal_contract_summary = build_signal_contract_summary_from_evaluation(sc)

    wo_bundle = load_latest_work_order_bundle(root, pid)
    builder_work_orders_note: str | None = None
    if wo_bundle is not None:
        n = len(wo_bundle.get("work_orders") or [])
        builder_work_orders_note = (
            f"{n} proposed work order(s) — `runs/builder/work_orders/{pid}/latest.json` "
            f"(generate: `argus builder work-orders {pid}`)"
        )

    project_permissions = phase1_summary_for_product(
        root,
        pid,
        products_dir=products_dir,
    )
    permission_gate_summary = build_permission_gate_summary(
        root,
        pid,
        products_dir=products_dir,
    )

    expected_bootstrap = [
        "runs/signals/latest/<product>.json",
        "runs/temporal/latest/<product>.json",
        "runs/findings/latest/<product>.json",
        "runs/decisions/latest/<product>.json",
        "runs/orchestration/latest/<product>.json",
    ]

    return {
        "schema": PRODUCT_READINESS_SCHEMA,
        "evaluated_at_utc": evaluated_at,
        "product_id": pid,
        "orchestration_latest_path": str(orch_path.relative_to(root)) if orch_path.is_file() else None,
        "orchestration_evaluated_at_utc": orch.get("evaluated_at_utc") if orch else None,
        "operator_view_source": view_src,
        "operator_view_evaluated_at_utc": snap.get("source_evaluated_at_utc") if snap else None,
        "evidence_maturity_hint": hint,
        "thin_evidence_baseline": thin,
        "signal_contract_summary": signal_contract_summary,
        "builder_work_orders_note": builder_work_orders_note,
        "project_permissions": project_permissions,
        "permission_gate_summary": permission_gate_summary,
        "queue_entry": entry,
        "expected_bootstrap_artifacts_relative": expected_bootstrap,
        "note": (
            "Diagnostic only — does not run pipelines or mutate onboarding state. "
            "Use after admitting a local product to see how Argus classifies thin evidence."
        ),
    }


def render_product_readiness_markdown(payload: dict[str, Any]) -> str:
    pid = payload.get("product_id", "")
    lines = [
        f"# Product readiness (debug) — `{pid}`",
        "",
        f"**Schema:** `{payload.get('schema')}`",
        f"**Evaluated (UTC):** {payload.get('evaluated_at_utc')}",
        "",
        "## Signal contract (operability invariant)",
        "",
    ]
    pp = payload.get("project_permissions") or {}
    if pp.get("policy_load_error"):
        lines.extend(
            [
                "## Project permissions (Phase 1)",
                "",
                f"- **Policy load error:** `{pp.get('policy_load_error')}`",
                f"- **Path:** `{pp.get('policy_path') or '—'}`",
                "",
                "",
            ],
        )
    else:
        pol = pp.get("policy") or {}
        perms = pol.get("permissions") or {}
        mism = pp.get("policy_environment_mismatches") or []
        lines.extend(
            [
                "## Project permissions (Phase 1)",
                "",
                f"- **Policy file:** `{pol.get('policy_path') or '— (defaults — add products/{pid}/argus.policy.yaml)'}`",
            ]
        )
        if perms:
            lines.append("- **Stance:** " + ", ".join(f"`{k}`={v}" for k, v in sorted(perms.items())))
        if mism:
            lines.append("- **Policy vs environment:** mismatches where policy allows but env lacks support:")
            for row in mism:
                lines.append(
                    f"  - `{row.get('permission_key')}` ({row.get('policy')}): {row.get('reason')}",
                )
        else:
            lines.append("- **Policy vs environment:** no mismatches recorded (see JSON for alignment detail).")
        lines.append("")
    pg = payload.get("permission_gate_summary") or {}
    if pg:
        lines.extend(
            [
                "## Permission gates (triage)",
                "",
                f"- **One-line:** `{pg.get('one_line')}`",
                f"- **Labels:** {', '.join(f'`{x}`' for x in (pg.get('triage_labels') or [])) or '—'}",
            ],
        )
        pend = pg.get("pending_approvals") or []
        if pend:
            lines.append(f"- **Pending approval artifacts:** {len(pend)} (newest in JSON `permission_gate_summary.pending_approvals`)")
        g = pg.get("grants") or {}
        af = g.get("always_fields") or []
        if af:
            lines.append(f"- **Active always-grant fields:** {', '.join(f'`{x}`' for x in af)}")
        lines.append("")
    scs = payload.get("signal_contract_summary") or {}
    lines.extend(
        [
            f"- **{scs.get('label') or 'Signal contract'}**",
            f"- **Operability / optimization:** `{scs.get('operability_status')}` / `{scs.get('optimization_status')}`",
            f"- **Signals latest surface_state (on-disk partition):** `{scs.get('signals_latest_surface_state')}`",
            f"- **Product type bucket / mission:** `{scs.get('product_type_bucket')}` / `{scs.get('mission_id')}`",
            f"- **Golden missing / stale:** `{scs.get('golden_missing')}` / `{scs.get('golden_stale')}`",
            f"- **Mission missing:** `{scs.get('mission_missing')}`",
            f"- **Builder task candidates (descriptive):** `{scs.get('builder_task_candidates_count')}` entries — see JSON `signal_contract_summary.builder_task_candidates`",
            f"- **Builder work orders (on disk):** `{payload.get('builder_work_orders_note') or '— (none yet — run argus builder work-orders)'}`",
            "",
            "## Orchestration",
            "",
            f"- **Latest path:** `{payload.get('orchestration_latest_path')}`",
            f"- **evaluated_at_utc:** `{payload.get('orchestration_evaluated_at_utc')}`",
            f"- **Operator view source:** `{payload.get('operator_view_source')}`",
            f"- **View evaluated_at:** `{payload.get('operator_view_evaluated_at_utc')}`",
            "",
            "## Evidence maturity (thin-evidence semantics)",
            "",
            f"- **hint:** `{payload.get('evidence_maturity_hint')}`",
            f"- **thin_evidence_baseline (intervention gating):** `{payload.get('thin_evidence_baseline')}`",
            "",
            "## Queue row (if present)",
            "",
        ]
    )
    lines.extend(
        [
            "```",
            dumps_json(payload.get("queue_entry") or {}),
            "```",
            "",
            "## Expected bootstrap artifact paths (relative)",
            "",
        ]
    )
    for p in payload.get("expected_bootstrap_artifacts_relative") or []:
        lines.append(f"- `{p}`")
    lines.extend(["", str(payload.get("note") or ""), ""])
    return "\n".join(lines).rstrip() + "\n"
