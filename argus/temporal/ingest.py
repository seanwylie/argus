"""Scan repo-level temporal snapshot trees and persist signal bundles."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any

from argus.core.models.validation import validate_signal_record
from argus.experiments.execution_apply import apply_execution_outcomes
from argus.products.external_bindings import parse_external_bindings
from argus.products.inventory import build_inventory
from argus.signals.persistence import load_latest_bundle, save_collection
from argus.signals.runner import product_root_path
from argus.signals.snapshots.binding import BindingError
from argus.signals.snapshots.registry import match_snapshot_parser


def iter_temporal_snapshot_files(repo_root: Path) -> list[Path]:
    """All JSON/CSV files under temporal snapshot roots that match a registered parser."""
    root = repo_root.resolve()
    bases = [
        root / "runs" / "temporal" / "snapshots",
        root / "fixtures" / "temporal" / "snapshots",
    ]
    out: list[Path] = []
    for base in bases:
        if not base.is_dir():
            continue
        for p in base.rglob("*"):
            if not p.is_file():
                continue
            if p.suffix.lower() not in (".json", ".csv"):
                continue
            if match_snapshot_parser(p.name):
                out.append(p)
    # Deterministic order
    return sorted(set(out), key=lambda x: str(x))


def ingest_temporal_snapshots(
    repo_root: Path,
    *,
    product_id: str | None = None,
    merge: bool = True,
    no_save: bool = False,
) -> dict[str, Any]:
    """
    Parse every temporal snapshot file under ``runs/temporal/snapshots`` and
    ``fixtures/temporal/snapshots``, group by ``SignalRecord.product_id``, optionally
    merge with prior bundles, and save.

    If ``product_id`` is set, only records whose ``product_id`` matches are kept.
    """
    root = repo_root.resolve()
    by_pid: dict[str, list] = defaultdict(list)
    errors: list[str] = []

    for path in iter_temporal_snapshot_files(root):
        fn = match_snapshot_parser(path.name)
        if fn is None:
            continue
        try:
            recs = fn(path, root)
        except BindingError as e:
            errors.append(f"{path}: {e}")
            continue
        except (OSError, ValueError, TypeError) as e:
            errors.append(f"{path}: {e}")
            continue
        for r in recs:
            validate_signal_record(r)
            if product_id is not None and r.product_id != product_id:
                continue
            by_pid[r.product_id].append(r)

    inv_for_manifest = build_inventory(root) if not no_save else None
    saved: dict[str, int] = {}
    for pid, recs in sorted(by_pid.items()):
        if not recs:
            continue
        final = list(recs)
        if merge:
            prev = load_latest_bundle(root, pid)
            if prev is not None:
                final = list(prev.records) + final
        if not no_save:
            vr = inv_for_manifest.valid.get(pid) if inv_for_manifest is not None else None
            if vr is not None:
                n = vr.node
                _, _ = save_collection(
                    root,
                    pid,
                    final,
                    signal_manifest=n.signal_manifest,
                    product_root=product_root_path(root, n),
                    external_bindings=parse_external_bindings(n.raw_extensions),
                )
            else:
                _, _ = save_collection(root, pid, final)
        saved[pid] = len(recs)

    if not no_save:
        apply_execution_outcomes(root)

    return {
        "saved_counts": saved,
        "errors": errors,
        "product_filter": product_id,
    }
