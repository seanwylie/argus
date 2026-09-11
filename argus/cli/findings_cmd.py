"""CLI: ``argus findings`` (generate, show, summary)."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from argus.cli.repo import repo_root
from argus.core.models.product import ProductNode
from argus.core.serialize import dumps_json, to_jsonable
from argus.findings.engine import generate_findings
from argus.findings.persistence import (
    load_all_latest_summaries,
    load_latest_findings,
    save_findings_bundle,
)
from argus.input.apply import apply_to_findings
from argus.products.inventory import build_inventory
from argus.signals.adapters import default_builtin_adapters
from argus.signals.persistence import load_latest_bundle
from argus.signals.registry import AdapterRegistry
from argus.signals.runner import collect_for_product


def _products_dir(repo: Path, override: Path | None) -> Path | None:
    if override is None:
        return None
    return override.resolve()


def _signals_for_product(
    repo: Path,
    product_id: str,
    node: ProductNode,
    *,
    fresh: bool,
) -> list:
    if not fresh:
        b = load_latest_bundle(repo, product_id)
        if b is not None:
            return b.records
    reg = AdapterRegistry(default_builtin_adapters())
    return collect_for_product(repo, node, reg)


def cmd_findings_generate(repo: Path, args: Any) -> int:
    inv = build_inventory(repo, products_dir=_products_dir(repo, args.products_dir))
    if args.product_id:
        if args.product_id not in inv.valid:
            print(f"Unknown or invalid product: {args.product_id!r}", file=sys.stderr)
            return 1
        nodes = [(args.product_id, inv.valid[args.product_id].node)]
    else:
        nodes = [(pid, rec.node) for pid, rec in inv.valid.items()]

    all_results: dict[str, list] = {}
    for pid, node in nodes:
        signals = _signals_for_product(repo, pid, node, fresh=args.fresh_signals)
        findings = generate_findings(node, signals, repo_root=repo)
        findings = apply_to_findings(repo, pid, findings)
        if not args.no_save:
            save_findings_bundle(repo, pid, findings)
        all_results[pid] = findings

    if args.json:
        print(
            dumps_json(
                {
                    pid: [to_jsonable(f) for f in fs]
                    for pid, fs in all_results.items()
                }
            )
        )
    else:
        for pid, fs in all_results.items():
            print(f"{pid}: {len(fs)} finding(s)")
            for f in fs:
                print(f"  [{f.kind.value}] {f.severity.value}  {f.title}")
    return 0


def cmd_findings_show(repo: Path, args: Any) -> int:
    b = load_latest_findings(repo, args.product_id)
    if b is None:
        print(
            f"No findings for {args.product_id!r}. Run: argus findings generate {args.product_id}",
            file=sys.stderr,
        )
        return 1
    if args.json:
        print(
            dumps_json(
                {
                    "product_id": b.product_id,
                    "generated_at_utc": b.generated_at_utc,
                    "findings": [to_jsonable(f) for f in b.findings],
                }
            )
        )
    else:
        print(f"product_id: {b.product_id}")
        print(f"generated_at_utc: {b.generated_at_utc}")
        for f in b.findings:
            print(f"- [{f.kind.value}] {f.title}")
            print(f"    {f.summary}")
    return 0


def cmd_findings_summary(repo: Path, args: Any) -> int:
    summaries = load_all_latest_summaries(repo)
    if args.json:
        print(dumps_json(summaries))
        return 0
    if not summaries:
        print("No findings bundles under runs/findings/latest/ (run findings generate first).")
        return 0
    total = 0
    for pid, row in sorted(summaries.items()):
        n = row.get("finding_count", 0)
        total += n
        kinds = row.get("by_kind", {})
        print(f"{pid}: {n} finding(s)  {kinds}")
    print(f"total: {total} across {len(summaries)} product(s)")
    return 0


def run_findings_subcommand(args: Any) -> int:
    repo = repo_root()
    if args.findings_command == "generate":
        return cmd_findings_generate(repo, args)
    if args.findings_command == "show":
        return cmd_findings_show(repo, args)
    if args.findings_command == "summary":
        return cmd_findings_summary(repo, args)
    return 2
