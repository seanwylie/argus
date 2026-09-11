"""
Multi-product Builder visibility — derived rows from :func:`compute_builder_status` only.

Phase 2B: operators see which inventory products have Builder activity, merge posture, and escalations
without opening per-product JSON.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from argus.builder.history_rollup import (
    builder_history_row_from_status,
    format_recent_run_compact_line,
)
from argus.builder.status import compute_builder_status
from argus.products.inventory import build_inventory

BUILDER_MULTI_PRODUCT_VIEW_SCHEMA = "argus.builder_multi_product_view.v1"


def _has_invoke_or_reconcile_record(repo_root: Path, product_id: str) -> bool:
    root = repo_root.resolve()
    pid = str(product_id).strip()
    if not pid:
        return False
    return (
        (root / "runs" / "builder" / "invoke" / pid / "latest.json").is_file()
        or (root / "runs" / "builder" / "reconcile" / pid / "latest.json").is_file()
    )


def _builder_escalation_product_ids(repo_root: Path) -> set[str]:
    """Product ids with a Builder-tagged packet under ``runs/escalations/latest``."""
    root = repo_root.resolve()
    lat = root / "runs" / "escalations" / "latest"
    out: set[str] = set()
    if not lat.is_dir():
        return out
    for p in lat.glob("esc_*.json"):
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(raw, dict):
            continue
        md = raw.get("metadata") if isinstance(raw.get("metadata"), dict) else {}
        if not md.get("builder_escalation"):
            continue
        pid = str(raw.get("product_id") or "").strip()
        if pid:
            out.add(pid)
    return out


def list_inventory_builder_candidate_ids(
    repo_root: Path,
    inventory_ids: list[str],
) -> list[str]:
    """
    Validated inventory product ids that have a Builder invoke/reconcile ``latest.json`` **or** a
    Builder-tagged escalation packet — cheap pre-filter before :func:`compute_builder_status`.

    Used by :mod:`argus.portfolio.builder_activity` and :func:`build_builder_multi_product_view`.
    """
    return _candidate_inventory_ids(repo_root, inventory_ids)


def _candidate_inventory_ids(
    repo_root: Path,
    inventory_ids: list[str],
) -> list[str]:
    """
    Inventory products worth calling :func:`compute_builder_status` for.

    Cheap pre-filter: latest invoke/reconcile path exists, or a Builder escalation packet names the product.
    """
    esc = _builder_escalation_product_ids(repo_root)
    out: list[str] = []
    for pid in inventory_ids:
        if _has_invoke_or_reconcile_record(repo_root, pid) or pid in esc:
            out.append(pid)
    return sorted(out)


def builder_row_has_visibility(payload: dict[str, Any]) -> bool:
    """True when the status payload should appear in the multi-product table."""
    osum = payload.get("operator_summary") if isinstance(payload.get("operator_summary"), dict) else {}
    if osum.get("has_builder_activity"):
        return True
    le = payload.get("last_escalation") if isinstance(payload.get("last_escalation"), dict) else {}
    if le.get("present"):
        return True
    lr = payload.get("latest_reconcile") if isinstance(payload.get("latest_reconcile"), dict) else {}
    if lr.get("escalation_emit_emitted"):
        return True
    return False


def _escalation_cell(payload: dict[str, Any]) -> str:
    le = payload.get("last_escalation") if isinstance(payload.get("last_escalation"), dict) else {}
    lr = payload.get("latest_reconcile") if isinstance(payload.get("latest_reconcile"), dict) else {}
    parts: list[str] = []
    if le.get("present"):
        sev = str(le.get("severity") or "?")
        if le.get("operator_visible"):
            parts.append(f"packet {sev} (inbox)")
        else:
            parts.append(f"packet {sev}")
    if lr.get("escalation_emit_emitted"):
        pkt = lr.get("escalation_emit_packet_id") or "packet"
        parts.append(f"reconcile emit ({pkt})")
    elif lr.get("escalation_emit_reason") == "dedupe_recent_packet":
        parts.append("reconcile dedupe")
    return " · ".join(parts) if parts else "—"


def row_from_builder_status(payload: dict[str, Any]) -> dict[str, Any]:
    """Flatten status + operator_summary + history rollup into one table row (short strings)."""
    rollup = builder_history_row_from_status(payload)
    osum = payload.get("operator_summary") if isinstance(payload.get("operator_summary"), dict) else {}
    align = str(osum.get("alignment_headline") or osum.get("alignment_summary") or "").strip()
    if len(align) > 160:
        align = align[:157] + "..."
    nxt = str(osum.get("suggested_next") or "").strip()
    if len(nxt) > 220:
        nxt = nxt[:217] + "..."
    eo = osum.get("execution_outcome")
    eo_s = "—" if eo is None or eo == "" else str(eo)
    ct = rollup.get("execution_contract_kind") or rollup.get("declared_target_type")
    ct_s = "—" if ct is None or ct == "" else str(ct)
    ts = rollup.get("updated_at_utc")
    ts_s = "—" if ts is None or ts == "" else str(ts)
    tp = rollup.get("trust_posture")
    tp_s = "—" if tp is None or tp == "" else str(tp)
    inv_s = rollup.get("invocation_status")
    inv_s = "—" if inv_s is None or inv_s == "" else str(inv_s)
    compact = format_recent_run_compact_line(rollup)
    return {
        "product_id": str(payload.get("product_id") or ""),
        "updated_at_utc": ts_s,
        "contract_or_target_type": ct_s,
        "invocation_status": inv_s,
        "alignment": align,
        "merge_readiness": str(osum.get("merge_readiness") or "—"),
        "execution_outcome": eo_s,
        "trust_posture": tp_s,
        "cleanup_hint": rollup.get("cleanup_hint"),
        "recommended_next_action": rollup.get("recommended_next_action"),
        "recommended_next_label": osum.get("recommended_next_label"),
        "escalation": _escalation_cell(payload),
        "next": nxt,
        "compact_line": compact,
        "latest_invoke_path": rollup.get("latest_invoke_path"),
        "latest_reconcile_path": rollup.get("latest_reconcile_path"),
    }


def build_builder_multi_product_view(
    repo_root: Path,
    *,
    products_dir: Path | None = None,
    max_inventory_products: int = 512,
) -> dict[str, Any]:
    """
    One row per inventory product that has Builder-visible state.

    Rows are derived only from :func:`compute_builder_status` (no parallel truth model).
    """
    root = repo_root.resolve()
    inv = build_inventory(root, products_dir=products_dir)
    ids = sorted(inv.valid.keys())[:max_inventory_products]
    candidates = list_inventory_builder_candidate_ids(root, ids)
    rows: list[dict[str, Any]] = []
    for pid in candidates:
        payload = compute_builder_status(root, pid, products_dir=products_dir)
        if not builder_row_has_visibility(payload):
            continue
        rows.append(row_from_builder_status(payload))

    empty_msg = (
        "No Builder invoke/reconcile records or Builder escalation packets for any inventory product. "
        "Run `argus builder status <id>` after invoke/reconcile, or use the single-product Builder snapshot."
    )
    return {
        "schema": BUILDER_MULTI_PRODUCT_VIEW_SCHEMA,
        "products_scanned": len(ids),
        "candidates_considered": len(candidates),
        "row_count": len(rows),
        "rows": rows,
        "empty_message": empty_msg if not rows else None,
    }


def format_builder_multi_product_view_human(view: dict[str, Any]) -> str:
    """Plain-text multi-product summary for CLI."""
    if int(view.get("row_count") or 0) == 0:
        return (view.get("empty_message") or "No Builder multi-product rows.") + "\n"
    lines = [
        "Builder — inventory products with activity or escalation",
        f"  (scanned {view.get('products_scanned')} id(s), "
        f"{view.get('candidates_considered')} with invoke/reconcile or escalation packet)",
        "",
    ]
    for r in view.get("rows") or []:
        if not isinstance(r, dict):
            continue
        pid = r.get("product_id") or "?"
        cl = str(r.get("compact_line") or "").strip()
        if cl:
            lines.append(f"  {cl}")
        else:
            lines.append(f"  {pid}")
        if r.get("cleanup_hint"):
            lines.append(f"    cleanup: {r.get('cleanup_hint')}")
        lines.append(
            f"    updated: {r.get('updated_at_utc')} · contract/target: {r.get('contract_or_target_type')} · "
            f"invoke: {r.get('invocation_status')}"
        )
        lines.append(
            f"    merge: {r.get('merge_readiness')} · outcome: {r.get('execution_outcome')} · "
            f"trust: {r.get('trust_posture')} · escalation: {r.get('escalation')}"
        )
        lines.append(f"    alignment: {r.get('alignment')}")
        lines.append(f"    next: {r.get('next')}")
        lines.append("")
    return "\n".join(lines) + "\n"
