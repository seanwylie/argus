"""Read local cost snapshot JSON files."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from argus.core.models.enums import SeverityLevel, SignalType
from argus.core.models.signal import SignalRecord
from argus.signals.contract import ProductSignalContext, SignalAdapter
from argus.signals.ids import new_signal_id

_CANDIDATES = (
    "metrics/cost.snapshot.json",
    "config/cost.snapshot.json",
    "metrics/cost.json",
)


class CostFileAdapter(SignalAdapter):
    adapter_id = "cost_file"

    @property
    def signal_type(self) -> SignalType:
        return SignalType.COST

    def describe_capabilities(self) -> str:
        return "Uses metrics/cost.snapshot.json or config/cost.snapshot.json when present."

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
                        tags=["cost", "parse_error"],
                    )
                )
                continue

            monthly = None
            if isinstance(data, dict):
                monthly = data.get("monthly_usd")
                if monthly is None and "primary" not in data:
                    monthly = data.get("monthly_usd_estimate")
            out.append(
                SignalRecord(
                    id=new_signal_id(),
                    product_id=ctx.product_id,
                    signal_type=self.signal_type,
                    source=self.adapter_id,
                    observed_at=now,
                    payload={
                        "file": rel,
                        "ok": True,
                        "monthly_usd": monthly,
                        "raw_keys": list(data.keys()) if isinstance(data, dict) else None,
                    },
                    severity_hint=SeverityLevel.INFO,
                    confidence=0.8,
                    tags=["cost", "snapshot"],
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
                    "check": "cost_snapshot",
                    "ok": True,
                    "note": "no cost snapshot file found; expected one of "
                    + ", ".join(_CANDIDATES),
                },
                severity_hint=SeverityLevel.INFO,
                confidence=0.4,
                tags=["cost", "empty"],
            )
        )
        return out
