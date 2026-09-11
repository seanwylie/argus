"""Deterministic local snapshot files → :class:`~argus.core.models.signal.SignalRecord`.

Supports JSON and CSV under ``products/<id>/metrics/snapshots/local/`` with bounded reads,
explicit collection status, and stable provenance fields in ``payload``.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from argus.core.models.enums import SeverityLevel, SignalType
from argus.core.models.signal import SignalRecord
from argus.signals.ids import new_signal_id
from argus.signals.snapshots.binding import BindingError, parse_csv_dicts, resolve_product_id
from argus.signals.snapshots.normalize import parse_observed_at

ADAPTER_ID = "local_snapshots"
SCHEMA_ENVELOPE = "argus.local_snapshot.v1"
SCHEMA_MANIFEST = "argus.local_snapshot_manifest.v1"

# Deterministic, no network: cap bytes and CSV rows.
_MAX_FILE_BYTES = 512 * 1024
_MAX_CSV_ROWS = 500

_SOURCE_KINDS = frozenset(
    {
        "json_snapshot",
        "csv_snapshot",
        "artifact",
        "summary",
        "manual",
    }
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _rel_ref(path: Path, repo_root: Path) -> str:
    try:
        return path.resolve().relative_to(repo_root.resolve()).as_posix()
    except ValueError:
        return path.name


def _safe_under_product(path: Path, product_root: Path) -> bool:
    try:
        path.resolve().relative_to(product_root.resolve())
        return True
    except ValueError:
        return False


def _safe_runs_signals_path(path: Path, repo_root: Path, product_id: str) -> bool:
    """Allow ``runs/signals/snapshots/products/<id>/`` (repo-local artifact drop)."""
    base = (repo_root / "runs" / "signals" / "snapshots" / "products" / product_id).resolve()
    try:
        path.resolve().relative_to(base)
        return True
    except ValueError:
        return False


def _allowed_snapshot_path(path: Path, repo_root: Path, product_id: str, product_root: Path) -> bool:
    return _safe_under_product(path, product_root) or _safe_runs_signals_path(
        path, repo_root, product_id
    )


def infer_source_kind(filename: str) -> str:
    """Map filename prefix to ``source_kind`` (deterministic)."""
    n = filename.lower()
    if n.startswith("summary_"):
        return "summary"
    if n.startswith("manual_"):
        return "manual"
    if n.startswith("artifact_"):
        return "artifact"
    if n.endswith(".csv"):
        return "csv_snapshot"
    if n.endswith(".json"):
        return "json_snapshot"
    return "json_snapshot"


def _coerce_snapshot_kind(raw: Any, filename: str) -> str:
    if raw is not None and str(raw).strip():
        k = str(raw).strip().lower()
        if k in _SOURCE_KINDS:
            return k
    return infer_source_kind(filename)


def _error_record(
    *,
    product_id: str,
    adapter_id: str,
    source_ref: str,
    collection_status: str,
    detail: str,
    observed_at: datetime,
    source_kind: str = "json_snapshot",
    severity: SeverityLevel = SeverityLevel.MEDIUM,
) -> SignalRecord:
    return SignalRecord(
        id=new_signal_id("lsnap"),
        product_id=product_id,
        signal_type=SignalType.CUSTOM,
        source=adapter_id,
        observed_at=observed_at,
        payload={
            "schema": SCHEMA_ENVELOPE,
            "collection_status": collection_status,
            "source_ref": source_ref,
            "source_kind": source_kind,
            "provenance": {
                "adapter_id": adapter_id,
                "ingest": "deterministic_v1",
            },
            "detail": detail[:2000],
        },
        severity_hint=severity,
        confidence=1.0,
        tags=sorted({"local_snapshot", "snapshot", collection_status}),
    )


def _read_bytes_bounded(path: Path) -> tuple[str | None, str | None]:
    """Return (text, error_code) — error_code is path_escape|oversized|read_error."""
    try:
        raw = path.read_bytes()
    except OSError as e:
        return None, f"read_error:{e}"
    if len(raw) > _MAX_FILE_BYTES:
        return None, "oversized"
    try:
        return raw.decode("utf-8"), None
    except UnicodeDecodeError as e:
        return None, f"read_error:{e}"


def _parse_manifest(text: str) -> dict[str, Any] | None:
    try:
        raw = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(raw, dict):
        return None
    if raw.get("schema") != SCHEMA_MANIFEST:
        return None
    return raw


def parse_local_json_payload(
    data: dict[str, Any],
    *,
    path: Path,
    repo_root: Path,
) -> tuple[str, datetime, dict[str, Any], str]:
    """
    Returns (product_id, observed_at, normalized_data_dict, schema_label).

    ``schema_label`` is SCHEMA_ENVELOPE or ``raw_json``.
    """
    if data.get("schema") == SCHEMA_ENVELOPE:
        pid = resolve_product_id(path, repo_root, data)
        obs = parse_observed_at(data.get("observed_at"), fallback=_utc_now())
        inner = data.get("data")
        if inner is None:
            inner = {}
        if not isinstance(inner, dict):
            inner = {"value": inner}
        sk = _coerce_snapshot_kind(data.get("snapshot_kind"), path.name)
        merged = {
            "snapshot_kind": sk,
            **inner,
        }
        return pid, obs, merged, SCHEMA_ENVELOPE
    pid = resolve_product_id(path, repo_root, data)
    obs = parse_observed_at(data.get("observed_at"), fallback=_utc_now())
    sk = _coerce_snapshot_kind(data.get("snapshot_kind"), path.name)
    return pid, obs, {**data, "snapshot_kind": sk}, "raw_json"


def record_from_local_file(
    path: Path,
    *,
    repo_root: Path,
    product_root: Path,
    product_id_expected: str,
    adapter_id: str = ADAPTER_ID,
) -> SignalRecord:
    """
    Build one signal record for a single file under ``product_root`` (deterministic).
    """
    now = _utc_now()
    repo_root = repo_root.resolve()
    product_root = product_root.resolve()
    source_ref = _rel_ref(path, repo_root)

    if not _allowed_snapshot_path(path, repo_root, product_id_expected, product_root):
        return _error_record(
            product_id=product_id_expected,
            adapter_id=adapter_id,
            source_ref=source_ref,
            collection_status="path_escape",
            detail="path must be under product tree or runs/signals/snapshots/products/<id>/",
            observed_at=now,
            severity=SeverityLevel.HIGH,
        )

    text, err = _read_bytes_bounded(path)
    if err:
        status = "oversized" if err == "oversized" else "read_error"
        return _error_record(
            product_id=product_id_expected,
            adapter_id=adapter_id,
            source_ref=source_ref,
            collection_status=status,
            detail=err,
            observed_at=now,
            source_kind=infer_source_kind(path.name),
        )

    assert text is not None

    if path.suffix.lower() == ".csv":
        return _record_csv(
            text,
            path=path,
            repo_root=repo_root,
            product_id_expected=product_id_expected,
            adapter_id=adapter_id,
            source_ref=source_ref,
            now=now,
        )

    if path.suffix.lower() != ".json":
        return _error_record(
            product_id=product_id_expected,
            adapter_id=adapter_id,
            source_ref=source_ref,
            collection_status="invalid_schema",
            detail="only .json and .csv are supported",
            observed_at=now,
        )

    try:
        loaded = json.loads(text)
    except json.JSONDecodeError as e:
        return _error_record(
            product_id=product_id_expected,
            adapter_id=adapter_id,
            source_ref=source_ref,
            collection_status="invalid_schema",
            detail=str(e),
            observed_at=now,
            source_kind="json_snapshot",
        )

    if not isinstance(loaded, dict):
        return _error_record(
            product_id=product_id_expected,
            adapter_id=adapter_id,
            source_ref=source_ref,
            collection_status="invalid_schema",
            detail="JSON root must be an object",
            observed_at=now,
            source_kind="json_snapshot",
        )

    try:
        pid, observed_at, norm, schema_label = parse_local_json_payload(loaded, path=path, repo_root=repo_root)
    except BindingError as e:
        return _error_record(
            product_id=product_id_expected,
            adapter_id=adapter_id,
            source_ref=source_ref,
            collection_status="invalid_binding",
            detail=str(e),
            observed_at=now,
            source_kind="json_snapshot",
        )

    if pid != product_id_expected:
        return _error_record(
            product_id=product_id_expected,
            adapter_id=adapter_id,
            source_ref=source_ref,
            collection_status="invalid_binding",
            detail=f"product_id {pid!r} does not match context {product_id_expected!r}",
            observed_at=now,
            source_kind=_coerce_snapshot_kind(norm.get("snapshot_kind"), path.name),
        )

    sk = _coerce_snapshot_kind(norm.get("snapshot_kind"), path.name)
    content_fingerprint = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
    payload: dict[str, Any] = {
        "schema": schema_label,
        "collection_status": "ok",
        "source_ref": source_ref,
        "source_kind": sk,
        "provenance": {
            "adapter_id": adapter_id,
            "ingest": "deterministic_v1",
            "content_sha256_16": content_fingerprint,
        },
        "data": norm,
        "file_bytes": len(text.encode("utf-8")),
    }
    return SignalRecord(
        id=new_signal_id("lsnap"),
        product_id=pid,
        signal_type=SignalType.CUSTOM,
        source=adapter_id,
        observed_at=observed_at,
        payload=payload,
        severity_hint=SeverityLevel.INFO,
        confidence=0.9,
        tags=sorted({"local_snapshot", "snapshot", sk, "ok"}),
    )


def _record_csv(
    text: str,
    *,
    path: Path,
    repo_root: Path,
    product_id_expected: str,
    adapter_id: str,
    source_ref: str,
    now: datetime,
) -> SignalRecord:
    rows = parse_csv_dicts(text)
    if not rows:
        return _error_record(
            product_id=product_id_expected,
            adapter_id=adapter_id,
            source_ref=source_ref,
            collection_status="invalid_schema",
            detail="CSV has no data rows",
            observed_at=now,
            source_kind="csv_snapshot",
        )
    head = rows[0]
    try:
        pid = resolve_product_id(path, repo_root, None, csv_first_row=head)
    except BindingError as e:
        return _error_record(
            product_id=product_id_expected,
            adapter_id=adapter_id,
            source_ref=source_ref,
            collection_status="invalid_binding",
            detail=str(e),
            observed_at=now,
            source_kind="csv_snapshot",
        )
    if pid != product_id_expected:
        return _error_record(
            product_id=product_id_expected,
            adapter_id=adapter_id,
            source_ref=source_ref,
            collection_status="invalid_binding",
            detail=f"product_id {pid!r} does not match context {product_id_expected!r}",
            observed_at=now,
            source_kind="csv_snapshot",
        )
    observed_at = parse_observed_at(head.get("observed_at"), fallback=now)
    sk = _coerce_snapshot_kind(head.get("snapshot_kind"), path.name)
    trimmed = rows[:_MAX_CSV_ROWS]
    payload: dict[str, Any] = {
        "schema": "raw_csv",
        "collection_status": "ok",
        "source_ref": source_ref,
        "source_kind": sk,
        "provenance": {
            "adapter_id": adapter_id,
            "ingest": "deterministic_v1",
            "row_count": len(trimmed),
            "truncated": len(rows) > len(trimmed),
        },
        "data": {"rows": trimmed},
        "file_bytes": len(text.encode("utf-8")),
    }
    return SignalRecord(
        id=new_signal_id("lsnap"),
        product_id=pid,
        signal_type=SignalType.CUSTOM,
        source=adapter_id,
        observed_at=observed_at,
        payload=payload,
        severity_hint=SeverityLevel.INFO,
        confidence=0.85,
        tags=sorted({"local_snapshot", "snapshot", sk, "ok"}),
    )


@dataclass
class ManifestEntry:
    rel_path: str
    source_kind: str
    required: bool


def parse_manifest_entries(manifest: dict[str, Any]) -> list[ManifestEntry]:
    raw = manifest.get("entries")
    if not isinstance(raw, list):
        return []
    out: list[ManifestEntry] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        rp = item.get("rel_path") or item.get("path")
        if not rp or not str(rp).strip():
            continue
        rel = str(rp).strip().replace("\\", "/")
        if rel.startswith("/") or ".." in rel.split("/"):
            continue
        sk = str(item.get("source_kind") or "json_snapshot").strip().lower()
        if sk not in _SOURCE_KINDS:
            sk = "json_snapshot"
        req = bool(item.get("required", False))
        out.append(ManifestEntry(rel_path=rel, source_kind=sk, required=req))
    return out


def record_manifest_missing_file(
    *,
    product_id: str,
    adapter_id: str,
    rel_path: str,
    source_kind: str,
    repo_root: Path,
    product_root: Path,
) -> SignalRecord:
    """Explicit degraded record for a manifest entry pointing at a missing file."""
    now = _utc_now()
    expected = (product_root / rel_path).resolve()
    source_ref = _rel_ref(expected, repo_root)
    return _error_record(
        product_id=product_id,
        adapter_id=adapter_id,
        source_ref=source_ref,
        collection_status="missing",
        detail=f"manifest required file not found: {rel_path}",
        observed_at=now,
        source_kind=source_kind,
        severity=SeverityLevel.MEDIUM,
    )
