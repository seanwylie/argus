"""
Deterministic signal instrumentation pass: assess observability / signal coverage for a product.

No LLM, no mutation of telemetry sources. Synthetic/structural seed entries are labeled and
are not presented as real-world outcomes.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Final

from argus.core.models.enums import SignalType
from argus.core.models.signal_manifest import ProductSignalManifestEntry
from argus.core.serialize import dumps_json, to_jsonable
from argus.products.loader import load_yaml_file
from argus.products.paths import join_under_product
from argus.products.validate import validate_manifest

PRODUCT_SIGNAL_INSTRUMENTATION_SCHEMA: Final = "argus.product_signal_instrumentation.v1"
PROPOSED_SIGNAL_CONTRACT_SCHEMA: Final = "argus.proposed_signal_contract.v1"

# Contract dimensions (keys are stable API).
DIM_ACTIVITY: Final = "activity_usage"
DIM_GROWTH: Final = "growth_trend"
DIM_HEALTH: Final = "health_quality"
DIM_FRESH: Final = "freshness_recency"
DIM_MISSION: Final = "mission_relevant_metrics"

ALL_DIMENSIONS: Final[tuple[str, ...]] = (
    DIM_ACTIVITY,
    DIM_GROWTH,
    DIM_HEALTH,
    DIM_FRESH,
    DIM_MISSION,
)

# Statuses where optimization-style loops are unlikely to help until observability improves.
INSTRUMENTATION_PRESSURE_STATUSES: Final = frozenset({"weak", "sparse", "missing"})

# Map SignalType enum values to one or more contract dimensions (deterministic).
_SIGNAL_TYPE_TO_DIMS: dict[SignalType, frozenset[str]] = {
    SignalType.FILESYSTEM: frozenset({DIM_ACTIVITY, DIM_FRESH}),
    SignalType.EXECUTION: frozenset({DIM_ACTIVITY}),
    SignalType.LOGS: frozenset({DIM_ACTIVITY, DIM_HEALTH}),
    SignalType.ANALYTICS: frozenset({DIM_ACTIVITY, DIM_GROWTH}),
    SignalType.METRICS: frozenset({DIM_HEALTH}),
    SignalType.HEALTH: frozenset({DIM_HEALTH}),
    SignalType.COST: frozenset({DIM_MISSION}),
    SignalType.CUSTOM: frozenset({DIM_MISSION}),
    SignalType.TEMPORAL: frozenset({DIM_GROWTH, DIM_FRESH}),
}


def signal_instrumentation_dir(repo_root: Path) -> Path:
    return Path(repo_root).resolve() / "runs" / "products" / "signal_instrumentation"


def signal_instrumentation_latest_dir(repo_root: Path) -> Path:
    return signal_instrumentation_dir(repo_root) / "latest"


def _load_json_file(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return raw if isinstance(raw, dict) else None


def load_full_signal_instrumentation_payload(repo_root: Path, product_id: str) -> dict[str, Any] | None:
    """Read ``runs/products/signal_instrumentation/latest/<product_id>.json`` when schema matches."""
    root = Path(repo_root).resolve()
    pid = str(product_id).strip()
    if not pid:
        return None
    path = signal_instrumentation_latest_dir(root) / f"{pid}.json"
    raw = _load_json_file(path)
    if not raw or str(raw.get("schema") or "") != PRODUCT_SIGNAL_INSTRUMENTATION_SCHEMA:
        return None
    return raw


def load_latest_signal_instrumentation_by_product(repo_root: Path) -> dict[str, dict[str, Any]]:
    """
    Read-only scan of ``runs/products/signal_instrumentation/latest/<product_id>.json``.

    Returns per-product rows with at least: ``instrumentation_status``, ``evaluated_at_utc``, ``ok``,
    when the file matches :data:`PRODUCT_SIGNAL_INSTRUMENTATION_SCHEMA` and includes ``product_id``.
    """
    root = Path(repo_root).resolve()
    latest = signal_instrumentation_latest_dir(root)
    if not latest.is_dir():
        return {}
    out: dict[str, dict[str, Any]] = {}
    for path in sorted(latest.glob("*.json")):
        raw = _load_json_file(path)
        if not raw or str(raw.get("schema") or "") != PRODUCT_SIGNAL_INSTRUMENTATION_SCHEMA:
            continue
        pid = str(raw.get("product_id") or "").strip()
        if not pid:
            continue
        out[pid] = {
            "instrumentation_status": str(raw.get("instrumentation_status") or ""),
            "evaluated_at_utc": raw.get("evaluated_at_utc"),
            "ok": bool(raw.get("ok")),
            "recommended_next_step": raw.get("recommended_next_step"),
        }
    return out


def product_ids_under_instrumentation_pressure(
    inst_by_product: dict[str, dict[str, Any]],
) -> list[str]:
    """Product ids whose latest instrumentation status is weak/sparse/missing (and payload ok)."""
    pids: list[str] = []
    for pid, row in inst_by_product.items():
        if not row.get("ok"):
            continue
        st = str(row.get("instrumentation_status") or "").strip().lower()
        if st in INSTRUMENTATION_PRESSURE_STATUSES:
            pids.append(pid)
    return sorted(set(pids))


def _parse_signal_type(raw: str) -> SignalType | None:
    try:
        return SignalType(str(raw).strip().lower())
    except ValueError:
        return None


def _count_metric_files(product_root: Path, local_paths: list[str]) -> int:
    n = 0
    for rel in local_paths:
        if not isinstance(rel, str) or not rel.strip():
            continue
        try:
            base = join_under_product(product_root, rel.strip().lstrip("/"))
        except ValueError:
            continue
        if not base.is_dir():
            continue
        for p in base.rglob("*"):
            if p.is_file():
                n += 1
    return n


def _dimension_scores(
    *,
    yaml_signal_types: list[SignalType],
    manifest_entries: list[ProductSignalManifestEntry],
    primary_metric_keys: list[str],
    metric_file_count: int,
    mission_id_set: bool,
    mission_block_set: bool,
) -> dict[str, int]:
    scores: dict[str, int] = {d: 0 for d in ALL_DIMENSIONS}

    def add(st: SignalType, weight: int) -> None:
        for d in _SIGNAL_TYPE_TO_DIMS.get(st, frozenset()):
            scores[d] += weight

    for st in yaml_signal_types:
        add(st, 2)

    for e in manifest_entries:
        if not e.enabled:
            continue
        add(e.source_type, 4)

    if primary_metric_keys:
        scores[DIM_HEALTH] += 3
        scores[DIM_GROWTH] += 1
        scores[DIM_ACTIVITY] += 1

    if metric_file_count >= 1:
        scores[DIM_HEALTH] += 1
        scores[DIM_FRESH] += 1
    if metric_file_count >= 3:
        scores[DIM_HEALTH] += 2
        scores[DIM_ACTIVITY] += 1

    if mission_id_set or mission_block_set:
        scores[DIM_MISSION] += 3

    return scores


def _level_from_score(score: int) -> str:
    if score >= 8:
        return "adequate"
    if score >= 4:
        return "partial"
    return "missing"


def _overall_status(
    dim_levels: dict[str, str],
) -> tuple[str, str]:
    """Return (instrumentation_status, signal_coverage_assessment)."""
    adequate = sum(1 for d in ALL_DIMENSIONS if dim_levels[d] == "adequate")
    partial = sum(1 for d in ALL_DIMENSIONS if dim_levels[d] == "partial")
    missing = sum(1 for d in ALL_DIMENSIONS if dim_levels[d] == "missing")

    parts = [
        f"dimensions_adequate={adequate}",
        f"partial={partial}",
        f"missing={missing}",
    ]
    assessment = "coverage: " + "; ".join(parts)

    if adequate >= 4 and missing == 0:
        return "adequate", assessment
    if missing >= 4:
        return "missing", assessment
    if missing >= 2 and adequate <= 1:
        return "sparse", assessment
    if adequate >= 2 and missing <= 1:
        return "weak", assessment
    return "weak", assessment


def _recommended_next_step(status: str) -> str:
    if status == "adequate":
        return (
            "Signal coverage is sufficient for bounded improvement loops; keep manifest and "
            "metrics.primary aligned with code changes."
        )
    if status == "weak":
        return (
            "Strengthen sparse dimensions: add manifest entries (temporal/analytics/health) and "
            "non-empty metrics.primary where appropriate."
        )
    if status == "sparse":
        return (
            "Observability is thin: define signal_manifest signals for growth and health, wire "
            "primary metric keys, and add real telemetry paths before optimization-focused work."
        )
    return (
        "Almost no usable signal dimensions: add a validated signal_manifest, primary metrics, "
        "and at least one non-placeholder signal source before expecting meaningful autonomy."
    )


def _missing_dimensions(dim_levels: dict[str, str]) -> list[str]:
    out = [d for d in ALL_DIMENSIONS if dim_levels[d] != "adequate"]
    return sorted(out)


def build_proposed_signal_contract(
    *,
    product_id: str,
    dim_levels: dict[str, str],
    mission_id: str | None,
) -> dict[str, Any]:
    """Minimal structured observable product model (not prose-only)."""
    dims_out: dict[str, Any] = {}
    suggested_types: dict[str, tuple[str, ...]] = {
        DIM_ACTIVITY: ("execution", "logs", "analytics"),
        DIM_GROWTH: ("temporal", "analytics"),
        DIM_HEALTH: ("health", "metrics", "logs"),
        DIM_FRESH: ("temporal", "filesystem"),
        DIM_MISSION: ("custom", "cost"),
    }
    for d in ALL_DIMENSIONS:
        dims_out[d] = {
            "coverage_level": dim_levels[d],
            "suggested_signal_types": list(suggested_types[d]),
            "primary_metric_keys_suggested": (
                ["core_errors", "core_latency_ms", "usage_events"]
                if d in (DIM_HEALTH, DIM_ACTIVITY)
                else ["north_star_metric", "conversion_proxy"]
                if d == DIM_GROWTH
                else ["evidence_freshness_s", "release_recency_d"]
                if d == DIM_FRESH
                else ["mission_alignment_score", "cost_to_serve_usd"]
                if d == DIM_MISSION
                else []
            ),
        }

    mission_hooks: dict[str, Any] = {
        "mission_id": mission_id,
        "note": (
            "Mission-weighted metrics belong here only when mission_id/mission block is set; "
            "keep declarations in signal_manifest."
        ),
    }

    return {
        "schema": PROPOSED_SIGNAL_CONTRACT_SCHEMA,
        "product_id": product_id,
        "dimensions": dims_out,
        "mission_hooks": mission_hooks,
    }


def _build_seed_signals(
    *,
    product_id: str,
    instrumentation_status: str,
    enabled_yaml_count: int,
    enabled_manifest_count: int,
    metric_file_count: int,
    primary_len: int,
) -> list[dict[str, Any]]:
    """Structural/derived/synthetic seeds — never impersonate real telemetry."""
    seeds: list[dict[str, Any]] = [
        {
            "id": "struct.product_id",
            "kind": "structural",
            "dimension": DIM_MISSION,
            "description": "Product identifier resolved from inventory (structural fact).",
            "value": product_id,
        },
        {
            "id": "derived.enabled_yaml_signal_definitions",
            "kind": "derived",
            "dimension": DIM_ACTIVITY,
            "description": "Count of enabled signal definitions in product.yaml `signals` list.",
            "value": enabled_yaml_count,
        },
        {
            "id": "derived.enabled_manifest_signal_count",
            "kind": "derived",
            "dimension": DIM_HEALTH,
            "description": "Count of enabled entries in product signal manifest (if any).",
            "value": enabled_manifest_count,
        },
        {
            "id": "derived.metric_artifact_files_under_local_paths",
            "kind": "derived",
            "dimension": DIM_FRESH,
            "description": "Count of files under declared metrics.local_paths (repo-relative).",
            "value": metric_file_count,
        },
        {
            "id": "derived.metrics_primary_key_count",
            "kind": "derived",
            "dimension": DIM_GROWTH,
            "description": "Length of metrics.primary list in product.yaml.",
            "value": primary_len,
        },
    ]
    # Placeholder slots only when overall instrumentation is not yet adequate (not fake success).
    if instrumentation_status != "adequate":
        seeds.append(
            {
                "id": "synthetic.placeholder.usage_events_per_day",
                "kind": "synthetic",
                "dimension": DIM_ACTIVITY,
                "description": (
                    "Placeholder slot for a future time series; not populated with real telemetry."
                ),
                "value": None,
                "unit": "events/day",
            }
        )
        seeds.append(
            {
                "id": "synthetic.placeholder.quality_score",
                "kind": "synthetic",
                "dimension": DIM_HEALTH,
                "description": (
                    "Placeholder slot for a future quality gauge; not populated with real data."
                ),
                "value": None,
                "unit": "score_0_1",
            }
        )
    return seeds


def _observability_notes(
    *,
    dim_levels: dict[str, str],
    warnings: list[str],
    metric_file_count: int,
    primary_len: int,
) -> list[str]:
    notes: list[str] = []
    if metric_file_count == 0:
        notes.append("No metric artifact files found under metrics.local_paths.")
    elif metric_file_count < 3:
        notes.append("Few metric artifact files; consider richer local metrics or ingest.")
    if primary_len == 0:
        notes.append("metrics.primary is empty; portfolio and decisions lack stable key refs.")
    for d in ALL_DIMENSIONS:
        if dim_levels[d] == "missing":
            notes.append(f"Dimension {d!r} has no declared coverage.")
    for w in sorted(warnings):
        notes.append(f"validation_warning: {w}")
    return notes


def evaluate_product_signal_instrumentation(
    repo_root: Path,
    product_id: str,
    *,
    products_dir: Path | None = None,
) -> dict[str, Any]:
    """
    Inspect a product and return ``argus.product_signal_instrumentation.v1`` payload.

    Deterministic; does not mutate telemetry or external systems.
    """
    root = Path(repo_root).resolve()
    pid = str(product_id).strip()
    evaluated_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )

    base: dict[str, Any] = {
        "schema": PRODUCT_SIGNAL_INSTRUMENTATION_SCHEMA,
        "product_id": pid,
        "evaluated_at_utc": evaluated_at,
        "ok": False,
    }

    pdir = (root / "products") if products_dir is None else Path(products_dir).resolve()
    product_root = pdir / pid
    config_path = product_root / "product.yaml"

    if not config_path.is_file():
        base["ok"] = False
        base["errors"] = [f"product.yaml not found at {config_path}"]
        base["instrumentation_status"] = "missing"
        base["signal_coverage_assessment"] = "coverage: product manifest missing"
        base["missing_signal_dimensions"] = sorted(ALL_DIMENSIONS)
        base["proposed_signal_contract"] = build_proposed_signal_contract(
            product_id=pid,
            dim_levels={d: "missing" for d in ALL_DIMENSIONS},
            mission_id=None,
        )
        base["synthetic_seed_signals"] = []
        base["observability_notes"] = base["errors"]
        base["recommended_next_step"] = _recommended_next_step("missing")
        return base

    raw, err = load_yaml_file(config_path)
    if err is not None:
        base["errors"] = [err]
        return base
    if not isinstance(raw, dict):
        base["errors"] = ["product.yaml must be a mapping"]
        return base

    cfg_default = root / "config" / "mission_profiles.yaml"
    result = validate_manifest(
        raw,
        repo_root=root,
        product_root=product_root,
        config_path=cfg_default if cfg_default.is_file() else config_path,
    )
    if result.errors:
        base["errors"] = list(result.errors)
        base["warnings"] = list(result.warnings)
        return base

    node = result.node
    assert node is not None

    yaml_types: list[SignalType] = []
    for s in node.signals or []:
        if not s.enabled:
            continue
        st = _parse_signal_type(s.type)
        if st is not None:
            yaml_types.append(st)

    manifest_entries: list[ProductSignalManifestEntry] = []
    if node.signal_manifest is not None:
        manifest_entries = [e for e in node.signal_manifest.signals if e.enabled]

    m = node.metrics
    local_paths = list(m.local_paths) if m is not None else []
    primary_keys = list(m.primary) if m is not None else []

    metric_files = _count_metric_files(product_root, local_paths)
    mission_id_set = bool(str(getattr(node, "mission_id", None) or "").strip())
    mission_block_set = getattr(node, "mission", None) is not None

    scores = _dimension_scores(
        yaml_signal_types=yaml_types,
        manifest_entries=manifest_entries,
        primary_metric_keys=primary_keys,
        metric_file_count=metric_files,
        mission_id_set=mission_id_set,
        mission_block_set=mission_block_set,
    )

    dim_levels = {d: _level_from_score(scores[d]) for d in ALL_DIMENSIONS}
    status, assessment = _overall_status(dim_levels)

    mid = str(node.mission_id).strip() if getattr(node, "mission_id", None) else None

    payload: dict[str, Any] = {
        **base,
        "ok": True,
        "warnings": list(result.warnings),
        "instrumentation_status": status,
        "signal_coverage_assessment": assessment,
        "signal_coverage_scores": {d: scores[d] for d in ALL_DIMENSIONS},
        "dimension_coverage_levels": {d: dim_levels[d] for d in ALL_DIMENSIONS},
        "missing_signal_dimensions": _missing_dimensions(dim_levels),
        "proposed_signal_contract": build_proposed_signal_contract(
            product_id=pid,
            dim_levels=dim_levels,
            mission_id=mid,
        ),
        "synthetic_seed_signals": _build_seed_signals(
            product_id=pid,
            instrumentation_status=status,
            enabled_yaml_count=len(yaml_types),
            enabled_manifest_count=len(manifest_entries),
            metric_file_count=metric_files,
            primary_len=len(primary_keys),
        ),
        "observability_notes": _observability_notes(
            dim_levels=dim_levels,
            warnings=list(result.warnings),
            metric_file_count=metric_files,
            primary_len=len(primary_keys),
        ),
        "recommended_next_step": _recommended_next_step(status),
        "inputs": {
            "products_dir": str(pdir),
            "metric_file_count": metric_files,
            "metrics_primary_count": len(primary_keys),
            "enabled_yaml_signal_types": sorted({t.value for t in yaml_types}),
        },
    }
    return payload


def render_product_signal_instrumentation_markdown(payload: dict[str, Any]) -> str:
    """Human-readable summary for operator review."""
    lines = [
        "# Product signal instrumentation",
        "",
        f"- **Schema:** `{payload.get('schema')}`",
        f"- **Product:** `{payload.get('product_id')}`",
        f"- **Evaluated (UTC):** {payload.get('evaluated_at_utc')}",
        f"- **Status:** `{payload.get('instrumentation_status')}`",
        f"- **OK:** {payload.get('ok')}",
        "",
        "## Coverage",
        "",
        f"- {payload.get('signal_coverage_assessment')}",
        "",
        "### Missing dimensions",
        "",
    ]
    for d in payload.get("missing_signal_dimensions") or []:
        lines.append(f"- `{d}`")
    lines.extend(["", "## Recommended next step", "", payload.get("recommended_next_step", ""), ""])
    notes = payload.get("observability_notes") or []
    if notes:
        lines.extend(["## Observability notes", ""])
        for n in notes:
            lines.append(f"- {n}")
        lines.append("")
    seeds = payload.get("synthetic_seed_signals") or []
    if seeds:
        lines.extend(["## Seed signals (labeled)", ""])
        for s in seeds:
            lines.append(
                f"- **{s.get('kind')}** `{s.get('id')}` — {s.get('description')}"
            )
        lines.append("")
    return "\n".join(lines)


def write_product_signal_instrumentation_artifacts(
    repo_root: Path,
    payload: dict[str, Any],
    *,
    run_timestamp_utc: str | None = None,
) -> tuple[Path, Path, Path, Path]:
    """
    Write JSON + Markdown under ``runs/products/signal_instrumentation/``.

    Paths:
    - ``latest/<product_id>.json`` (+ .md)
    - ``<product_id>__<timestamp>.json`` (+ .md)
    """
    root = Path(repo_root).resolve()
    pid = str(payload.get("product_id") or "").strip()
    if not pid:
        raise ValueError("payload missing product_id")

    ts = run_timestamp_utc
    if not ts:
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    base = signal_instrumentation_dir(root)
    latest_dir = base / "latest"
    latest_dir.mkdir(parents=True, exist_ok=True)

    pl = dict(payload)
    pl_json = dumps_json(to_jsonable(pl)) + "\n"
    md = render_product_signal_instrumentation_markdown(pl)

    latest_json = latest_dir / f"{pid}.json"
    latest_md = latest_dir / f"{pid}.md"
    stamped_json = base / f"{pid}__{ts}.json"
    stamped_md = base / f"{pid}__{ts}.md"

    latest_json.write_text(pl_json, encoding="utf-8")
    latest_md.write_text(md, encoding="utf-8")
    stamped_json.write_text(pl_json, encoding="utf-8")
    stamped_md.write_text(md, encoding="utf-8")

    return stamped_json, stamped_md, latest_json, latest_md


def run_product_signal_instrumentation(
    repo_root: Path,
    *,
    product_id: str,
    write_artifacts: bool = True,
    products_dir: Path | None = None,
) -> dict[str, Any]:
    """
    Public runner: evaluate signal instrumentation and optionally persist artifacts.

    Artifacts: ``runs/products/signal_instrumentation/latest/<id>.json`` and stamped copies.
    """
    payload = evaluate_product_signal_instrumentation(
        repo_root,
        product_id,
        products_dir=products_dir,
    )
    if (
        write_artifacts
        and payload.get("schema") == PRODUCT_SIGNAL_INSTRUMENTATION_SCHEMA
        and payload.get("ok")
    ):
        write_product_signal_instrumentation_artifacts(repo_root, payload)
    return payload


__all__ = [
    "ALL_DIMENSIONS",
    "INSTRUMENTATION_PRESSURE_STATUSES",
    "PRODUCT_SIGNAL_INSTRUMENTATION_SCHEMA",
    "PROPOSED_SIGNAL_CONTRACT_SCHEMA",
    "build_proposed_signal_contract",
    "evaluate_product_signal_instrumentation",
    "load_full_signal_instrumentation_payload",
    "load_latest_signal_instrumentation_by_product",
    "product_ids_under_instrumentation_pressure",
    "render_product_signal_instrumentation_markdown",
    "run_product_signal_instrumentation",
    "signal_instrumentation_dir",
    "signal_instrumentation_latest_dir",
    "write_product_signal_instrumentation_artifacts",
]
