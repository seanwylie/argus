"""Filesystem persistence for collected signals (no database)."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from argus.core.models.signal import SignalRecord
from argus.core.models.signal_manifest import ProductSignalManifest
from argus.core.models.validation import validate_signal_record
from argus.core.serialize import dumps_json, signal_record_from_dict, to_jsonable
from argus.disk_budget import require_disk_headroom_for_write
from argus.products.external_bindings import ExternalBindings
from argus.runs_retention import maybe_prune_signal_collections_after_save
from argus.signals.adapters.execution_outcomes import dedupe_execution_outcome_signals
from argus.signals.continuity import compute_signal_continuity
from argus.signals.external_identity import (
    SignalIdentityVerificationError,
    verify_external_identities_for_collection,
)
from argus.signals.normalize import attach_canonical_to_records

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SignalCollectionBundle:
    """One persisted collection run."""

    product_id: str
    collected_at_utc: str
    repo_root: str
    records: list[SignalRecord]
    #: Optional ``argus.signal_continuity.v1`` block (present from first diff onward).
    signal_continuity: dict[str, Any] | None = None
    #: Optional ``argus.signal_collection_external_identity.v1`` block.
    external_identity_verification: dict[str, Any] | None = None


def collections_base(repo_root: Path) -> Path:
    return repo_root / "runs" / "signals" / "collections"


def latest_path(repo_root: Path, product_id: str) -> Path:
    return repo_root / "runs" / "signals" / "latest" / f"{product_id}.json"


def save_collection(
    repo_root: Path,
    product_id: str,
    records: list[SignalRecord],
    *,
    write_latest: bool = True,
    signal_manifest: ProductSignalManifest | None = None,
    product_root: Path | None = None,
    external_bindings: ExternalBindings | None = None,
) -> tuple[Path, list[SignalRecord]]:
    """
    Write JSON bundle under ``runs/signals/collections/`` and optionally update
    ``runs/signals/latest/<product_id>.json``.

    Returns the collection path and the **persisted** records (with ``canonical`` populated).

    When ``signal_manifest`` and ``product_root`` are set, manifest rows are matched to
    records during normalization (see :func:`argus.signals.normalize.attach_canonical_to_records`).

    When ``external_bindings`` is set (from ``product.yaml``), collected rows are checked
    against declared domains/repos/analytics ids; mismatches raise
    :class:`~argus.signals.external_identity.SignalIdentityVerificationError`.
    """
    root = repo_root.resolve()
    collected_at = datetime.now(timezone.utc)
    prior_bundle = load_latest_bundle(root, product_id)
    records = dedupe_execution_outcome_signals(list(records))
    normalized = attach_canonical_to_records(
        records,
        collected_at,
        signal_manifest=signal_manifest,
        product_root=product_root,
    )
    for r in normalized:
        validate_signal_record(r)

    verification, blocking = verify_external_identities_for_collection(
        external_bindings,
        normalized,
    )
    if blocking:
        raise SignalIdentityVerificationError("; ".join(blocking))

    continuity = compute_signal_continuity(
        prior_bundle.records if prior_bundle else [],
        normalized,
        prior_collected_at_utc=prior_bundle.collected_at_utc if prior_bundle else None,
        current_collected_at=collected_at,
    )
    ts = collected_at.strftime("%Y%m%dT%H%M%SZ")
    base = collections_base(root)
    require_disk_headroom_for_write(base, op="signal collection")
    base.mkdir(parents=True, exist_ok=True)
    bundle = {
        "schema": "argus.signal_collection.v1",
        "product_id": product_id,
        "collected_at_utc": collected_at.isoformat(),
        "repo_root": str(root),
        "record_count": len(normalized),
        "records": [to_jsonable(r) for r in normalized],
        "signal_continuity": continuity,
        "external_identity_verification": verification,
    }
    path = base / f"{ts}_{product_id}.json"
    path.write_text(dumps_json(bundle), encoding="utf-8")
    if write_latest:
        lp = latest_path(root, product_id)
        lp.parent.mkdir(parents=True, exist_ok=True)
        lp.write_text(dumps_json(bundle), encoding="utf-8")

    try:
        from argus.temporal.persistence import write_temporal_artifacts_for_collection

        write_temporal_artifacts_for_collection(
            root,
            product_id,
            normalized,
            collected_at,
            signal_continuity=continuity,
        )
    except OSError as e:
        # Temporal sidecar must not block signal collection on I/O failure.
        logger.warning(
            "Temporal sidecar write skipped after I/O error (signal collection succeeded): %s",
            e,
        )

    maybe_prune_signal_collections_after_save(root)

    return path, normalized


def load_latest_bundle(repo_root: Path, product_id: str) -> SignalCollectionBundle | None:
    """Load ``runs/signals/latest/<product_id>.json`` if present."""
    lp = latest_path(repo_root.resolve(), product_id)
    if not lp.is_file():
        return None
    return load_bundle_file(lp)


def load_bundle_file(path: Path) -> SignalCollectionBundle:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("bundle must be a JSON object")
    pid = str(data.get("product_id", ""))
    ts = str(data.get("collected_at_utc", ""))
    repo = str(data.get("repo_root", ""))
    raw_records = data.get("records") or []
    records: list[SignalRecord] = []
    if isinstance(raw_records, list):
        for item in raw_records:
            if isinstance(item, dict):
                r = signal_record_from_dict(item)
                validate_signal_record(r)
                records.append(r)
    sc = data.get("signal_continuity")
    signal_continuity = sc if isinstance(sc, dict) else None
    eiv = data.get("external_identity_verification")
    external_identity_verification = eiv if isinstance(eiv, dict) else None
    return SignalCollectionBundle(
        product_id=pid,
        collected_at_utc=ts,
        repo_root=repo,
        records=records,
        signal_continuity=signal_continuity,
        external_identity_verification=external_identity_verification,
    )


def bundle_to_jsonable(bundle: SignalCollectionBundle) -> dict[str, Any]:
    out: dict[str, Any] = {
        "product_id": bundle.product_id,
        "collected_at_utc": bundle.collected_at_utc,
        "repo_root": bundle.repo_root,
        "records": [to_jsonable(r) for r in bundle.records],
    }
    if bundle.signal_continuity is not None:
        out["signal_continuity"] = bundle.signal_continuity
    if bundle.external_identity_verification is not None:
        out["external_identity_verification"] = bundle.external_identity_verification
    return out
