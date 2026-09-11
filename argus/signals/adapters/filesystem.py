"""Filesystem layout: expected dirs, file presence, stale mtimes."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from argus.core.models.enums import SeverityLevel, SignalType
from argus.core.models.signal import SignalRecord
from argus.signals.contract import ProductSignalContext, SignalAdapter
from argus.signals.ids import new_signal_id

_STALE_AFTER = timedelta(days=7)


class FilesystemAdapter(SignalAdapter):
    adapter_id = "filesystem"

    @property
    def signal_type(self) -> SignalType:
        return SignalType.FILESYSTEM

    def describe_capabilities(self) -> str:
        return "Uses metrics.local_paths from product.yaml; checks dirs exist and file mtimes."

    def collect(self, ctx: ProductSignalContext) -> list[SignalRecord]:
        now = datetime.now(timezone.utc)
        out: list[SignalRecord] = []
        base = ctx.product_root.resolve()

        for rel in ctx.product.metrics.local_paths or ["metrics"]:
            rel = rel.strip().lstrip("/")
            target = (base / rel).resolve()
            try:
                target.relative_to(base)
            except ValueError:
                out.append(
                    SignalRecord(
                        id=new_signal_id(),
                        product_id=ctx.product_id,
                        signal_type=self.signal_type,
                        source=self.adapter_id,
                        observed_at=now,
                        payload={
                            "check": "metrics_path",
                            "relative_path": rel,
                            "ok": False,
                            "reason": "path_escapes_product_root",
                        },
                        severity_hint=SeverityLevel.HIGH,
                        confidence=1.0,
                        tags=["filesystem", "layout"],
                    )
                )
                continue

            if not target.exists():
                out.append(
                    SignalRecord(
                        id=new_signal_id(),
                        product_id=ctx.product_id,
                        signal_type=self.signal_type,
                        source=self.adapter_id,
                        observed_at=now,
                        payload={
                            "check": "path_exists",
                            "relative_path": rel,
                            "ok": False,
                            "path": str(target),
                        },
                        severity_hint=SeverityLevel.MEDIUM,
                        confidence=1.0,
                        tags=["filesystem", "missing"],
                    )
                )
                continue

            if target.is_file():
                mtime = datetime.fromtimestamp(target.stat().st_mtime, tz=timezone.utc)
                age = now - mtime
                out.append(
                    SignalRecord(
                        id=new_signal_id(),
                        product_id=ctx.product_id,
                        signal_type=self.signal_type,
                        source=self.adapter_id,
                        observed_at=now,
                        payload={
                            "check": "file_mtime",
                            "relative_path": rel,
                            "path": str(target),
                            "mtime_utc": mtime.isoformat(),
                            "age_seconds": int(age.total_seconds()),
                            "stale": age > _STALE_AFTER,
                        },
                        severity_hint=(
                            SeverityLevel.MEDIUM if age > _STALE_AFTER else SeverityLevel.INFO
                        ),
                        confidence=0.9,
                        tags=["filesystem", "file"],
                    )
                )
                continue

            if target.is_dir():
                files = [p for p in target.rglob("*") if p.is_file()]
                if not files:
                    out.append(
                        SignalRecord(
                            id=new_signal_id(),
                            product_id=ctx.product_id,
                            signal_type=self.signal_type,
                            source=self.adapter_id,
                            observed_at=now,
                            payload={
                                "check": "directory_empty",
                                "relative_path": rel,
                                "path": str(target),
                                "file_count": 0,
                            },
                            severity_hint=SeverityLevel.LOW,
                            confidence=0.85,
                            tags=["filesystem", "empty"],
                        )
                    )
                    continue
                newest = max(files, key=lambda p: p.stat().st_mtime)
                mtime = datetime.fromtimestamp(newest.stat().st_mtime, tz=timezone.utc)
                age = now - mtime
                out.append(
                    SignalRecord(
                        id=new_signal_id(),
                        product_id=ctx.product_id,
                        signal_type=self.signal_type,
                        source=self.adapter_id,
                        observed_at=now,
                        payload={
                            "check": "directory_freshness",
                            "relative_path": rel,
                            "path": str(target),
                            "newest_file": str(newest.relative_to(base)),
                            "newest_mtime_utc": mtime.isoformat(),
                            "file_count": len(files),
                            "stale": age > _STALE_AFTER,
                        },
                        severity_hint=(
                            SeverityLevel.MEDIUM if age > _STALE_AFTER else SeverityLevel.INFO
                        ),
                        confidence=0.85,
                        tags=["filesystem", "directory"],
                    )
                )

        return out
