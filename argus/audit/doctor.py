"""Doctor hooks for audit artifacts (informational; non-fatal)."""

from __future__ import annotations

import json
from pathlib import Path

from argus.audit.bundle import ANGLE_IDS, BUNDLE_SCHEMA
from argus.audit.models import AuditSummary
from argus.products.inventory import build_inventory


def check_audit_artifacts(repo: Path) -> tuple[list[str], list[str], list[str]]:
    """
    Return (errors, warnings, info) for audit JSON under ``runs/audit/``.

    Missing per-product audits are **info**, not errors.
    """
    errors: list[str] = []
    warnings: list[str] = []
    info: list[str] = []

    base = repo / "runs" / "audit"
    if not base.is_dir():
        return errors, warnings, info

    inv = build_inventory(repo)
    for pid in sorted(inv.valid.keys()):
        adir = base / pid
        bundle_p = adir / "bundle.json"
        latest_p = adir / "latest.json"

        if bundle_p.is_file():
            try:
                raw = json.loads(bundle_p.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as e:
                warnings.append(f"{bundle_p.relative_to(repo)}: invalid JSON ({e})")
                continue
            if not isinstance(raw, dict):
                warnings.append(f"{bundle_p.relative_to(repo)}: expected JSON object")
                continue
            if str(raw.get("schema", "")) != BUNDLE_SCHEMA:
                warnings.append(
                    f"{bundle_p.relative_to(repo)}: expected schema {BUNDLE_SCHEMA!r}, got {raw.get('schema')!r}"
                )
            angles = raw.get("angles") if isinstance(raw.get("angles"), dict) else {}
            for aid in ANGLE_IDS:
                if aid not in angles:
                    warnings.append(f"{pid}: bundle missing angle {aid!r}")
                    continue
                ang = angles[aid]
                if not isinstance(ang, dict):
                    warnings.append(f"{pid}: angle {aid!r} not an object")
                    continue
                sl = ang.get("summary_lines")
                if not isinstance(sl, list) or len(sl) < 1:
                    warnings.append(f"{pid}: angle {aid!r} needs >=1 summary_lines")
            if not latest_p.is_file():
                warnings.append(
                    f"{pid}: bundle.json exists but latest.json missing — run `argus audit run --product-id {pid}`"
                )
            continue

        if not latest_p.is_file():
            info.append(
                f"{pid}: no audit yet — `argus audit run --product-id {pid}` (grounds context packets & ideas)"
            )
            continue
        try:
            raw = json.loads(latest_p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            warnings.append(f"{latest_p.relative_to(repo)}: invalid JSON ({e})")
            continue
        if not isinstance(raw, dict):
            warnings.append(f"{latest_p.relative_to(repo)}: expected JSON object")
            continue
        parsed = AuditSummary.from_dict(raw)
        if parsed is None:
            warnings.append(f"{latest_p.relative_to(repo)}: not a valid argus.audit_summary.v1 payload")

    idx = base / "index.json"
    if idx.is_file():
        try:
            ir = json.loads(idx.read_text(encoding="utf-8"))
            if isinstance(ir, dict) and ir.get("schema") not in (None, "argus.audit_index.v1"):
                info.append(f"runs/audit/index.json: unexpected schema {ir.get('schema')!r}")
        except (OSError, json.JSONDecodeError):
            warnings.append("runs/audit/index.json: not valid JSON")

    return errors, warnings, info
