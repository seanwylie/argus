"""Heartbeat / activity: last activity from mtimes or optional marker files."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from argus.core.models.enums import SeverityLevel, SignalType
from argus.core.models.signal import SignalRecord
from argus.signals.contract import ProductSignalContext, SignalAdapter
from argus.signals.ids import new_signal_id

_STALE_AFTER = timedelta(days=14)
_MARKER = ".argus-activity"
_WATCH_DIRS = ("metrics", "app", "config", "scripts")


class HeartbeatAdapter(SignalAdapter):
    adapter_id = "heartbeat"

    @property
    def signal_type(self) -> SignalType:
        return SignalType.HEALTH

    def describe_capabilities(self) -> str:
        return (
            "Infers activity from newest mtime under metrics/, app/, config/, scripts/ "
            f"or `{_MARKER}` marker file."
        )

    def collect(self, ctx: ProductSignalContext) -> list[SignalRecord]:
        now = datetime.now(timezone.utc)
        base = ctx.product_root.resolve()
        mtimes: list[tuple[Path, datetime]] = []

        marker = base / _MARKER
        if marker.is_file():
            mtime = datetime.fromtimestamp(marker.stat().st_mtime, tz=timezone.utc)
            mtimes.append((marker, mtime))

        for sub in _WATCH_DIRS:
            d = base / sub
            if not d.is_dir():
                continue
            n_files = 0
            for p in d.rglob("*"):
                if not p.is_file():
                    continue
                if n_files >= 500:
                    break
                n_files += 1
                try:
                    ts = datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc)
                except OSError:
                    continue
                mtimes.append((p, ts))

        if not mtimes:
            return [
                SignalRecord(
                    id=new_signal_id(),
                    product_id=ctx.product_id,
                    signal_type=self.signal_type,
                    source=self.adapter_id,
                    observed_at=now,
                    payload={
                        "check": "activity",
                        "active": False,
                        "reason": "no watched files or marker found",
                    },
                    severity_hint=SeverityLevel.MEDIUM,
                    confidence=0.55,
                    tags=["health", "activity"],
                )
            ]

        newest_path, newest_ts = max(mtimes, key=lambda x: x[1])
        age = now - newest_ts
        active = age <= _STALE_AFTER

        return [
            SignalRecord(
                id=new_signal_id(),
                product_id=ctx.product_id,
                signal_type=self.signal_type,
                source=self.adapter_id,
                observed_at=now,
                payload={
                    "check": "activity",
                    "active": active,
                    "last_activity_utc": newest_ts.isoformat(),
                    "last_activity_path": str(newest_path.relative_to(base)).replace("\\", "/"),
                    "age_seconds": int(age.total_seconds()),
                    "stale": not active,
                },
                severity_hint=SeverityLevel.MEDIUM if not active else SeverityLevel.INFO,
                confidence=0.7,
                tags=["health", "heartbeat"],
            )
        ]
