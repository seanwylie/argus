"""
Debug report: which temporal rows drive worst freshness (incl. manifest gap rows).

Writes ``runs/debug/temporal_worst_freshness/latest.{json,md}``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from argus.core.serialize import dumps_json
from argus.temporal.persistence import load_latest_temporal_bundle, temporal_latest_path
from argus.temporal.recency import (
    signals_for_worst_freshness_aggregate,
    worst_freshness_bucket_from_signal_dicts,
    worst_freshness_status_from_signal_dicts,
)

TEMPORAL_WORST_FRESHNESS_DEBUG_SCHEMA = "argus.debug_temporal_worst_freshness.v1"


def temporal_worst_freshness_debug_dir(repo_root: Path) -> Path:
    return Path(repo_root).resolve() / "runs" / "debug" / "temporal_worst_freshness"


def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return raw if isinstance(raw, dict) else None


def _iso_now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _summarize_manifest_row(s: dict[str, Any]) -> dict[str, Any]:
    pl = s.get("payload") if isinstance(s.get("payload"), dict) else {}
    return {
        "signal_id": s.get("signal_id"),
        "signal_type": s.get("signal_type"),
        "freshness_status": s.get("freshness_status"),
        "freshness_bucket": s.get("freshness_bucket"),
        "observed_at": s.get("observed_at"),
        "freshness_age": s.get("freshness_age"),
        "manifest_path": pl.get("path"),
        "collection_status": pl.get("collection_status"),
        "manifest_signal_id": pl.get("manifest_signal_id"),
        "reason": pl.get("reason"),
    }


def _likely_cause(s: dict[str, Any]) -> str:
    src = str(s.get("source") or "").strip()
    if src == "manifest_declaration":
        pl = s.get("payload") if isinstance(s.get("payload"), dict) else {}
        st = str(pl.get("collection_status") or "")
        p = str(pl.get("path") or "")
        return (
            f"Manifest declaration gap ({st}): no collected SignalRecord matched declared path "
            f"{p!r}; synthetic row uses epoch observed_at — coverage gap, not runtime clock drift."
        )
    return "Collected signal; see freshness_age and payload for observability staleness."


def build_temporal_worst_freshness_report_payload(
    repo_root: Path,
    *,
    product_ids: list[str],
) -> dict[str, Any]:
    root = repo_root.resolve()
    evaluated_at = _iso_now()
    products: list[dict[str, Any]] = []

    for pid in product_ids:
        raw = load_latest_temporal_bundle(root, pid)
        path_rel = str(temporal_latest_path(root, pid).relative_to(root))
        if not raw:
            products.append(
                {
                    "product_id": pid,
                    "bundle_path": path_rel,
                    "bundle_present": False,
                    "note": "No temporal bundle on disk",
                }
            )
            continue
        sigs = [x for x in (raw.get("signals") or []) if isinstance(x, dict)]
        manifest_rows = [s for s in sigs if str(s.get("source") or "").strip() == "manifest_declaration"]
        degraded = [s for s in sigs if str(s.get("freshness_status") or "") in ("stale", "expired", "unknown")]
        agg_sigs = signals_for_worst_freshness_aggregate(sigs)
        worst_only_collected = worst_freshness_status_from_signal_dicts(agg_sigs)
        bucket_only_collected = worst_freshness_bucket_from_signal_dicts(agg_sigs)

        manifest_expired = [s for s in manifest_rows if str(s.get("freshness_status") or "") == "expired"]
        expired_all = [s for s in sigs if str(s.get("freshness_status") or "") == "expired"]
        products.append(
            {
                "product_id": pid,
                "bundle_path": path_rel,
                "bundle_present": True,
                "record_count": int(raw.get("record_count") or len(sigs)),
                "worst_freshness_status_in_bundle": raw.get("worst_freshness_status"),
                "worst_freshness_status_aggregate_collected_only": worst_only_collected,
                "worst_freshness_bucket_aggregate_collected_only": bucket_only_collected,
                "manifest_declaration_rows": [_summarize_manifest_row(s) for s in manifest_rows],
                "manifest_declaration_expired_count": len(manifest_expired),
                "degraded_row_count_all_sources": len(degraded),
                "expired_contributors": [
                    {
                        "signal_id": s.get("signal_id"),
                        "signal_type": s.get("signal_type"),
                        "source": s.get("source"),
                        "freshness_status": s.get("freshness_status"),
                        "observed_at": s.get("observed_at"),
                        "freshness_age": s.get("freshness_age"),
                        "manifest_path": (s.get("payload") or {}).get("path") if isinstance(s.get("payload"), dict) else None,
                        "likely_cause": _likely_cause(s),
                    }
                    for s in expired_all
                ],
                "recommended_next_actions": (
                    [
                        "Add/adjust manifest entries or signal adapters so declared filesystem paths emit "
                        "real collected rows, or trim declarations that are not meant to be monitored."
                    ]
                    if manifest_expired
                    else []
                ),
            }
        )

    notes = [
        "aggregate_collected_only excludes source=manifest_declaration (synthetic gap rows with epoch observed_at).",
        "Persisted bundle worst_freshness_status updates on next temporal refresh from signals.",
    ]

    return {
        "schema": TEMPORAL_WORST_FRESHNESS_DEBUG_SCHEMA,
        "evaluated_at_utc": evaluated_at,
        "inputs": {
            "product_ids": list(product_ids),
        },
        "products": products,
        "notes": notes,
    }


def render_temporal_worst_freshness_report_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Temporal worst-freshness debug",
        "",
        f"**Schema:** `{payload.get('schema')}`",
        f"**Evaluated (UTC):** {payload.get('evaluated_at_utc')}",
        "",
    ]
    for p in payload.get("products") or []:
        pid = p.get("product_id")
        lines.append(f"## `{pid}`")
        lines.append("")
        if not p.get("bundle_present"):
            lines.append(str(p.get("note") or "(missing bundle)"))
            lines.append("")
            continue
        lines.append(f"- Bundle: `{p.get('bundle_path')}`")
        lines.append(f"- Rows: **{p.get('record_count')}**")
        lines.append(f"- `worst_freshness_status` in file: `{p.get('worst_freshness_status_in_bundle')}`")
        lines.append(f"- Worst among collected signals only: `{p.get('worst_freshness_status_aggregate_collected_only')}`")
        lines.append(f"- Manifest declaration rows: **{len(p.get('manifest_declaration_rows') or [])}**")
        lines.append(f"- Manifest rows expired: **{p.get('manifest_declaration_expired_count')}**")
        lines.append("")
        lines.append("### Expired rows (all sources)")
        for row in p.get("expired_contributors") or []:
            lines.append(f"- `{row.get('signal_id')}` ({row.get('source')}) — {row.get('likely_cause')}")
        lines.append("")
    lines.append("## Notes")
    for n in payload.get("notes") or []:
        lines.append(f"- {n}")
    lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def write_temporal_worst_freshness_report_artifacts(repo_root: Path, payload: dict[str, Any]) -> tuple[Path, Path]:
    root = repo_root.resolve()
    d = temporal_worst_freshness_debug_dir(root)
    d.mkdir(parents=True, exist_ok=True)
    j = d / "latest.json"
    m = d / "latest.md"
    j.write_text(dumps_json(payload) + "\n", encoding="utf-8")
    m.write_text(render_temporal_worst_freshness_report_markdown(payload), encoding="utf-8")
    return j, m


def run_temporal_worst_freshness_report(
    repo_root: Path,
    *,
    product_ids: list[str],
    write_artifacts: bool = True,
) -> dict[str, Any]:
    payload = build_temporal_worst_freshness_report_payload(repo_root, product_ids=product_ids)
    if write_artifacts:
        write_temporal_worst_freshness_report_artifacts(repo_root, payload)
    return payload


__all__ = [
    "TEMPORAL_WORST_FRESHNESS_DEBUG_SCHEMA",
    "build_temporal_worst_freshness_report_payload",
    "render_temporal_worst_freshness_report_markdown",
    "run_temporal_worst_freshness_report",
    "temporal_worst_freshness_debug_dir",
    "write_temporal_worst_freshness_report_artifacts",
]
