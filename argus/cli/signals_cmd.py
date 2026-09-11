"""CLI: ``argus signals`` (collect, show, adapters)."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from argus.adapters.loader import load_adapter_config
from argus.cli.repo import repo_root
from argus.core.serialize import dumps_json, to_jsonable
from argus.experiments.execution_apply import apply_execution_outcomes
from argus.products.external_bindings import parse_external_bindings
from argus.signals.adapters import default_builtin_adapters
from argus.signals.external_identity import (
    SignalIdentityVerificationError,
    verify_external_identities_for_collection,
)
from argus.signals.normalize import attach_canonical_to_records
from argus.signals.persistence import load_latest_bundle, save_collection
from argus.signals.reality import load_or_classify, write_signal_reality_report
from argus.signals.registry import AdapterRegistry
from argus.signals.review_ingest import (
    bounded_signal_review_for_json,
    load_signal_review_bundle,
    signal_review_operator_summary,
)
from argus.signals.runner import collect_for_product, collect_inventory, product_root_path
from argus.signals.snapshots.ingest import ingest_snapshots_for_product
from argus.signals.snapshots.registry import snapshot_type_catalog


def _registry() -> AdapterRegistry:
    return AdapterRegistry(default_builtin_adapters())


def signal_operator_context(repo: Path, product_id: str) -> dict[str, Any]:
    """
    Manifest + temporal sidecar facts for ``signals show`` and JSON (deterministic; no I/O beyond
    inventory + temporal bundle read).
    """
    from argus.products.inventory import build_inventory
    from argus.temporal.persistence import load_latest_temporal_bundle, temporal_latest_path

    root = repo.resolve()
    m_entries: int | None = None
    inv = build_inventory(root)
    if product_id in inv.valid:
        sm = inv.valid[product_id].node.signal_manifest
        m_entries = len(sm.signals) if sm is not None else 0

    tp = temporal_latest_path(root, product_id)
    temporal_present = tp.is_file()
    worst_status: str | None = None
    tb = load_latest_temporal_bundle(root, product_id) if temporal_present else None
    if isinstance(tb, dict):
        ws = tb.get("worst_freshness_status")
        worst_status = ws if isinstance(ws, str) and ws.strip() else None
    degraded = bool(worst_status and worst_status in ("stale", "expired"))

    out: dict[str, Any] = {
        "product_signal_manifest_entries": m_entries,
        "temporal_bundle_present": temporal_present,
        "worst_freshness_status": worst_status,
        "freshness_degraded": degraded,
    }
    out.update(signal_review_operator_summary(root, product_id))
    return out


def _products_dir(repo: Path, override: Path | None) -> Path | None:
    if override is None:
        return None
    return override.resolve()


def _merge_adapter_layer(repo: Path, args: Any) -> bool:
    if getattr(args, "merge_adapter_layer", False):
        return True
    cfg = load_adapter_config(repo)
    sc = cfg.get("signals_collect") or {}
    if isinstance(sc, dict) and sc.get("merge_adapter_layer"):
        return True
    return False


def cmd_signals_adapters(_repo: Path, args: Any) -> int:
    reg = _registry()
    if args.json:
        print(dumps_json(reg.summary_table()))
        return 0
    for aid, row in sorted(reg.summary_table().items()):
        cap = row.get("capabilities", "")
        print(f"{aid}\t{row['signal_type']}\t{cap}")
    return 0


def cmd_signals_collect(repo: Path, args: Any) -> int:
    reg = _registry()
    pdir = _products_dir(repo, args.products_dir)
    merge_layer = _merge_adapter_layer(repo, args)

    if args.product_id:
        from argus.products.inventory import build_inventory

        inv = build_inventory(repo, products_dir=pdir)
        if args.product_id not in inv.valid:
            print(
                f"Unknown or invalid product: {args.product_id!r}",
                file=sys.stderr,
            )
            return 1
        node = inv.valid[args.product_id].node
        eb = parse_external_bindings(node.raw_extensions)
        records = collect_for_product(
            repo,
            node,
            reg,
            merge_adapter_layer=merge_layer,
        )
        if not args.no_save:
            try:
                _, records = save_collection(
                    repo,
                    args.product_id,
                    records,
                    signal_manifest=node.signal_manifest,
                    product_root=product_root_path(repo, node),
                    external_bindings=eb,
                )
            except SignalIdentityVerificationError as ex:
                print(f"error: {ex}", file=sys.stderr)
                return 1
            apply_execution_outcomes(repo)
        else:
            records = attach_canonical_to_records(
                records,
                datetime.now(timezone.utc),
                signal_manifest=node.signal_manifest,
                product_root=product_root_path(repo, node),
            )
            _v, blocking = verify_external_identities_for_collection(eb, records)
            if blocking:
                print(
                    "error: external identity verification failed: " + "; ".join(blocking),
                    file=sys.stderr,
                )
                return 1
        if args.json:
            print(
                dumps_json(
                    {
                        "product_id": args.product_id,
                        "record_count": len(records),
                        "records": [to_jsonable(r) for r in records],
                    }
                )
            )
        else:
            print(f"Collected {len(records)} signal(s) for {args.product_id}")
            for r in records:
                print(f"  [{r.signal_type.value}] {r.source}  {r.id}")
            if not args.no_save:
                print(
                    f"Freshness sidecar: runs/temporal/latest/{args.product_id}.json "
                    f"(inspect: uv run argus temporal freshness {args.product_id})",
                    file=sys.stderr,
                )
        return 0

    inv, by_id = collect_inventory(
        repo,
        reg,
        products_dir=pdir,
        merge_adapter_layer=merge_layer,
    )
    total = 0
    for pid, records in list(by_id.items()):
        total += len(records)
        if not args.no_save:
            pnode = inv.valid[pid].node
            try:
                _, recs = save_collection(
                    repo,
                    pid,
                    records,
                    signal_manifest=pnode.signal_manifest,
                    product_root=product_root_path(repo, pnode),
                    external_bindings=parse_external_bindings(pnode.raw_extensions),
                )
            except SignalIdentityVerificationError as ex:
                print(f"error: {ex}", file=sys.stderr)
                return 1
            by_id[pid] = recs
        else:
            pnode = inv.valid[pid].node
            recs = attach_canonical_to_records(
                records,
                datetime.now(timezone.utc),
                signal_manifest=pnode.signal_manifest,
                product_root=product_root_path(repo, pnode),
            )
            _v, blocking = verify_external_identities_for_collection(
                parse_external_bindings(pnode.raw_extensions),
                recs,
            )
            if blocking:
                print(
                    "error: external identity verification failed: " + "; ".join(blocking),
                    file=sys.stderr,
                )
                return 1
            by_id[pid] = recs
    if not args.no_save:
        apply_execution_outcomes(repo)
    if args.json:
        print(
            dumps_json(
                {
                    "summary": {
                        "products": len(by_id),
                        "records": total,
                    },
                    "records_by_product": {
                        pid: [to_jsonable(r) for r in recs]
                        for pid, recs in by_id.items()
                    },
                }
            )
        )
    else:
        print(
            f"Collected {total} signal(s) across {len(by_id)} product(s) "
            f"(inventory: {inv.summary.valid_count} valid)"
        )
        for pid, recs in sorted(by_id.items()):
            print(f"  {pid}: {len(recs)} record(s)")
        if not args.no_save and by_id:
            print(
                "Freshness overview: uv run argus temporal summary",
                file=sys.stderr,
            )
    return 0


def cmd_signals_snapshot_types(_repo: Path, args: Any) -> int:
    rows = snapshot_type_catalog()
    if args.json:
        print(dumps_json(rows))
        return 0
    for r in rows:
        print(
            f"{r.get('adapter_id')}\t{r.get('filename_contains')}\t"
            f"{r.get('formats')}\t{r.get('signal_type')}"
        )
    return 0


def cmd_signals_ingest_snapshots(repo: Path, args: Any) -> int:
    from argus.products.inventory import build_inventory

    pdir = _products_dir(repo, args.products_dir)
    inv = build_inventory(repo, products_dir=pdir)
    include_fixtures = not args.no_fixtures
    merge = not args.no_merge

    def _run_one(pid: str, node: Any) -> tuple[str, int, str | None]:
        try:
            snap = ingest_snapshots_for_product(
                repo,
                node,
                include_fixtures=include_fixtures,
            )
        except Exception as e:
            return pid, 0, str(e)
        if merge:
            prev = load_latest_bundle(repo, pid)
            if prev is not None:
                snap = list(prev.records) + snap
        if not args.no_save:
            try:
                _, _ = save_collection(
                    repo,
                    pid,
                    snap,
                    signal_manifest=node.signal_manifest,
                    product_root=product_root_path(repo, node),
                    external_bindings=parse_external_bindings(node.raw_extensions),
                )
            except SignalIdentityVerificationError as ex:
                return pid, 0, str(ex)
        return pid, len(snap), None

    if args.product_id:
        if args.product_id not in inv.valid:
            print(f"Unknown or invalid product: {args.product_id!r}", file=sys.stderr)
            return 1
        node = inv.valid[args.product_id].node
        pid, n, err = _run_one(args.product_id, node)
        if err:
            print(f"{pid}: error: {err}", file=sys.stderr)
            return 1
        if args.json:
            print(
                dumps_json(
                    {
                        "product_id": pid,
                        "record_count": n,
                        "merged": merge,
                    }
                )
            )
        else:
            print(f"Ingested {n} business snapshot signal(s) for {pid}")
        return 0

    total = 0
    errors: list[str] = []
    results: list[dict[str, object]] = []
    for pid, rec in sorted(inv.valid.items()):
        _pid, n, err = _run_one(pid, rec.node)
        total += n
        if err:
            errors.append(f"{_pid}: {err}")
        results.append({"product_id": _pid, "record_count": n, "error": err})
    if args.json:
        print(
            dumps_json(
                {
                    "summary": {"products": len(inv.valid), "records": total},
                    "results": results,
                    "errors": errors,
                }
            )
        )
    else:
        print(
            f"Ingested {total} business snapshot signal(s) across {len(inv.valid)} product(s)"
        )
        if errors:
            for e in errors:
                print(e, file=sys.stderr)
            return 1
    return 0


def cmd_signals_reality(repo: Path, args: Any) -> int:
    from argus.products.inventory import build_inventory

    pdir = _products_dir(repo, args.products_dir)
    inv = build_inventory(repo, products_dir=pdir)
    if args.product_id not in inv.valid:
        print(f"Unknown or invalid product: {args.product_id!r}", file=sys.stderr)
        return 1
    node = inv.valid[args.product_id].node
    report = load_or_classify(
        repo,
        args.product_id,
        manifest=node.signal_manifest,
        product_root=product_root_path(repo, node),
    )
    if report is None:
        print(
            f"No persisted signals for {args.product_id!r} "
            f"(run: argus signals collect {args.product_id})",
            file=sys.stderr,
        )
        return 1
    if not args.no_save:
        write_signal_reality_report(repo, report)
    if args.json:
        print(dumps_json(report))
    else:
        s = report.get("summary") or {}
        print(f"product_id: {report.get('product_id')}")
        print(f"summary: {s}")
        for row in report.get("record_classifications") or []:
            print(f"  {row.get('signal_id')}\t{row.get('reality_status')}\t{row.get('reasons')}")
        for g in report.get("manifest_gaps") or []:
            print(f"  gap {g.get('manifest_signal_id')}\t{g.get('reality_status')}\t{g.get('reasons')}")
    return 0


def cmd_signals_show(repo: Path, args: Any) -> int:
    b = load_latest_bundle(repo, args.product_id)
    if b is None:
        print(
            f"No persisted signals for {args.product_id!r}. "
            f"Collect first: uv run argus signals collect {args.product_id}",
            file=sys.stderr,
        )
        return 1
    ctx = signal_operator_context(repo, args.product_id)
    srb = load_signal_review_bundle(repo, args.product_id)
    if args.json:
        print(
            dumps_json(
                {
                    "product_id": b.product_id,
                    "collected_at_utc": b.collected_at_utc,
                    "repo_root": b.repo_root,
                    "record_count": len(b.records),
                    "operator": ctx,
                    "signal_review_bundle": bounded_signal_review_for_json(srb),
                    "records": [to_jsonable(r) for r in b.records],
                }
            )
        )
    else:
        print(f"product_id: {b.product_id}")
        print(f"collected_at_utc: {b.collected_at_utc}")
        print(f"records: {len(b.records)}")
        if ctx.get("product_signal_manifest_entries") is not None:
            n = ctx["product_signal_manifest_entries"]
            print(
                f"product_signal_manifest: {n} entr{'y' if n == 1 else 'ies'} "
                f"(signals.yaml or product.yaml — see docs/model-contracts.md)"
            )
        if not ctx.get("temporal_bundle_present"):
            print(
                "temporal sidecar: missing — recompute: "
                f"uv run argus temporal refresh {args.product_id}",
            )
        else:
            ws = ctx.get("worst_freshness_status")
            line = "temporal sidecar: present (runs/temporal/latest/…)"
            if isinstance(ws, str) and ws:
                line += f"  worst_freshness_status={ws}"
            print(line)
            if ctx.get("freshness_degraded"):
                print(
                    "  degraded: stale/expired vs SLA or default tiers — "
                    f"detail: uv run argus temporal freshness {args.product_id}",
                )
        if ctx.get("signal_review_present"):
            rel = ctx.get("signal_review_path_repo_relative") or "runs/signals/review/…"
            match = ctx.get("signal_review_matches_deterministic_fingerprint")
            line = f"cursor signal review: present ({rel})"
            if match is False:
                line += " — stale vs latest collect; re-run: uv run argus signals cursor-ingest …"
            print(line)
        for r in b.records:
            print(f"  [{r.signal_type.value}] {r.source}  {r.id}")
            p = r.payload
            keys = list(p.keys())[:8]
            print(f"    payload keys: {keys}")
    return 0


def cmd_signals_cursor_prompt(repo: Path, args: Any) -> int:
    from argus.signals.review_prompt import build_signal_review_prompt

    try:
        text = build_signal_review_prompt(repo, args.product_id)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 1
    print(text)
    return 0


def cmd_signals_cursor_ingest(repo: Path, args: Any) -> int:
    from argus.signals.review_ingest import ingest_signal_cursor_review, load_ingest_json

    path = Path(args.file)
    if not path.is_file():
        print(f"Not a file: {path}", file=sys.stderr)
        return 1
    try:
        raw = load_ingest_json(path)
    except (OSError, ValueError, json.JSONDecodeError) as e:
        print(f"Invalid JSON: {e}", file=sys.stderr)
        return 1
    try:
        out = ingest_signal_cursor_review(
            repo,
            args.product_id,
            raw,
            overwrite=not args.no_overwrite,
        )
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 1
    if args.json:
        print(dumps_json(out))
    else:
        print(
            f"Ingested signal review for {args.product_id} "
            f"(deterministic fp {out.get('deterministic_signals_fingerprint', '')})"
        )
    return 0


def cmd_signals_prune_collections(repo: Path, args: Any) -> int:
    from argus.core.serialize import dumps_json
    from argus.runs_retention import prune_signal_collections

    summary = prune_signal_collections(
        repo,
        keep_per_product=args.keep,
        dry_run=bool(getattr(args, "dry_run", False)),
    )
    if args.json:
        print(dumps_json(summary))
        return 0
    if summary.get("skipped_reason"):
        print(summary["skipped_reason"])
        return 0
    n = summary.get("would_delete_files") if summary.get("dry_run") else summary.get("deleted_files")
    if not n:
        print("Nothing to prune.")
        return 0
    label = "Would delete" if summary.get("dry_run") else "Deleted"
    print(f"{label} {n} file(s) across {summary.get('products_affected', 0)} product id(s) (keep={summary.get('keep_per_product')}).")
    for rel in summary.get("paths") or []:
        print(f"  - {rel}")
    return 0


def run_signals_subcommand(args: Any) -> int:
    repo = repo_root()
    if args.signals_command == "adapters":
        return cmd_signals_adapters(repo, args)
    if args.signals_command == "snapshot-types":
        return cmd_signals_snapshot_types(repo, args)
    if args.signals_command == "ingest-snapshots":
        return cmd_signals_ingest_snapshots(repo, args)
    if args.signals_command == "collect":
        return cmd_signals_collect(repo, args)
    if args.signals_command == "prune-collections":
        return cmd_signals_prune_collections(repo, args)
    if args.signals_command == "show":
        return cmd_signals_show(repo, args)
    if args.signals_command == "reality":
        return cmd_signals_reality(repo, args)
    if args.signals_command == "cursor-prompt":
        return cmd_signals_cursor_prompt(repo, args)
    if args.signals_command == "cursor-ingest":
        return cmd_signals_cursor_ingest(repo, args)
    return 2
