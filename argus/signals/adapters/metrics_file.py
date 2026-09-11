"""Read local JSON / JSONL metrics artifacts under ``metrics/``."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from argus.core.models.enums import SeverityLevel, SignalType
from argus.core.models.signal import SignalRecord
from argus.signals.contract import ProductSignalContext, SignalAdapter
from argus.signals.ids import new_signal_id

_MAX_JSONL_LINES = 50


class MetricsFileAdapter(SignalAdapter):
    adapter_id = "metrics_file"

    @property
    def signal_type(self) -> SignalType:
        return SignalType.METRICS

    def describe_capabilities(self) -> str:
        return "Reads metrics/*.json, metrics.json, metrics.jsonl, metrics.example.json when present."

    def collect(self, ctx: ProductSignalContext) -> list[SignalRecord]:
        now = datetime.now(timezone.utc)
        base = ctx.product_root / "metrics"
        out: list[SignalRecord] = []

        candidates: list[Path] = []
        if base.is_dir():
            candidates.extend(sorted(base.glob("*.json")))
            candidates.extend(sorted(base.glob("*.jsonl")))
            for name in ("metrics.json", "metrics.jsonl", "metrics.example.json"):
                p = base / name
                if p.is_file() and p not in candidates:
                    candidates.append(p)

        for path in candidates:
            if not path.is_file():
                continue
            try:
                raw = path.read_text(encoding="utf-8", errors="replace")
            except OSError as e:
                out.append(
                    SignalRecord(
                        id=new_signal_id(),
                        product_id=ctx.product_id,
                        signal_type=self.signal_type,
                        source=self.adapter_id,
                        observed_at=now,
                        payload={"file": str(path), "ok": False, "error": str(e)},
                        severity_hint=SeverityLevel.MEDIUM,
                        confidence=1.0,
                        tags=["metrics", "io_error"],
                    )
                )
                continue

            rel = path.relative_to(ctx.product_root.resolve())
            if path.suffix.lower() == ".jsonl":
                lines = [ln for ln in raw.splitlines() if ln.strip()][: _MAX_JSONL_LINES]
                parsed: list[object] = []
                for ln in lines:
                    try:
                        parsed.append(json.loads(ln))
                    except json.JSONDecodeError:
                        parsed.append({"parse_error": True, "line": ln[:200]})
                payload = {
                    "file": str(rel).replace("\\", "/"),
                    "format": "jsonl",
                    "line_count": len(lines),
                    "samples": parsed,
                }
            else:
                try:
                    data = json.loads(raw)
                except json.JSONDecodeError as e:
                    out.append(
                        SignalRecord(
                            id=new_signal_id(),
                            product_id=ctx.product_id,
                            signal_type=self.signal_type,
                            source=self.adapter_id,
                            observed_at=now,
                            payload={
                                "file": str(rel).replace("\\", "/"),
                                "ok": False,
                                "error": str(e),
                            },
                            severity_hint=SeverityLevel.MEDIUM,
                            confidence=1.0,
                            tags=["metrics", "parse_error"],
                        )
                    )
                    continue
                payload = {
                    "file": str(rel).replace("\\", "/"),
                    "format": "json",
                    "data": data if isinstance(data, dict) else {"value": data},
                }

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
                    tags=["metrics", "file"],
                )
            )

        if not out:
            out.append(
                SignalRecord(
                    id=new_signal_id(),
                    product_id=ctx.product_id,
                    signal_type=self.signal_type,
                    source=self.adapter_id,
                    observed_at=now,
                    payload={
                        "check": "metrics_files",
                        "ok": True,
                        "note": "no JSON/JSONL metrics files found under metrics/",
                    },
                    severity_hint=SeverityLevel.INFO,
                    confidence=0.5,
                    tags=["metrics", "empty"],
                )
            )

        return out
