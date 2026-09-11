"""Store & business angle — declarative manifest + bounded local artifacts.

No market claims, no storefront compliance, no crawling. Only repo-local declarations
and Argus experiment / economics indexes when present.
"""

from __future__ import annotations

import hashlib
from collections import Counter
from pathlib import Path
from typing import Any

from argus.core.models.product import ProductNode
from argus.economics.registry import load_merged_resources
from argus.experiments.store import list_experiments
from argus.products.loader import load_yaml_file

# Top-level product.yaml keys treated as optional commercial/distribution metadata (presence only).
_OPTIONAL_BUSINESS_KEYS: frozenset[str] = frozenset(
    {
        "commercial",
        "store",
        "distribution",
        "pricing",
        "packaging",
        "channels",
        "go_to_market",
    }
)

# Supplemental docs under the product root (existence only; bounded list).
_SUPPLEMENTAL_DOC_NAMES: tuple[str, ...] = (
    "README.md",
    "readme.md",
    "STORE.md",
    "store.md",
    "CHECKLIST.md",
    "checklist.md",
    "RELEASE.md",
    "release.md",
    "RELEASE_CHECKLIST.md",
    "release_checklist.md",
)


def _mtime_ns(p: Path) -> int:
    try:
        return p.stat().st_mtime_ns
    except OSError:
        return 0


def _safe_size(p: Path) -> int:
    try:
        return p.stat().st_size if p.is_file() else 0
    except OSError:
        return 0


def _analytics_enabled(node: ProductNode) -> bool:
    for s in node.signals:
        if str(s.type) == "analytics" and s.enabled:
            return True
    return False


def _optional_yaml_blocks(raw: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for k in sorted(raw.keys()):
        if k in _OPTIONAL_BUSINESS_KEYS:
            out.append(k)
    return out


def _supplemental_docs(product_root: Path) -> tuple[bool, list[str]]:
    readme = False
    names: list[str] = []
    for name in _SUPPLEMENTAL_DOC_NAMES:
        p = product_root / name
        if not p.is_file():
            continue
        low = name.lower()
        if low == "readme.md" or name == "README.md" or name == "readme.md":
            readme = True
        names.append(name)
    return readme, sorted(set(names))[:16]


def fingerprint_store_business_inputs(repo_root: Path, product_id: str, product_root: Path, node: ProductNode) -> str:
    root = repo_root.resolve()
    pr = product_root.resolve()
    parts: list[str] = []

    cfg = pr / "product.yaml"
    if cfg.is_file():
        parts.append(f"py:{_mtime_ns(cfg)}:{_safe_size(cfg)}")

    raw, err = load_yaml_file(cfg)
    if err is None and raw is not None:
        parts.append(f"optkeys:{','.join(_optional_yaml_blocks(raw))}")

    for name in _SUPPLEMENTAL_DOC_NAMES:
        p = pr / name
        if p.is_file():
            parts.append(f"doc:{name}:{_mtime_ns(p)}")

    merged = load_merged_resources(root)
    econ_ids = sorted(e.id for e in merged.values() if e.product_id == product_id)
    parts.append(f"econ:{len(econ_ids)}:{','.join(econ_ids[:24])}")

    exps = list_experiments(root, product_id=product_id)
    parts.append(f"exp:{len(exps)}:{','.join(sorted(e.id for e in exps)[:48])}")

    parts.append(f"stage:{node.lifecycle.stage.value}")
    ti = node.type_info
    if ti is not None:
        parts.append(f"type:{ti.type}:{ti.status}")

    raw_fp = "|".join(sorted(parts)) if parts else "empty"
    return hashlib.sha256(raw_fp.encode()).hexdigest()[:20]


def run_store_business_angle(
    repo_root: Path,
    product_id: str,
    product_root: Path,
    node: ProductNode,
) -> tuple[dict[str, Any], str]:
    fp = fingerprint_store_business_inputs(repo_root, product_id, product_root, node)
    pr = product_root.resolve()
    cfg = pr / "product.yaml"

    raw: dict[str, Any] | None = None
    yaml_err: str | None = None
    if cfg.is_file():
        raw, yaml_err = load_yaml_file(cfg)

    optional_blocks: list[str] = _optional_yaml_blocks(raw or {})
    readme_present, doc_names = _supplemental_docs(pr)
    analytics_on = _analytics_enabled(node)
    next_gate = node.lifecycle.next_gate
    next_gate_declared = bool(next_gate and str(next_gate).strip())

    merged = load_merged_resources(repo_root)
    econ_rows = [e for e in merged.values() if e.product_id == product_id]
    econ_kinds = sorted({e.kind for e in econ_rows if str(e.kind).strip()})[:16]

    exps = list_experiments(repo_root, product_id=product_id)
    by_status: Counter[str] = Counter()
    by_type: Counter[str] = Counter()
    for e in exps:
        by_status[e.status.value] += 1
        by_type[e.type.value] += 1

    ti = node.type_info
    product_type = ti.type if ti is not None else None
    product_status = ti.status if ti is not None else None
    product_state = ti.state if ti is not None else None

    evidence_score = 0
    if optional_blocks:
        evidence_score += 2
    if exps:
        evidence_score += 2
    if econ_rows:
        evidence_score += 1
    if analytics_on:
        evidence_score += 1
    if readme_present or doc_names:
        evidence_score += 1
    if next_gate_declared:
        evidence_score += 1

    if evidence_score >= 2 or exps or optional_blocks:
        angle_status = "active"
    else:
        angle_status = "partial"

    lines: list[str] = [
        f"store_business: angle={angle_status} evidence_score~{evidence_score} (local declarative index)",
        f"store_business: lifecycle_stage={node.lifecycle.stage.value}",
    ]
    if ti is not None:
        lines.append(
            f"store_business: manifest type={product_type!s} status={product_status!s}"
            + (f" state={product_state!s}" if product_state else "")
        )
    if optional_blocks:
        lines.append(f"store_business: optional_yaml_blocks={','.join(optional_blocks)}")
    else:
        lines.append("store_business: no optional commercial/distribution blocks in product.yaml (top-level keys)")
    lines.append(f"store_business: tracked_experiments={len(exps)} economics_resources={len(econ_rows)}")
    if exps:
        st = ",".join(f"{k}:{v}" for k, v in sorted(by_status.items())[:8])
        lines.append(f"store_business: experiment_status_counts {st}")
    if analytics_on:
        lines.append("store_business: analytics signal enabled (declaration)")
    if readme_present or doc_names:
        lines.append(
            f"store_business: local_docs readme={int(readme_present)} "
            f"files={','.join(doc_names[:4])}{'…' if len(doc_names) > 4 else ''}"
        )
    if yaml_err:
        lines.append(f"store_business: product.yaml load warning (angle still partial): {yaml_err[:120]}")
    lines = lines[:10]

    payload: dict[str, Any] = {
        "schema": "argus.audit_angle.store_business.v1",
        "angle_status": angle_status,
        "summary_lines": lines,
        "manifest": {
            "product_type": product_type,
            "product_status": product_status,
            "product_state": product_state,
            "lifecycle_stage": node.lifecycle.stage.value,
            "next_gate_declared": next_gate_declared,
            "analytics_signal_enabled": analytics_on,
            "optional_yaml_blocks": optional_blocks,
        },
        "experiments": {
            "total": len(exps),
            "by_status": dict(sorted(by_status.items())),
            "by_type": dict(sorted(by_type.items())),
            "ids_sample": sorted(e.id for e in exps)[:24],
        },
        "economics": {
            "resource_count": len(econ_rows),
            "resource_kinds": econ_kinds,
            "resource_ids_sample": sorted(e.id for e in econ_rows)[:24],
        },
        "local_docs": {
            "readme_present": readme_present,
            "supplemental_files": doc_names,
        },
        "notes": [
            "declarations and local Argus artifacts only; not market performance or store compliance",
        ],
    }
    return payload, fp
