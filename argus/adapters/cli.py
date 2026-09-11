"""CLI: ``argus adapters`` — list and run the adapter layer."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from argus.adapters.loader import instantiate_adapters
from argus.adapters.pipeline import run_adapter_layer
from argus.adapters.registry import registered_adapters, summary_table
from argus.cli.repo import repo_root
from argus.core.serialize import dumps_json, to_jsonable
from argus.products.inventory import build_inventory


def cmd_adapters_list(_repo: Path, args: Any) -> int:
    rows = summary_table()
    if args.json:
        print(dumps_json({"adapters": list(rows.values())}))
        return 0
    for aid in sorted(rows.keys()):
        r = rows[aid]
        print(f"{r['adapter_id']}\t{r['category']}\t{r['class']}")
    return 0


def cmd_adapters_run(repo: Path, args: Any) -> int:
    aid = str(args.adapter_id).strip()
    reg = registered_adapters()
    if aid not in reg and aid != "all":
        print(f"Unknown adapter id: {aid!r} (use: argus adapters list)", file=sys.stderr)
        return 1

    pdir = None
    if getattr(args, "products_dir", None) is not None:
        pdir = Path(args.products_dir).resolve()

    inv = build_inventory(repo, products_dir=pdir)
    pid = str(args.product_id).strip()
    if pid not in inv.valid:
        print(f"Unknown or invalid product: {pid!r}", file=sys.stderr)
        return 1
    node = inv.valid[pid].node

    if aid == "all":
        recs = run_adapter_layer(
            repo,
            node,
            adapters=instantiate_adapters(enabled_ids=None),
        )
    else:
        recs = run_adapter_layer(
            repo,
            node,
            adapters=instantiate_adapters(enabled_ids=[aid]),
        )

    if args.json:
        print(
            dumps_json(
                {
                    "product_id": pid,
                    "adapter_id": aid,
                    "record_count": len(recs),
                    "records": [to_jsonable(r) for r in recs],
                }
            )
        )
    else:
        print(f"Adapter layer: {aid}  product={pid}  records={len(recs)}")
        for r in recs:
            print(f"  [{r.signal_type.value}] {r.source}  {r.id}")
    return 0


def run_adapters_command(args: Any) -> int:
    repo = repo_root()
    sub = getattr(args, "adapters_command", None)
    if sub == "list":
        return cmd_adapters_list(repo, args)
    if sub == "run":
        return cmd_adapters_run(repo, args)
    print("Unknown adapters subcommand.", file=sys.stderr)
    return 2
