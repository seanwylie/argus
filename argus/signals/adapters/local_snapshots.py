"""Collect deterministic local snapshot files from ``metrics/snapshots/local/``."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from argus.core.models.enums import SeverityLevel, SignalType
from argus.core.models.signal import SignalRecord
from argus.core.models.validation import validate_signal_record
from argus.signals.contract import ProductSignalContext, SignalAdapter
from argus.signals.ids import new_signal_id
from argus.signals.snapshots.local_generic import (
    ADAPTER_ID,
    SCHEMA_MANIFEST,
    parse_manifest_entries,
    record_from_local_file,
    record_manifest_missing_file,
)

_log = logging.getLogger(__name__)

_MANIFEST_NAME = "manifest.json"
_MANIFEST_MAX_BYTES = 64 * 1024


class LocalSnapshotAdapter(SignalAdapter):
    """
    Reads ``products/<id>/metrics/snapshots/local/`` — JSON/CSV snapshots with bounded reads.

    Optional ``manifest.json`` (schema ``argus.local_snapshot_manifest.v1``) lists paths;
    ``required: true`` entries produce explicit ``collection_status: missing`` records when absent.
    """

    adapter_id = ADAPTER_ID

    @property
    def signal_type(self) -> SignalType:
        return SignalType.CUSTOM

    def describe_capabilities(self) -> str:
        return (
            "metrics/snapshots/local/*.json|csv (optional manifest.json), "
            "and runs/signals/snapshots/products/<id>/; bounded reads; "
            "provenance and collection_status in payload."
        )

    def collect(self, ctx: ProductSignalContext) -> list[SignalRecord]:
        root = ctx.repo_root.resolve()
        product_root = ctx.product_root.resolve()
        local_dir = (product_root / "metrics" / "snapshots" / "local").resolve()
        runs_dir = (root / "runs" / "signals" / "snapshots" / "products" / ctx.product_id).resolve()
        now = datetime.now(timezone.utc)
        out: list[SignalRecord] = []

        if not local_dir.is_dir() and not runs_dir.is_dir():
            return out

        candidates: dict[str, Path] = {}

        for base in (local_dir, runs_dir):
            if not base.is_dir():
                continue
            for p in sorted(base.iterdir()):
                if not p.is_file():
                    continue
                if p.name == _MANIFEST_NAME:
                    continue
                if p.suffix.lower() not in (".json", ".csv"):
                    continue
                candidates[str(p.resolve())] = p

        manifest_path = local_dir / _MANIFEST_NAME
        if manifest_path.is_file():
            try:
                raw_bytes = manifest_path.read_bytes()
                if len(raw_bytes) > _MANIFEST_MAX_BYTES:
                    out.append(
                        _manifest_error_record(
                            ctx.product_id,
                            self.adapter_id,
                            now,
                            "manifest exceeds size bound",
                        )
                    )
                else:
                    manifest = json.loads(raw_bytes.decode("utf-8"))
                    if not isinstance(manifest, dict) or manifest.get("schema") != SCHEMA_MANIFEST:
                        out.append(
                            _manifest_error_record(
                                ctx.product_id,
                                self.adapter_id,
                                now,
                                "manifest schema must be argus.local_snapshot_manifest.v1",
                            )
                        )
                    else:
                        for ent in parse_manifest_entries(manifest):
                            cand = (local_dir / ent.rel_path).resolve()
                            try:
                                cand.relative_to(local_dir)
                            except ValueError:
                                out.append(
                                    SignalRecord(
                                        id=new_signal_id("lsnap"),
                                        product_id=ctx.product_id,
                                        signal_type=self.signal_type,
                                        source=self.adapter_id,
                                        observed_at=now,
                                        payload={
                                            "schema": SCHEMA_MANIFEST,
                                            "collection_status": "invalid_schema",
                                            "source_ref": ent.rel_path,
                                            "detail": "manifest path escapes local dir",
                                            "provenance": {"adapter_id": self.adapter_id},
                                        },
                                        severity_hint=SeverityLevel.HIGH,
                                        confidence=1.0,
                                        tags=["local_snapshot", "manifest"],
                                    )
                                )
                                continue
                            if not cand.is_file():
                                if ent.required:
                                    out.append(
                                        record_manifest_missing_file(
                                            product_id=ctx.product_id,
                                            adapter_id=self.adapter_id,
                                            rel_path=f"metrics/snapshots/local/{ent.rel_path}",
                                            source_kind=ent.source_kind,
                                            repo_root=root,
                                            product_root=product_root,
                                        )
                                    )
                                continue
                            candidates[str(cand)] = cand
            except json.JSONDecodeError as e:
                out.append(
                    _manifest_error_record(ctx.product_id, self.adapter_id, now, str(e)[:500])
                )
            except OSError as e:
                _log.warning("local_snapshots: manifest read failed: %s", e)

        for path in sorted(candidates.values(), key=lambda x: x.as_posix()):
            rec = record_from_local_file(
                path,
                repo_root=root,
                product_root=product_root,
                product_id_expected=ctx.product_id,
                adapter_id=self.adapter_id,
            )
            validate_signal_record(rec)
            out.append(rec)

        return out


def _manifest_error_record(product_id: str, adapter_id: str, now: datetime, detail: str) -> SignalRecord:
    return SignalRecord(
        id=new_signal_id("lsnap"),
        product_id=product_id,
        signal_type=SignalType.CUSTOM,
        source=adapter_id,
        observed_at=now,
        payload={
            "schema": SCHEMA_MANIFEST,
            "collection_status": "invalid_schema",
            "source_ref": "metrics/snapshots/local/manifest.json",
            "detail": detail,
            "provenance": {"adapter_id": adapter_id, "ingest": "deterministic_v1"},
        },
        severity_hint=SeverityLevel.MEDIUM,
        confidence=1.0,
        tags=["local_snapshot", "manifest", "invalid_schema"],
    )
