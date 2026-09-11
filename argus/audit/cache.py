"""Persist audit bundle + legacy Product Gap ``latest.json``."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from argus.audit.angles.compliance import run_compliance_angle
from argus.audit.angles.cost import run_cost_angle
from argus.audit.angles.performance import run_performance_angle
from argus.audit.angles.product_gap import run_product_gap_angle
from argus.audit.angles.quality import run_quality_angle
from argus.audit.angles.reliability import run_reliability_angle
from argus.audit.angles.security import run_security_angle
from argus.audit.angles.store_business import run_store_business_angle
from argus.audit.angles.stub import STUB_ANGLE_IDS, stub_angle_payload, stub_fingerprint
from argus.audit.angles.ux import run_ux_angle
from argus.audit.bundle import (
    ANGLE_IDS,
    BUNDLE_SCHEMA,
    audit_summary_from_bundle_dict,
    bundle_audit_path,
    compute_bundle_fingerprint,
    load_audit_bundle,
)
from argus.audit.cursor_scan import angle_inputs_fingerprint, merge_deterministic_with_prior_cursor
from argus.audit.models import AuditSummary, now_utc
from argus.audit.scanner import resolve_product_paths
from argus.core.serialize import dumps_json
from argus.products.inventory import build_inventory


def audit_product_dir(repo_root: Path, product_id: str) -> Path:
    return repo_root.resolve() / "runs" / "audit" / product_id


def latest_audit_path(repo_root: Path, product_id: str) -> Path:
    return audit_product_dir(repo_root, product_id) / "latest.json"


def load_latest_audit(repo_root: Path, product_id: str) -> AuditSummary | None:
    """Load Product Gap summary from ``bundle.json`` if present, else legacy ``latest.json``."""
    bundle = load_audit_bundle(repo_root, product_id)
    if bundle:
        s = audit_summary_from_bundle_dict(bundle)
        if s is not None:
            return s
    p = latest_audit_path(repo_root, product_id)
    if not p.is_file():
        return None
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return AuditSummary.from_dict(raw) if isinstance(raw, dict) else None


def _write_index(repo_root: Path, product_id: str, generated_at: str) -> None:
    idx_path = repo_root / "runs" / "audit" / "index.json"
    cur: dict[str, Any] = {"schema": "argus.audit_index.v1", "products": {}}
    if idx_path.is_file():
        try:
            prev = json.loads(idx_path.read_text(encoding="utf-8"))
            if isinstance(prev, dict) and isinstance(prev.get("products"), dict):
                cur["products"] = dict(prev["products"])
        except (OSError, json.JSONDecodeError):
            pass
    cur["products"][product_id] = {
        "bundle": f"runs/audit/{product_id}/bundle.json",
        "latest": f"runs/audit/{product_id}/latest.json",
        "generated_at_utc": generated_at,
    }
    idx_path.parent.mkdir(parents=True, exist_ok=True)
    idx_path.write_text(dumps_json(cur) + "\n", encoding="utf-8")


def refresh_audit_index(repo_root: Path, product_id: str) -> None:
    """Update ``runs/audit/index.json`` from current bundle (or now)."""
    b = load_audit_bundle(repo_root, product_id)
    gen = str(b.get("generated_at_utc", "")) if b else now_utc()
    _write_index(repo_root, product_id, gen)


def run_audit(repo_root: Path, product_id: str) -> AuditSummary:
    """Run all nine angles; write ``bundle.json`` + legacy ``latest.json`` (Product Gap)."""
    root = repo_root.resolve()
    inv = build_inventory(root)
    if product_id not in inv.valid:
        raise ValueError(f"Unknown product: {product_id!r}")
    record = inv.valid[product_id]
    product_root, node = resolve_product_paths(root, record)

    ts = now_utc()
    summary, pg_angle = run_product_gap_angle(
        root,
        product_id,
        product_root,
        node,
        generated_at_utc=ts,
    )

    cost_payload, fp_cost = run_cost_angle(root, product_id, node)
    qual_payload, fp_qual = run_quality_angle(root, product_root)
    sec_payload, fp_sec = run_security_angle(root, product_root)
    comp_payload, fp_comp = run_compliance_angle(root, product_root)
    rel_payload, fp_rel = run_reliability_angle(root, product_root)
    perf_payload, fp_perf = run_performance_angle(root, product_root)
    sb_payload, fp_sb = run_store_business_angle(root, product_id, product_root, node)
    ux_payload, fp_ux = run_ux_angle(root, product_root)

    prior_bundle = load_audit_bundle(root, product_id)
    prior_angles: dict[str, Any] = (
        prior_bundle.get("angles") if isinstance(prior_bundle, dict) else None
    ) or {}

    def _merge(
        aid: str,
        fresh: dict[str, Any],
        fp: str,
    ) -> tuple[dict[str, Any], str]:
        merged = merge_deterministic_with_prior_cursor(
            fresh,
            prior_angles.get(aid),
            angle_id=aid,
        )
        cs = merged.get("cursor_scan") if isinstance(merged.get("cursor_scan"), dict) else None
        return merged, angle_inputs_fingerprint(fp, cs)

    product_gap_m, fp_pg_m = _merge("product_gap", pg_angle, str(pg_angle.get("inputs_fingerprint", "")))
    cost_m, fp_cost_m = _merge("cost", cost_payload, fp_cost)
    qual_m, fp_qual_m = _merge("quality", qual_payload, fp_qual)
    sec_m, fp_sec_m = _merge("security", sec_payload, fp_sec)
    comp_m, fp_comp_m = _merge("compliance", comp_payload, fp_comp)
    rel_m, fp_rel_m = _merge("reliability", rel_payload, fp_rel)
    perf_m, fp_perf_m = _merge("performance", perf_payload, fp_perf)
    sb_m, fp_sb_m = _merge("store_business", sb_payload, fp_sb)
    ux_m, fp_ux_m = _merge("ux", ux_payload, fp_ux)

    inputs_fingerprint_by_angle = {
        "product_gap": fp_pg_m,
        "cost": fp_cost_m,
        "quality": fp_qual_m,
        "security": fp_sec_m,
        "compliance": fp_comp_m,
        "reliability": fp_rel_m,
        "performance": fp_perf_m,
        "store_business": fp_sb_m,
        "ux": fp_ux_m,
    }

    angles: dict[str, Any] = {
        "product_gap": product_gap_m,
        "cost": cost_m,
        "quality": qual_m,
        "security": sec_m,
        "compliance": comp_m,
        "reliability": rel_m,
        "performance": perf_m,
        "store_business": sb_m,
        "ux": ux_m,
    }

    for aid in STUB_ANGLE_IDS:
        if aid not in ANGLE_IDS:
            continue
        fresh_stub = stub_angle_payload(aid)
        merged_stub, fp_stub = _merge(aid, fresh_stub, stub_fingerprint(aid))
        angles[aid] = merged_stub
        inputs_fingerprint_by_angle[aid] = fp_stub

    # Guarantee key order and completeness
    ordered_angles: dict[str, Any] = {k: angles[k] for k in ANGLE_IDS}

    bundle: dict[str, Any] = {
        "schema": BUNDLE_SCHEMA,
        "product_id": product_id,
        "generated_at_utc": summary.generated_at_utc,
        "scan_depth": "quick",
        "inputs_fingerprint_by_angle": dict(sorted(inputs_fingerprint_by_angle.items())),
        "inputs_fingerprint_bundle": compute_bundle_fingerprint(inputs_fingerprint_by_angle),
        "angles": ordered_angles,
    }

    out_dir = audit_product_dir(root, product_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    bundle_path = bundle_audit_path(root, product_id)
    bundle_path.write_text(dumps_json(bundle) + "\n", encoding="utf-8")

    latest = latest_audit_path(root, product_id)
    latest.write_text(dumps_json(summary.to_dict()) + "\n", encoding="utf-8")
    _write_index(root, product_id, summary.generated_at_utc)
    return summary


def list_audited_products(repo_root: Path) -> list[str]:
    base = repo_root.resolve() / "runs" / "audit"
    if not base.is_dir():
        return []
    out: list[str] = []
    for d in sorted(base.iterdir()):
        if not d.is_dir():
            continue
        if (d / "bundle.json").is_file() or (d / "latest.json").is_file():
            out.append(d.name)
    return out
