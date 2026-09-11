"""
Issue Argus-native work orders for signal instrumentation (steward → worker handoff).

Consumes latest ``argus.product_signal_instrumentation.v1`` artifacts only; does not mutate products.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Final

from argus.core.serialize import to_jsonable
from argus.portfolio.lifecycle import evaluate_portfolio_lifecycle
from argus.products.inventory import build_inventory
from argus.products.signal_instrumentation import (
    load_full_signal_instrumentation_payload,
    signal_instrumentation_latest_dir,
)
from argus.worker.work_orders import (
    WORK_ORDER_SCHEMA,
    create_work_order,
    write_work_order_artifacts,
)

INSTRUMENTATION_WORK_ORDER_ISSUANCE_SCHEMA: Final = "argus.instrumentation_work_order_issuance.v1"
REQUEST_TYPE_SIGNAL_INSTRUMENTATION: Final = "signal_instrumentation"
SELECTED_BY_ARGUS_CORE: Final = "argus_core"

_QUALIFYING_STATUSES: Final = frozenset({"weak", "sparse", "missing"})


def _iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _priority_for_status(status: str) -> str:
    s = str(status).strip().lower()
    if s == "missing":
        return "high"
    if s == "sparse":
        return "high"
    if s == "weak":
        return "normal"
    return "normal"


def _lifecycle_pressure_note(repo_root: Path, product_id: str, products_dir: Path | None) -> str | None:
    """One cross-check line when lifecycle already flags instrumentation pressure for this product."""
    try:
        pl = evaluate_portfolio_lifecycle(repo_root, products_dir=products_dir)
    except Exception:
        return None
    pids = pl.get("products_under_instrumentation_pressure") or []
    if str(product_id).strip() in [str(x).strip() for x in pids]:
        return (
            "Portfolio lifecycle synthesis also lists this product under signal instrumentation pressure "
            "(consistent with latest instrumentation assessment)."
        )
    return None


def _mission_context_for_product(repo_root: Path, product_id: str, products_dir: Path | None) -> dict[str, Any] | None:
    inv = build_inventory(repo_root, products_dir=products_dir)
    rec = inv.valid.get(str(product_id).strip())
    if rec is None:
        return None
    node = rec.node
    out: dict[str, Any] = {}
    mid = getattr(node, "mission_id", None)
    if mid and str(mid).strip():
        out["mission_id"] = str(mid).strip()
    miss = getattr(node, "mission", None)
    if miss is not None:
        out["mission"] = to_jsonable(miss)
    return out if out else None


def build_signal_instrumentation_work_order(
    repo_root: Path,
    *,
    inst_payload: dict[str, Any],
    products_dir: Path | None = None,
    selected_at_utc: str | None = None,
    include_lifecycle_cross_check: bool = True,
) -> dict[str, Any]:
    """
    Build a single ``argus.work_order.v1`` from a full instrumentation payload dict.

    Caller must ensure ``inst_payload`` is ok and status qualifies (weak/sparse/missing).
    """
    pid = str(inst_payload.get("product_id") or "").strip()
    status = str(inst_payload.get("instrumentation_status") or "").strip().lower()
    rel_inst = f"runs/products/signal_instrumentation/latest/{pid}.json"
    sources = [rel_inst]
    rationale_parts = [
        f"Latest signal instrumentation assessment for `{pid}` is `{status}` (not adequate). "
        "Argus core requests observability improvements before optimization-style loops rely on this product's signals.",
        str(inst_payload.get("recommended_next_step") or "").strip(),
    ]
    if include_lifecycle_cross_check:
        extra = _lifecycle_pressure_note(repo_root, pid, products_dir)
        if extra:
            rationale_parts.append(extra)
            sources.append("runs/portfolio/lifecycle/latest.json")
    rationale = "\n\n".join(p for p in rationale_parts if p)

    mission_context = _mission_context_for_product(repo_root, pid, products_dir)

    contract = inst_payload.get("proposed_signal_contract")
    if not isinstance(contract, dict):
        contract = {}
    seed: dict[str, Any] = {
        "instrumentation_status": status,
        "missing_signal_dimensions": list(inst_payload.get("missing_signal_dimensions") or []),
        "dimension_coverage_levels": dict(inst_payload.get("dimension_coverage_levels") or {}),
        "proposed_signal_contract_schema": contract.get("schema"),
    }

    acceptance = [
        (
            f"After changes, `argus products instrument-signals --product-id {pid}` reports "
            "`instrumentation_status` of `adequate`, or the work order is superseded with documented blockers."
        ),
        "Extend `signal_manifest` / `signals.yaml` and/or `metrics.primary` per proposed_signal_contract for gaps listed.",
        "Do not present synthetic seed signals as real business telemetry.",
    ]

    risk_notes = [
        "Instrumentation artifacts may include synthetic/structural placeholders — not real KPIs.",
        "Worker executes only what the work order states; do not expand scope to unrelated refactors.",
    ]

    return create_work_order(
        product_id=pid,
        request_type=REQUEST_TYPE_SIGNAL_INSTRUMENTATION,
        selected_by=SELECTED_BY_ARGUS_CORE,
        rationale=rationale,
        source_artifact_paths=sources,
        acceptance_criteria=acceptance,
        implementation_seed=seed,
        priority=_priority_for_status(status),
        autonomy_mode="supervised",
        approval_required=True,
        mission_context=mission_context,
        risk_notes=risk_notes,
        recommended_worker_mode="apply_patch",
        selected_at_utc=selected_at_utc,
    )


def evaluate_instrumentation_work_order_issuance(
    repo_root: Path,
    *,
    products_dir: Path | None = None,
    product_id: str | None = None,
    selected_at_utc: str | None = None,
    include_lifecycle_cross_check: bool = True,
) -> dict[str, Any]:
    """
    Determine which products qualify and build work order payloads (no disk write).

    Emits one work order per qualifying product that has a valid latest instrumentation artifact.
    """
    root = Path(repo_root).resolve()
    evaluated_at = selected_at_utc or _iso_now()
    latest = signal_instrumentation_latest_dir(root)
    skipped: list[dict[str, Any]] = []
    issued: list[dict[str, Any]] = []

    candidates: list[str]
    if product_id is not None and str(product_id).strip():
        candidates = [str(product_id).strip()]
    else:
        if not latest.is_dir():
            return {
                "schema": INSTRUMENTATION_WORK_ORDER_ISSUANCE_SCHEMA,
                "evaluated_at_utc": evaluated_at,
                "issued_work_orders": [],
                "skipped": [
                    {"product_id": None, "reason": "no_signal_instrumentation_latest_directory"}
                ],
                "inputs": {
                    "products_dir": str(products_dir) if products_dir is not None else None,
                    "product_id_filter": product_id,
                },
            }
        candidates = sorted(
            p.stem for p in latest.glob("*.json") if p.is_file() and not p.name.startswith(".")
        )

    for pid in candidates:
        full = load_full_signal_instrumentation_payload(root, pid)
        if full is None:
            skipped.append({"product_id": pid, "reason": "no_valid_signal_instrumentation_artifact"})
            continue
        if not full.get("ok"):
            skipped.append({"product_id": pid, "reason": "instrumentation_payload_not_ok"})
            continue
        st = str(full.get("instrumentation_status") or "").strip().lower()
        if st == "adequate":
            skipped.append({"product_id": pid, "reason": "instrumentation_status_adequate"})
            continue
        if st not in _QUALIFYING_STATUSES:
            skipped.append({"product_id": pid, "reason": f"instrumentation_status_not_eligible:{st or 'empty'}"})
            continue

        wo = build_signal_instrumentation_work_order(
            root,
            inst_payload=full,
            products_dir=products_dir,
            selected_at_utc=evaluated_at,
            include_lifecycle_cross_check=include_lifecycle_cross_check,
        )
        if str(wo.get("schema") or "") != WORK_ORDER_SCHEMA:
            skipped.append({"product_id": pid, "reason": "work_order_build_failed"})
            continue
        issued.append(wo)

    return {
        "schema": INSTRUMENTATION_WORK_ORDER_ISSUANCE_SCHEMA,
        "evaluated_at_utc": evaluated_at,
        "issued_work_orders": issued,
        "skipped": skipped,
        "inputs": {
            "products_dir": str(products_dir) if products_dir is not None else None,
            "product_id_filter": product_id,
            "include_lifecycle_cross_check": include_lifecycle_cross_check,
        },
    }


def issue_instrumentation_work_orders(
    repo_root: Path,
    *,
    write_artifacts: bool = True,
    products_dir: Path | None = None,
    product_id: str | None = None,
    include_lifecycle_cross_check: bool = True,
) -> dict[str, Any]:
    """
    Evaluate issuance and write each work order via :func:`write_work_order_artifacts`.

    When ``write_artifacts`` is false, returns the same evaluation payload without writing files.
    """
    root = Path(repo_root).resolve()
    payload = evaluate_instrumentation_work_order_issuance(
        root,
        products_dir=products_dir,
        product_id=product_id,
        include_lifecycle_cross_check=include_lifecycle_cross_check,
    )
    if not write_artifacts:
        payload["artifacts_written"] = []
        return payload

    written: list[str] = []
    for wo in payload.get("issued_work_orders") or []:
        if str(wo.get("schema") or "") != WORK_ORDER_SCHEMA:
            continue
        write_work_order_artifacts(root, wo, write_latest=True)
        wid = str(wo.get("work_order_id") or "")
        if wid:
            written.append(wid)
    payload["artifacts_written"] = sorted(set(written))
    return payload


__all__ = [
    "INSTRUMENTATION_WORK_ORDER_ISSUANCE_SCHEMA",
    "REQUEST_TYPE_SIGNAL_INSTRUMENTATION",
    "SELECTED_BY_ARGUS_CORE",
    "build_signal_instrumentation_work_order",
    "evaluate_instrumentation_work_order_issuance",
    "issue_instrumentation_work_orders",
    "load_full_signal_instrumentation_payload",
]
