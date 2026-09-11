"""Scan local snapshot files and emit :class:`SignalRecord` lists."""

from __future__ import annotations

from pathlib import Path

from argus.core.models.product import ProductNode
from argus.core.models.signal import SignalRecord
from argus.core.models.validation import validate_signal_record
from argus.signals.snapshots.binding import BindingError
from argus.signals.snapshots.registry import match_snapshot_parser


def _collect_paths(
    repo_root: Path,
    node: ProductNode,
    *,
    include_fixtures: bool,
) -> list[Path]:
    """Product ``metrics/snapshots`` first; fixtures only for basenames not already seen."""
    root = repo_root.resolve()
    paths: list[Path] = []
    seen_names: set[str] = set()

    d_prod = (root / node.product_root / "metrics" / "snapshots").resolve()
    if d_prod.is_dir():
        for p in sorted(d_prod.iterdir()):
            if not p.is_file():
                continue
            if p.suffix.lower() not in (".json", ".csv"):
                continue
            if not match_snapshot_parser(p.name):
                continue
            paths.append(p)
            seen_names.add(p.name)

    for rel in (
        root / "runs" / "temporal" / "snapshots" / "products" / node.id,
        root / "fixtures" / "temporal" / "snapshots" / "products" / node.id,
    ):
        d_extra = rel.resolve()
        if not d_extra.is_dir():
            continue
        for p in sorted(d_extra.iterdir()):
            if not p.is_file():
                continue
            if p.suffix.lower() not in (".json", ".csv"):
                continue
            if not match_snapshot_parser(p.name):
                continue
            if p.name in seen_names:
                continue
            paths.append(p)
            seen_names.add(p.name)

    if include_fixtures:
        d_fix = (root / "fixtures" / "business_snapshots" / node.id).resolve()
        if d_fix.is_dir():
            for p in sorted(d_fix.iterdir()):
                if not p.is_file():
                    continue
                if p.suffix.lower() not in (".json", ".csv"):
                    continue
                if not match_snapshot_parser(p.name):
                    continue
                if p.name in seen_names:
                    continue
                paths.append(p)
                seen_names.add(p.name)
    return paths


def ingest_snapshots_for_product(
    repo_root: Path,
    node: ProductNode,
    *,
    include_fixtures: bool = True,
) -> list[SignalRecord]:
    """
    Parse recognized snapshot files under ``metrics/snapshots``,
    ``runs/temporal/snapshots/products/<id>/`` (and the same under ``fixtures/temporal/...``),
    and optionally ``fixtures/business_snapshots/<id>/`` (fixtures skipped if same basename exists in product dir).
    """
    paths = _collect_paths(repo_root, node, include_fixtures=include_fixtures)
    out: list[SignalRecord] = []
    for path in paths:
        fn = match_snapshot_parser(path.name)
        if fn is None:
            continue
        try:
            recs = fn(path, repo_root)
        except BindingError as e:
            raise BindingError(f"{path}: {e}") from e
        for r in recs:
            validate_signal_record(r)
            out.append(r)
    return out
