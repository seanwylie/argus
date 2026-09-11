"""Merge Cursor codebase scan JSON into ``bundle.json`` (optional interpretation layer)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from argus.audit.agent_lens import CURSOR_SCAN_ANGLE_IDS
from argus.audit.bundle import (
    ANGLE_IDS,
    BUNDLE_SCHEMA,
    bundle_audit_path,
    compute_bundle_fingerprint,
    load_audit_bundle,
)
from argus.audit.cache import audit_product_dir, refresh_audit_index
from argus.audit.cursor_scan import (
    CURSOR_SCAN_BATCH_SCHEMA,
    CURSOR_SCAN_SCHEMA,
    angle_inputs_fingerprint,
    legacy_agent_payload_to_cursor_scan,
    reapply_cursor_merge,
    validate_cursor_scan_v1,
)
from argus.audit.models import now_utc
from argus.core.serialize import dumps_json


def _deterministic_fp_from_bundle_map(fp_map: dict[str, str], aid: str, angle: dict[str, Any]) -> str:
    """Recover runner fingerprint (before ``angle_inputs_fingerprint`` combined hash)."""
    combined = fp_map.get(aid, "")
    if aid == "product_gap":
        return str(angle.get("inputs_fingerprint", ""))
    if ":" in combined:
        return combined.split(":", 1)[0]
    return combined


def normalize_to_cursor_scan(raw: dict[str, Any], *, angle_id: str) -> dict[str, Any]:
    """Normalize ingest payload to ``argus.audit_cursor_scan.v1``."""
    sch = str(raw.get("schema", ""))
    if sch == CURSOR_SCAN_SCHEMA:
        return validate_cursor_scan_v1(raw, expected_angle_id=angle_id)
    return legacy_agent_payload_to_cursor_scan(raw, angle_id=angle_id)


def parse_ingest_file(raw: dict[str, Any], *, angle: str | None) -> dict[str, dict[str, Any]]:
    """Return map angle_id -> payload to merge as ``cursor_scan``."""
    if angle:
        aid = angle.strip().lower().replace("-", "_")
        if aid not in ANGLE_IDS:
            raise ValueError(f"unknown angle {aid!r}; use one of {ANGLE_IDS}")
        return {aid: raw}

    sch = str(raw.get("schema", ""))
    if sch == CURSOR_SCAN_BATCH_SCHEMA and isinstance(raw.get("angles"), dict):
        return {str(k): v for k, v in raw["angles"].items() if isinstance(v, dict)}

    if sch == "argus.audit_agent_batch.v1" and isinstance(raw.get("angles"), dict):
        return {str(k): v for k, v in raw["angles"].items() if isinstance(v, dict)}

    if isinstance(raw.get("angles"), dict):
        return {str(k): v for k, v in raw["angles"].items() if isinstance(v, dict)}

    m2 = re.search(r"audit_angle\.([a-z0-9_]+)\.v", sch)
    if m2 and m2.group(1) in ANGLE_IDS:
        return {m2.group(1): raw}

    if raw.get("angle"):
        aid = str(raw["angle"]).strip().lower().replace("-", "_")
        if aid not in ANGLE_IDS:
            raise ValueError(f"angle {aid!r} not in {ANGLE_IDS}")
        return {aid: raw}

    raise ValueError(
        "expected schema argus.audit_cursor_scan_batch.v1 with angles{}, "
        "argus.audit_agent_batch.v1, or legacy angles map; or use --angle with one payload",
    )


def _validate_ingest_root_schema(raw: dict[str, Any], *, has_angles_map: bool) -> None:
    sch = str(raw.get("schema", ""))
    if not has_angles_map:
        return
    if sch in ("", CURSOR_SCAN_BATCH_SCHEMA, "argus.audit_agent_batch.v1"):
        return
    raise ValueError(
        f"ingest root schema must be {CURSOR_SCAN_BATCH_SCHEMA!r} or argus.audit_agent_batch.v1, got {sch!r}",
    )


def ingest_agent_angles(
    repo_root: Path,
    product_id: str,
    raw: dict[str, Any],
    *,
    single_angle: str | None = None,
    overwrite: bool = True,
) -> dict[str, Any]:
    """
    Merge Cursor / agent payloads into ``bundle.json`` as ``cursor_scan`` on each angle.

    Preserves deterministic fields from the last ``run_audit``; updates fingerprints.
    """
    root = repo_root.resolve()
    bundle = load_audit_bundle(root, product_id)
    if not bundle:
        raise ValueError(
            f"No bundle for {product_id!r}; run `argus audit run --product-id {product_id}` first",
        )

    has_batch = isinstance(raw.get("angles"), dict)
    _validate_ingest_root_schema(raw, has_angles_map=has_batch)

    parsed = parse_ingest_file(raw, angle=single_angle)
    angles = dict(bundle.get("angles") or {})
    fp_map: dict[str, str] = dict(bundle.get("inputs_fingerprint_by_angle") or {})

    for aid, payload in parsed.items():
        if aid not in CURSOR_SCAN_ANGLE_IDS:
            raise ValueError(f"ingest not supported for angle {aid!r}")
        base = angles.get(aid)
        if not isinstance(base, dict):
            raise ValueError(f"missing angle {aid!r} in bundle (run audit first)")
        if not overwrite and base.get("cursor_scan"):
            continue
        try:
            cs = normalize_to_cursor_scan(payload, angle_id=aid)
        except ValueError as e:
            raise ValueError(f"angle {aid!r}: {e}") from e
        cs = dict(cs)
        cs["ingested_at_utc"] = now_utc()
        base["cursor_scan"] = cs
        base = reapply_cursor_merge(base)
        angles[aid] = base
        det_fp = _deterministic_fp_from_bundle_map(fp_map, aid, base)
        fp_map[aid] = angle_inputs_fingerprint(
            det_fp,
            base.get("cursor_scan") if isinstance(base.get("cursor_scan"), dict) else None,
        )

    ordered = {k: angles[k] for k in ANGLE_IDS}
    fp_sorted = dict(sorted(fp_map.items()))
    out_bundle: dict[str, Any] = {
        "schema": BUNDLE_SCHEMA,
        "product_id": product_id,
        "generated_at_utc": bundle.get("generated_at_utc", now_utc()),
        "scan_depth": bundle.get("scan_depth", "quick"),
        "inputs_fingerprint_by_angle": fp_sorted,
        "inputs_fingerprint_bundle": compute_bundle_fingerprint(fp_sorted),
        "angles": ordered,
        "cursor_ingest_at_utc": now_utc(),
    }
    if bundle.get("agent_ingest_at_utc"):
        out_bundle["agent_ingest_at_utc"] = bundle["agent_ingest_at_utc"]

    adir = audit_product_dir(root, product_id)
    adir.mkdir(parents=True, exist_ok=True)
    bundle_audit_path(root, product_id).write_text(dumps_json(out_bundle) + "\n", encoding="utf-8")
    refresh_audit_index(root, product_id)
    return out_bundle


def load_ingest_json(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("JSON root must be an object")
    return data
