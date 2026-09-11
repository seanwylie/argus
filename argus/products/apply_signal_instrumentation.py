"""
Deterministic application of steward-approved signal instrumentation work orders.

Mutates only minimal observability contract surfaces (typically ``signals.yaml`` and light
``product.yaml`` touches). No LLM; does not claim synthetic placeholders are live telemetry.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Final

from argus.core.serialize import dumps_json, to_jsonable
from argus.products.loader import load_yaml_file
from argus.products.paths import join_under_product
from argus.products.signal_instrumentation import (
    ALL_DIMENSIONS,
    PRODUCT_SIGNAL_INSTRUMENTATION_SCHEMA,
    evaluate_product_signal_instrumentation,
    load_full_signal_instrumentation_payload,
)
from argus.products.signal_manifest import (
    resolve_signal_manifest_payload,
    validate_signal_manifest_dict,
)
from argus.products.validate import validate_manifest

PRODUCT_SIGNAL_INSTRUMENTATION_APPLY_SCHEMA: Final = "argus.product_signal_instrumentation_apply.v1"

_DIM_TO_CATEGORY: Final[dict[str, str]] = {
    "activity_usage": "operational",
    "growth_trend": "temporal",
    "health_quality": "quality",
    "freshness_recency": "operational",
    "mission_relevant_metrics": "business",
}


def signal_instrumentation_apply_dir(repo_root: Path) -> Path:
    return Path(repo_root).resolve() / "runs" / "products" / "signal_instrumentation_apply"


def signal_instrumentation_apply_latest_dir(repo_root: Path) -> Path:
    return signal_instrumentation_apply_dir(repo_root) / "latest"


def _iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _dump_yaml(mapping: dict[str, Any]) -> str:
    import yaml  # type: ignore[import-untyped]

    return yaml.safe_dump(
        mapping,
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
    )


def _first_metrics_artifact_path(product_root: Path, local_paths: list[str]) -> str | None:
    """Repo-relative path string to an existing file under declared metrics paths, or None."""
    for rel in sorted(local_paths):
        if not isinstance(rel, str) or not str(rel).strip():
            continue
        try:
            base = join_under_product(product_root, str(rel).strip().lstrip("/"))
        except ValueError:
            continue
        if base.is_dir():
            for p in sorted(base.rglob("*")):
                if p.is_file():
                    try:
                        return p.resolve().relative_to(product_root.resolve()).as_posix()
                    except ValueError:
                        continue
    return None


def _ensure_anchor_file(product_root: Path, local_paths: list[str]) -> str:
    """Create a small anchor file under metrics/ for manifest paths; returns repo-relative path."""
    anchor_dir = product_root / "metrics"
    if local_paths:
        try:
            first = str(local_paths[0]).strip().lstrip("/")
            anchor_dir = join_under_product(product_root, first)
        except ValueError:
            anchor_dir = product_root / "metrics"
    anchor_dir.mkdir(parents=True, exist_ok=True)
    path = anchor_dir / "argus_contract_anchor.txt"
    if not path.is_file():
        path.write_text(
            "# Argus instrumentation apply — contract path anchor (not live telemetry).\n",
            encoding="utf-8",
        )
    return path.resolve().relative_to(product_root.resolve()).as_posix()


def _merge_primary_keys(existing: list[str], suggested: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for x in list(existing) + list(suggested):
        s = str(x).strip()
        if not s or s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out


def _build_manifest_entries_for_contract(
    *,
    missing_dims: list[str],
    contract: dict[str, Any],
    existing_ids: set[str],
    manifest_path: str,
) -> list[dict[str, Any]]:
    """One deterministic manifest row per missing dimension (first suggested source_type)."""
    dims_block = contract.get("dimensions") if isinstance(contract.get("dimensions"), dict) else {}
    rows: list[dict[str, Any]] = []
    idx = 0
    for dim in sorted(missing_dims):
        if dim not in ALL_DIMENSIONS:
            continue
        dinfo = dims_block.get(dim) if isinstance(dims_block.get(dim), dict) else {}
        suggested = dinfo.get("suggested_signal_types") if isinstance(dinfo.get("suggested_signal_types"), list) else []
        st = None
        for cand in suggested:
            c = str(cand).strip().lower()
            if c:
                st = c
                break
        if not st:
            st = "metrics"
        safe_dim = re.sub(r"[^a-z0-9]+", "_", dim).strip("_")[:48] or "dim"
        sid = f"argus_apply_{safe_dim}_{st}"
        if sid in existing_ids:
            continue
        existing_ids.add(sid)
        cat = _DIM_TO_CATEGORY.get(dim, "operational")
        rows.append(
            {
                "id": sid,
                "category": cat,
                "source_type": st,
                "path": manifest_path,
                "freshness_sla": "24h",
                "value_type": "gauge",
                "required_for": "pipeline",
                "trust_level": "heuristic",
                "description": (
                    f"Argus instrumentation apply ({dim}): declared contract surface; "
                    "replace with operational paths when telemetry exists."
                ),
                "enabled": True,
            }
        )
        idx += 1
        if idx >= 8:
            break
    return rows


def apply_signal_instrumentation(
    repo_root: Path,
    *,
    work_order: dict[str, Any],
    execution_id: str,
    products_dir: Path | None = None,
    applied_at_utc: str | None = None,
    save: bool = True,
) -> dict[str, Any]:
    """
    Apply a minimal signal contract for ``request_type == signal_instrumentation``.

    Reads latest ``argus.product_signal_instrumentation.v1`` for the product (source of truth
    for ``proposed_signal_contract``). When ``save`` is false, performs no filesystem writes.
    """
    root = Path(repo_root).resolve()
    pid = str(work_order.get("product_id") or "").strip()
    woid = str(work_order.get("work_order_id") or "").strip()
    sat = applied_at_utc or _iso_now()

    base_out: dict[str, Any] = {
        "schema": PRODUCT_SIGNAL_INSTRUMENTATION_APPLY_SCHEMA,
        "product_id": pid,
        "work_order_id": woid,
        "execution_id": str(execution_id).strip(),
        "applied_at_utc": sat,
        "apply_status": "failed",
        "files_written": [],
        "files_updated": [],
        "contract_applied": {},
        "signal_dimensions_applied": [],
        "validation_summary": "",
        "notes": "",
    }

    if not pid or not woid:
        base_out["notes"] = "Missing product_id or work_order_id on work order."
        return base_out

    inst = load_full_signal_instrumentation_payload(root, pid)
    if inst is None or str(inst.get("schema") or "") != PRODUCT_SIGNAL_INSTRUMENTATION_SCHEMA:
        base_out["notes"] = f"No valid latest instrumentation artifact for product {pid!r}."
        return base_out
    if not inst.get("ok"):
        base_out["notes"] = "Latest instrumentation payload is not ok; cannot apply."
        return base_out

    contract = inst.get("proposed_signal_contract")
    if not isinstance(contract, dict) or str(contract.get("schema") or "") != "argus.proposed_signal_contract.v1":
        base_out["notes"] = "proposed_signal_contract missing or wrong schema on instrumentation artifact."
        return base_out

    seed = work_order.get("implementation_seed") if isinstance(work_order.get("implementation_seed"), dict) else {}
    missing_seed = list(seed.get("missing_signal_dimensions") or [])
    missing_inst = list(inst.get("missing_signal_dimensions") or [])
    missing_dims = sorted(set(str(x).strip() for x in (missing_seed + missing_inst) if str(x).strip()))

    pdir = (root / "products") if products_dir is None else Path(products_dir).resolve()
    product_root = pdir / pid
    product_yaml_path = product_root / "product.yaml"
    if not product_yaml_path.is_file():
        base_out["notes"] = f"product.yaml not found at {product_yaml_path}"
        return base_out

    raw_py, err = load_yaml_file(product_yaml_path)
    if err is not None or raw_py is None:
        base_out["notes"] = err or "failed to load product.yaml"
        return base_out

    cfg_default = root / "config" / "mission_profiles.yaml"
    vres = validate_manifest(
        raw_py,
        repo_root=root,
        product_root=product_root,
        config_path=cfg_default if cfg_default.is_file() else product_yaml_path,
    )
    if vres.errors or vres.node is None:
        base_out["notes"] = "product.yaml validation failed: " + "; ".join(vres.errors)
        return base_out

    node = vres.node
    m = node.metrics
    local_paths = list(m.local_paths) if m is not None else []

    path_for_manifest = _first_metrics_artifact_path(product_root, local_paths)
    if path_for_manifest is None:
        path_for_manifest = _ensure_anchor_file(product_root, local_paths or ["metrics/"])

    payload, _, _ = resolve_signal_manifest_payload(
        product_root=product_root,
        product_yaml=raw_py,
        product_id=pid,
    )
    existing_signals: list[dict[str, Any]] = []
    if isinstance(payload, dict):
        existing_signals = [s for s in (payload.get("signals") or []) if isinstance(s, dict)]
    existing_ids = {str(s.get("id") or "") for s in existing_signals if s.get("id")}

    new_rows = _build_manifest_entries_for_contract(
        missing_dims=missing_dims,
        contract=contract,
        existing_ids=set(existing_ids),
        manifest_path=path_for_manifest,
    )

    suggested_primaries: list[str] = []
    dims_block = contract.get("dimensions") if isinstance(contract.get("dimensions"), dict) else {}
    for dim in sorted(missing_dims):
        dinfo = dims_block.get(dim) if isinstance(dims_block.get(dim), dict) else {}
        pks = dinfo.get("primary_metric_keys_suggested") if isinstance(dinfo.get("primary_metric_keys_suggested"), list) else []
        for k in pks[:2]:
            ks = str(k).strip()
            if ks:
                suggested_primaries.append(ks)
    suggested_primaries = sorted(set(suggested_primaries))[:6]

    base_out["contract_applied"] = {
        "schema": str(contract.get("schema") or ""),
        "dimensions_targeted": missing_dims,
        "manifest_entries_added": len(new_rows),
        "primary_keys_merged": suggested_primaries,
    }
    base_out["signal_dimensions_applied"] = sorted({str(r.get("id") or "") for r in new_rows if r.get("id")})

    if not save:
        base_out["apply_status"] = "blocked"
        base_out["validation_summary"] = "save=false: no product files or apply artifacts written."
        base_out["notes"] = "Instrumentation apply skipped (--no-save / save=false)."
        return base_out

    if not new_rows and not suggested_primaries:
        base_out["apply_status"] = "success"
        base_out["validation_summary"] = (
            "No manifest rows or primary keys to add (coverage may already match contract suggestions)."
        )
        base_out["notes"] = "No file changes required."
        _post_validate_and_finalize(root, base_out, pid, products_dir, product_root, product_yaml_path, raw_py)
        return base_out

    files_written: list[str] = []
    files_updated: list[str] = []

    signals_path = product_root / "signals.yaml"
    if new_rows:
        if signals_path.is_file():
            raw_sig, err_sig = load_yaml_file(signals_path)
            if err_sig is not None or raw_sig is None:
                base_out["notes"] = f"signals.yaml: {err_sig}"
                return base_out
            merged_signals = list(existing_signals) + new_rows
            out_map: dict[str, Any] = {
                "schema": "argus.product_signal_manifest.v1",
                "product_id": pid,
                "signals": merged_signals,
            }
            txt = _dump_yaml(out_map)
            signals_path.write_text(txt, encoding="utf-8")
            files_updated.append(
                signals_path.resolve().relative_to(root.resolve()).as_posix()
            )
        else:
            merged_signals = list(existing_signals) + new_rows
            out_map = {
                "schema": "argus.product_signal_manifest.v1",
                "product_id": pid,
                "signals": merged_signals,
            }
            txt = _dump_yaml(out_map)
            signals_path.write_text(txt, encoding="utf-8")
            files_written.append(
                signals_path.resolve().relative_to(root.resolve()).as_posix()
            )

    if suggested_primaries:
        raw_py2, err2 = load_yaml_file(product_yaml_path)
        if err2 is None and isinstance(raw_py2, dict):
            metrics = raw_py2.get("metrics")
            if not isinstance(metrics, dict):
                metrics = {}
            cur = metrics.get("primary")
            cur_list: list[str] = []
            if isinstance(cur, list):
                cur_list = [str(x).strip() for x in cur if str(x).strip()]
            merged_p = _merge_primary_keys(cur_list, suggested_primaries)
            metrics["primary"] = merged_p
            raw_py2["metrics"] = metrics
            product_yaml_path.write_text(_dump_yaml(raw_py2), encoding="utf-8")
            rel_py = product_yaml_path.resolve().relative_to(root.resolve()).as_posix()
            if rel_py not in files_updated:
                files_updated.append(rel_py)

    base_out["files_written"] = files_written
    base_out["files_updated"] = files_updated
    base_out["apply_status"] = "success"
    base_out["notes"] = (
        "Applied deterministic manifest rows from proposed_signal_contract; "
        "merged suggested primary metric key refs where listed."
    )
    _post_validate_and_finalize(root, base_out, pid, products_dir, product_root, product_yaml_path, raw_py)
    return base_out


def _post_validate_and_finalize(
    repo_root: Path,
    base_out: dict[str, Any],
    pid: str,
    products_dir: Path | None,
    product_root: Path,
    product_yaml_path: Path,
    _raw_py_before: dict[str, Any],
) -> None:
    """Re-evaluate instrumentation and validate manifest; update base_out."""
    ev = evaluate_product_signal_instrumentation(repo_root, pid, products_dir=products_dir)
    status = str(ev.get("instrumentation_status") or "")
    ok = bool(ev.get("ok"))
    summary_parts = [
        f"post_apply instrumentation_status={status!r}",
        f"ok={ok}",
        f"missing_dimensions={ev.get('missing_signal_dimensions')}",
    ]
    base_out["validation_summary"] = "; ".join(summary_parts)
    base_out["post_apply"] = {
        "instrumentation_status": status,
        "ok": ok,
        "missing_signal_dimensions": list(ev.get("missing_signal_dimensions") or []),
    }

    raw_check, err = load_yaml_file(product_yaml_path)
    if err is None and isinstance(raw_check, dict):
        pl, e2, _ = resolve_signal_manifest_payload(
            product_root=product_root,
            product_yaml=raw_check,
            product_id=pid,
        )
        if isinstance(pl, dict):
            m_errs, _, _ = validate_signal_manifest_dict(pl)
            if m_errs:
                base_out["apply_status"] = "partial"
                base_out["notes"] = (base_out.get("notes") or "") + " Manifest validation errors: " + "; ".join(
                    m_errs[:5]
                )

    if status != "adequate" and base_out.get("apply_status") == "success":
        base_out["apply_status"] = "partial"


def write_signal_instrumentation_apply_artifacts(
    repo_root: Path,
    payload: dict[str, Any],
    *,
    write_latest: bool = True,
) -> tuple[Path, Path, Path | None, Path | None]:
    """Write ``runs/products/signal_instrumentation_apply/`` JSON + md + optional latest."""
    root = Path(repo_root).resolve()
    if str(payload.get("schema") or "") != PRODUCT_SIGNAL_INSTRUMENTATION_APPLY_SCHEMA:
        raise ValueError(f"payload.schema must be {PRODUCT_SIGNAL_INSTRUMENTATION_APPLY_SCHEMA!r}")
    pid = str(payload.get("product_id") or "").strip()
    eid = str(payload.get("execution_id") or "").strip()
    if not pid or not eid:
        raise ValueError("payload missing product_id or execution_id")

    base = signal_instrumentation_apply_dir(root)
    base.mkdir(parents=True, exist_ok=True)
    body = dumps_json(to_jsonable(dict(payload))) + "\n"

    stamped = base / f"{pid}__{eid}.json"
    stamped.write_text(body, encoding="utf-8")
    md = base / f"{pid}__{eid}.md"
    md.write_text(_render_apply_markdown(payload), encoding="utf-8")

    latest_json: Path | None = None
    latest_md: Path | None = None
    if write_latest:
        ld = signal_instrumentation_apply_latest_dir(root)
        ld.mkdir(parents=True, exist_ok=True)
        latest_json = ld / f"{pid}.json"
        latest_md = ld / f"{pid}.md"
        latest_json.write_text(body, encoding="utf-8")
        latest_md.write_text(_render_apply_markdown(payload), encoding="utf-8")

    return stamped, md, latest_json, latest_md


def _render_apply_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Signal instrumentation apply",
        "",
        f"- **Schema:** `{payload.get('schema')}`",
        f"- **Product:** `{payload.get('product_id')}`",
        f"- **Work order:** `{payload.get('work_order_id')}`",
        f"- **Execution:** `{payload.get('execution_id')}`",
        f"- **Applied (UTC):** {payload.get('applied_at_utc')}",
        f"- **Status:** `{payload.get('apply_status')}`",
        "",
        "## Files written",
        "",
    ]
    for f in payload.get("files_written") or []:
        lines.append(f"- `{f}`")
    if not (payload.get("files_written") or []):
        lines.append("—")
    lines.extend(["", "## Files updated", ""])
    for f in payload.get("files_updated") or []:
        lines.append(f"- `{f}`")
    if not (payload.get("files_updated") or []):
        lines.append("—")
    lines.extend(
        [
            "",
            "## Validation",
            "",
            str(payload.get("validation_summary") or "—"),
            "",
            "## Notes",
            "",
            str(payload.get("notes") or "—"),
            "",
        ]
    )
    return "\n".join(lines)


__all__ = [
    "PRODUCT_SIGNAL_INSTRUMENTATION_APPLY_SCHEMA",
    "apply_signal_instrumentation",
    "signal_instrumentation_apply_dir",
    "signal_instrumentation_apply_latest_dir",
    "write_signal_instrumentation_apply_artifacts",
]
