"""Read local analytics snapshot JSON files."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from argus.core.models.enums import SeverityLevel, SignalType
from argus.core.models.signal import SignalRecord
from argus.signals.contract import ProductSignalContext, SignalAdapter
from argus.signals.ids import new_signal_id

_CANDIDATES = (
    "metrics/analytics.snapshot.json",
    "config/analytics.snapshot.json",
)


class AnalyticsFileAdapter(SignalAdapter):
    adapter_id = "analytics_file"

    @property
    def signal_type(self) -> SignalType:
        return SignalType.ANALYTICS

    def describe_capabilities(self) -> str:
        return "Uses metrics/analytics.snapshot.json or config/analytics.snapshot.json when present."

    def collect(self, ctx: ProductSignalContext) -> list[SignalRecord]:
        now = datetime.now(timezone.utc)
        base = ctx.product_root.resolve()
        out: list[SignalRecord] = []

        for rel in _CANDIDATES:
            path = (base / rel).resolve()
            try:
                path.relative_to(base)
            except ValueError:
                continue
            if not path.is_file():
                continue
            try:
                raw = path.read_text(encoding="utf-8")
                data = json.loads(raw)
            except (OSError, json.JSONDecodeError) as e:
                out.append(
                    SignalRecord(
                        id=new_signal_id(),
                        product_id=ctx.product_id,
                        signal_type=self.signal_type,
                        source=self.adapter_id,
                        observed_at=now,
                        payload={"file": rel, "ok": False, "error": str(e)},
                        severity_hint=SeverityLevel.MEDIUM,
                        confidence=1.0,
                        tags=["analytics", "parse_error"],
                    )
                )
                continue

            views = None
            payload: dict[str, Any] = {
                "file": rel,
                "ok": True,
                "views": None,
                "snippet": data if isinstance(data, dict) else {"value": data},
            }
            if isinstance(data, dict):
                views = data.get("views") or data.get("pageviews")
                payload["views"] = views
                for k in (
                    "site_hostname",
                    "domain",
                    "hostname",
                    "ga_property_id",
                    "property_id",
                    "repo_url",
                ):
                    v = data.get(k)
                    if isinstance(v, str) and v.strip():
                        payload[k] = v.strip()
            out.append(
                SignalRecord(
                    id=new_signal_id(),
                    product_id=ctx.product_id,
                    signal_type=self.signal_type,
                    source=self.adapter_id,
                    observed_at=now,
                    payload=payload,
                    severity_hint=SeverityLevel.INFO,
                    confidence=0.75,
                    tags=["analytics", "snapshot"],
                )
            )
            return out

        out.append(
            SignalRecord(
                id=new_signal_id(),
                product_id=ctx.product_id,
                signal_type=self.signal_type,
                source=self.adapter_id,
                observed_at=now,
                payload={
                    "check": "analytics_snapshot",
                    "ok": True,
                    "note": "no analytics snapshot file found; expected one of "
                    + ", ".join(_CANDIDATES),
                },
                severity_hint=SeverityLevel.INFO,
                confidence=0.4,
                tags=["analytics", "empty"],
            )
        )
        return out
