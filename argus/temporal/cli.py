"""CLI: ``argus temporal`` (snapshot ingest, adapters, collection recency views)."""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from typing import Any

from argus.cli.repo import repo_root
from argus.core.serialize import dumps_json
from argus.products.inventory import build_inventory
from argus.signals.adapters import default_builtin_adapters
from argus.signals.registry import AdapterRegistry
from argus.signals.snapshots.registry import snapshot_type_catalog
from argus.temporal.persistence import (
    load_latest_temporal_bundle,
    refresh_temporal_from_signals_latest,
    temporal_summary_path,
)


def _aggregate_signals(raw: dict[str, Any]) -> dict[str, Any]:
    signals = raw.get("signals") or []
    buckets: dict[str, int] = {}
    scores: list[float] = []
    status_counts: dict[str, int] = {}
    for s in signals:
        if not isinstance(s, dict):
            continue
        b = str(s.get("freshness_bucket") or "unknown")
        buckets[b] = buckets.get(b, 0) + 1
        st = s.get("freshness_status")
        if isinstance(st, str) and st.strip():
            status_counts[st] = status_counts.get(st, 0) + 1
        fs = s.get("freshness_score")
        if isinstance(fs, (int, float)):
            scores.append(float(fs))
    mean = sum(scores) / len(scores) if scores else None
    return {
        "record_count": len(signals),
        "bucket_counts": dict(sorted(buckets.items())),
        "freshness_status_counts": dict(sorted(status_counts.items())),
        "worst_freshness_status": raw.get("worst_freshness_status"),
        "mean_freshness_score": mean,
    }


def run_temporal_command(args: Any) -> int:
    repo = repo_root()
    cmd = getattr(args, "temporal_command", None)
    if cmd == "adapters":
        rows = [r for r in snapshot_type_catalog() if r.get("signal_type") == "temporal"]
        reg = AdapterRegistry(default_builtin_adapters())
        extra = reg.summary_table().get("temporal_snapshots", {})
        if getattr(args, "json", False):
            print(
                dumps_json(
                    {
                        "snapshot_formats": rows,
                        "signal_adapter": {
                            "adapter_id": "temporal_snapshots",
                            **extra,
                        },
                    }
                )
            )
            return 0
        print("Temporal snapshot file formats (filename substring → parser):")
        for r in rows:
            print(
                f"  {r.get('adapter_id')}\tcontains '{r.get('filename_contains')}'\t{r.get('formats')}"
            )
        print("\nSignal adapter (per-product collect when `temporal` enabled in product.yaml):")
        print(f"  temporal_snapshots\t{extra.get('signal_type', '')}\t{extra.get('capabilities', '')}")
        return 0

    if cmd == "show":
        pid = getattr(args, "product_id", None)
        raw = load_latest_temporal_bundle(repo, pid) if pid else None
        if raw is None:
            print(f"No temporal bundle for {pid!r} (runs/temporal/latest/).", file=sys.stderr)
            return 1
        if getattr(args, "json", False):
            print(dumps_json(raw))
            return 0
        print(dumps_json(raw))
        return 0

    if cmd == "freshness":
        pid = getattr(args, "product_id", None)
        raw = load_latest_temporal_bundle(repo, pid) if pid else None
        if raw is None:
            print(f"No temporal bundle for {pid!r}.", file=sys.stderr)
            return 1
        agg = _aggregate_signals(raw)
        agg["product_id"] = pid
        agg["collected_at_utc"] = raw.get("collected_at_utc")
        if getattr(args, "json", False):
            print(dumps_json(agg))
            return 0
        print(f"product_id: {pid}")
        print(f"collected_at_utc: {raw.get('collected_at_utc')}")
        print(f"record_count: {agg['record_count']}")
        print(f"mean_freshness_score: {agg['mean_freshness_score']}")
        print(f"worst_freshness_status: {agg.get('worst_freshness_status')}")
        print("bucket_counts:")
        for k, v in (agg.get("bucket_counts") or {}).items():
            print(f"  {k}: {v}")
        print("freshness_status_counts:")
        for k, v in (agg.get("freshness_status_counts") or {}).items():
            print(f"  {k}: {v}")
        return 0

    if cmd == "summary":
        pdir = getattr(args, "products_dir", None)
        inv = build_inventory(repo, products_dir=pdir)
        rows: list[dict[str, Any]] = []
        missing: list[str] = []
        for pid in sorted(inv.valid.keys()):
            raw = load_latest_temporal_bundle(repo, pid)
            if raw is None:
                missing.append(pid)
                rows.append(
                    {
                        "product_id": pid,
                        "status": "missing",
                    }
                )
                continue
            agg = _aggregate_signals(raw)
            rows.append(
                {
                    "product_id": pid,
                    "status": "ok",
                    "collected_at_utc": raw.get("collected_at_utc"),
                    **agg,
                }
            )
        # de-dupe: rows already have full agg; fix duplicate _aggregate_signals in append
        payload = {
            "schema": "argus.temporal_summary.v1",
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "products_dir": inv.products_dir,
            "products": rows,
            "missing_temporal_bundle": missing,
        }
        out_path = temporal_summary_path(repo)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(dumps_json(payload), encoding="utf-8")
        if getattr(args, "json", False):
            print(dumps_json(payload))
            return 0
        print(f"Wrote {out_path.relative_to(repo)}")
        print(f"products: {len(inv.valid)} valid, temporal bundles missing: {len(missing)}")
        return 0

    if cmd == "refresh":
        pid = getattr(args, "product_id", None)
        ok = refresh_temporal_from_signals_latest(repo, pid) if pid else False
        if not ok:
            err = {"ok": False, "error": "no_signals_bundle", "product_id": pid}
            if getattr(args, "json", False):
                print(dumps_json(err))
            else:
                print(
                    f"No runs/signals/latest/{pid}.json — run signal collection first.",
                    file=sys.stderr,
                )
            return 1
        raw = load_latest_temporal_bundle(repo, pid)
        if getattr(args, "json", False):
            print(dumps_json({"ok": True, "product_id": pid, "bundle": raw}))
            return 0
        print(f"Refreshed runs/temporal/latest/{pid}.json")
        return 0

    if cmd == "ingest":
        from argus.temporal.ingest import ingest_temporal_snapshots

        pid = getattr(args, "product_id", None)
        merge = not getattr(args, "no_merge", False)
        no_save = getattr(args, "no_save", False)
        res = ingest_temporal_snapshots(
            repo,
            product_id=pid,
            merge=merge,
            no_save=no_save,
        )
        if getattr(args, "json", False):
            print(dumps_json(res))
            return 0 if not res["errors"] else 1
        total = sum(res["saved_counts"].values())
        print(
            f"Ingested temporal snapshot signal(s): {total} record(s) across "
            f"{len(res['saved_counts'])} product bucket(s)"
        )
        for p, n in sorted(res["saved_counts"].items()):
            print(f"  {p}: {n}")
        if res["errors"]:
            for e in res["errors"]:
                print(e, file=sys.stderr)
            return 1
        return 0

    print("Unknown temporal command.", file=sys.stderr)
    return 2
