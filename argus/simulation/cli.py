"""CLI: ``argus simulate`` — preview scenario outcomes (deterministic)."""

from __future__ import annotations

import sys
from typing import Any

from argus.cli.repo import repo_root
from argus.core.serialize import dumps_json, to_jsonable
from argus.experiments.store import load_experiment
from argus.products.inventory import build_inventory
from argus.simulation.simulate import run_simulation


def _products_dir(repo, override):
    if override is None:
        return None
    return override.resolve()


def run_simulate_command(args: Any) -> int:
    r = repo_root()
    product_id = getattr(args, "product_id", None)
    exp_id = getattr(args, "experiment_id", None)
    exp_obj = None

    if exp_id:
        try:
            exp_obj = load_experiment(r, exp_id)
        except FileNotFoundError:
            print(f"Experiment not found: {exp_id!r}", file=sys.stderr)
            print(f"Expected file: runs/experiments/{exp_id}.json", file=sys.stderr)
            return 1
        if product_id and product_id != exp_obj.product_id:
            print(
                f"Product id {product_id!r} does not match experiment product {exp_obj.product_id!r}.",
                file=sys.stderr,
            )
            return 1
        product_id = exp_obj.product_id
    elif not product_id:
        print("Provide a product id or --experiment <id>.", file=sys.stderr)
        return 1

    inv = build_inventory(r, products_dir=_products_dir(r, getattr(args, "products_dir", None)))
    if product_id not in inv.valid:
        print(f"Unknown or invalid product: {product_id!r}", file=sys.stderr)
        return 1

    node = inv.valid[product_id].node
    result = run_simulation(r, node, experiment=exp_obj)

    if args.json:
        print(dumps_json(to_jsonable(result)))
        return 0

    print(f"Simulation for product: {result.product_id}")
    if result.experiment_id:
        print(f"  Experiment: {result.experiment_id} ({result.experiment_type})")
        print(f"  Hypothesis: {result.experiment_hypothesis or '(none)'}")
    else:
        print("  Mode: generic (no experiment — use --experiment for a saved experiment)")
    print(f"  Stage: {result.product_stage}")
    print(f"  Monthly cost (declared): {result.monthly_cost_usd}")
    print(f"  Monthly cap: {result.monthly_cap_usd}")
    if result.baseline_metrics:
        print("  Baseline metrics (from signals):", result.baseline_metrics)
    else:
        print("  Baseline metrics: (no signal bundle — using structural heuristics only)")
    print()
    for sc in result.scenarios:
        print(f"--- {sc.label} [{sc.kind.value}] ---")
        print("  Metric changes:")
        for k, v in sc.metric_changes.items():
            print(f"    {k}: {v}")
        print("  Cost changes:")
        for k, v in sc.cost_changes.items():
            print(f"    {k}: {v}")
        print("  Risk notes:")
        for note in sc.risk_notes:
            print(f"    - {note}")
        print()
    print(f"Methodology: {result.methodology}")
    return 0
