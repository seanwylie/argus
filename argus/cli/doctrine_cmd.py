"""CLI: ``argus doctrine`` (show parsed doctrine.yaml)."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from argus.cli.repo import repo_root
from argus.core.serialize import dumps_json
from argus.doctrine.load import load_doctrine_for_product
from argus.products.inventory import build_inventory


def _products_dir(repo: Path, override: Path | None) -> Path | None:
    if override is None:
        return None
    return override.resolve()


def run_doctrine_subcommand(args: Any) -> int:
    repo = repo_root()
    inv = build_inventory(repo, products_dir=_products_dir(repo, getattr(args, "products_dir", None)))
    if args.product_id not in inv.valid:
        print(f"Unknown or invalid product: {args.product_id!r}", file=sys.stderr)
        return 1
    node = inv.valid[args.product_id].node
    doc, err = load_doctrine_for_product(repo, args.product_id, product_root=node.product_root)
    if err:
        print(err, file=sys.stderr)
        return 1
    if doc is None:
        if args.json:
            print(dumps_json({"product_id": args.product_id, "doctrine": None}))
        else:
            print(f"No doctrine.yaml for product {args.product_id!r} (optional file).")
        return 0

    payload = {
        "product_id": args.product_id,
        "doctrine": {
            "schema": doc.schema_id,
            "summary": doc.summary,
            "principles": list(doc.principles),
            "constraints": {
                "max_monthly_cost_usd": doc.constraints.max_monthly_cost_usd,
                "require_human_review_when_kill_candidate": doc.constraints.require_human_review_when_kill_candidate,
            },
            "scoring": {
                "intent_priority_multiplier": doc.scoring.intent_priority_multiplier,
                "experiment_score_boost": doc.scoring.experiment_score_boost,
            },
        },
    }
    if args.json:
        print(dumps_json(payload))
    else:
        print(f"Doctrine for {args.product_id}")
        print("=" * 40)
        print(f"Summary: {doc.summary or '(none)'}")
        if doc.principles:
            print("Principles:")
            for p in doc.principles:
                print(f"  - {p}")
        print("Constraints:")
        c = doc.constraints
        print(f"  max_monthly_cost_usd: {c.max_monthly_cost_usd}")
        print(f"  require_human_review_when_kill_candidate: {c.require_human_review_when_kill_candidate}")
        print("Scoring nudges:")
        print(f"  experiment_score_boost: {doc.scoring.experiment_score_boost}")
        for k, v in sorted(doc.scoring.intent_priority_multiplier.items()):
            print(f"  intent {k!r} × {v}")
    return 0
