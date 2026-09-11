"""Ingest local temporal snapshot files (metrics/snapshots + runs/temporal/products/<id>)."""

from __future__ import annotations

import logging
from pathlib import Path

from argus.core.models.enums import SignalType
from argus.core.models.signal import SignalRecord
from argus.core.models.validation import validate_signal_record
from argus.signals.contract import ProductSignalContext, SignalAdapter
from argus.signals.snapshots.registry import match_snapshot_parser

_log = logging.getLogger(__name__)


class TemporalSnapshotSignalAdapter(SignalAdapter):
    """Parses ``*temporal_*.json|csv`` via the same registry as ``argus signals ingest-snapshots``."""

    adapter_id = "temporal_snapshots"

    @property
    def signal_type(self) -> SignalType:
        return SignalType.TEMPORAL

    def describe_capabilities(self) -> str:
        return (
            "Reads temporal_market / temporal_news / temporal_recency snapshots under "
            "metrics/snapshots and runs/temporal/snapshots/products/<id>/ (local JSON/CSV only)."
        )

    def collect(self, ctx: ProductSignalContext) -> list[SignalRecord]:
        root = ctx.repo_root.resolve()
        paths: list[Path] = []
        seen: set[str] = set()

        for base in (
            ctx.product_root / "metrics" / "snapshots",
            root / "runs" / "temporal" / "snapshots" / "products" / ctx.product_id,
            root / "fixtures" / "temporal" / "snapshots" / "products" / ctx.product_id,
        ):
            if not base.is_dir():
                continue
            for p in sorted(base.iterdir()):
                if not p.is_file():
                    continue
                if p.suffix.lower() not in (".json", ".csv"):
                    continue
                if not match_snapshot_parser(p.name):
                    continue
                key = str(p.resolve())
                if key in seen:
                    continue
                seen.add(key)
                paths.append(p)

        out: list[SignalRecord] = []
        for path in paths:
            fn = match_snapshot_parser(path.name)
            if fn is None:
                continue
            try:
                recs = fn(path, root)
            except Exception as e:
                _log.warning(
                    "temporal_snapshots: skipped malformed snapshot %s (%s: %s)",
                    path.name,
                    type(e).__name__,
                    e,
                )
                continue
            for r in recs:
                validate_signal_record(r)
                out.append(r)
        return out
