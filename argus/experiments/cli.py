"""CLI: ``argus experiments``."""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from typing import Any

from argus.capabilities.requests.integrations import record_experiment_evaluation_gap
from argus.cli.repo import repo_root
from argus.core.serialize import dumps_json, to_jsonable
from argus.experiments.evaluate import run_evaluations
from argus.experiments.execution_apply import apply_execution_outcomes
from argus.experiments.models import Experiment, ExperimentStatus, ExperimentType
from argus.experiments.prioritize import format_prioritization_text, prioritize_experiments
from argus.experiments.propose import format_proposals_text, propose_experiments
from argus.experiments.registry import can_transition
from argus.experiments.store import (
    list_experiments,
    load_experiment,
    new_experiment_id,
    save_experiment,
)
from argus.products.inventory import build_inventory


def run_experiments_command(args: Any) -> int:
    r = repo_root()
    sub = args.experiments_command

    if sub == "create":
        pid = args.product_id
        inv = build_inventory(r)
        if pid not in inv.valid:
            print(f"Unknown or invalid product: {pid!r}", file=sys.stderr)
            return 1
        from argus.autonomy.quotas import check_experiment_create_allowed, record_experiment_created

        q_ok, q_msg = check_experiment_create_allowed(r)
        if not q_ok:
            print(q_msg, file=sys.stderr)
            return 1
        now = datetime.now(timezone.utc).isoformat()
        exp = Experiment(
            id=new_experiment_id(),
            product_id=pid,
            hypothesis=str(args.hypothesis),
            type=ExperimentType(str(args.experiment_type)),
            description=str(args.description or ""),
            expected_outcome=str(args.expected_outcome or ""),
            success_metrics=list(getattr(args, "success_metric", None) or []),
            start_at=str(args.start_at or now),
            end_at=(str(args.end_at) if getattr(args, "end_at", None) else None),
            status=ExperimentStatus(str(args.status)),
            confidence=float(args.confidence),
            created_at=now,
        )
        path = save_experiment(r, exp)
        record_experiment_created(r)
        if args.json:
            print(dumps_json(to_jsonable(exp)))
        else:
            print(f"Created {exp.id} for product {pid}")
            print(f"  {path.relative_to(r)}")
        return 0

    if sub == "list":
        pid = getattr(args, "product_id", None)
        rows = list_experiments(r, product_id=pid)
        if args.json:
            print(dumps_json([to_jsonable(e) for e in rows]))
        else:
            for e in rows:
                print(f"{e.id}\t{e.product_id}\t{e.status.value}\t{e.type.value}")
                print(f"  {e.hypothesis[:100]}{'...' if len(e.hypothesis) > 100 else ''}")
        return 0

    if sub == "show":
        try:
            exp = load_experiment(r, args.experiment_id)
        except FileNotFoundError:
            print(f"Unknown experiment: {args.experiment_id!r}", file=sys.stderr)
            return 1
        if args.json:
            print(dumps_json(to_jsonable(exp)))
        else:
            print(f"id: {exp.id}")
            print(f"product_id: {exp.product_id}")
            print(f"type: {exp.type.value}")
            print(f"status: {exp.status.value}")
            print(f"hypothesis: {exp.hypothesis}")
            print(f"description: {exp.description}")
            print(f"expected_outcome: {exp.expected_outcome}")
            print(f"success_metrics: {exp.success_metrics}")
            print(f"start_at: {exp.start_at}")
            print(f"end_at: {exp.end_at}")
            print(f"confidence: {exp.confidence}")
            print(f"created_at: {exp.created_at}")
        return 0

    if sub == "update-status":
        try:
            exp = load_experiment(r, args.experiment_id)
        except FileNotFoundError:
            print(f"Unknown experiment: {args.experiment_id!r}", file=sys.stderr)
            return 1
        new_s = ExperimentStatus(str(args.status))
        if not can_transition(exp.status, new_s):
            print(
                f"Invalid transition {exp.status.value!r} -> {new_s.value!r}",
                file=sys.stderr,
            )
            return 2
        exp.status = new_s
        if new_s in (ExperimentStatus.COMPLETED, ExperimentStatus.FAILED) and exp.end_at is None:
            exp.end_at = datetime.now(timezone.utc).isoformat()
        save_experiment(r, exp)
        if args.json:
            print(dumps_json(to_jsonable(exp)))
        else:
            print(f"Updated {exp.id} -> {new_s.value}")
        return 0

    if sub == "evaluate":
        pid = args.evaluate_product_id
        apply_u = not args.no_apply
        results = run_evaluations(r, product_id=pid, apply_updates=apply_u)
        for ev in results:
            try:
                record_experiment_evaluation_gap(r, ev)
            except OSError:
                pass
        if args.json:
            print(dumps_json([to_jsonable(x) for x in results]))
        else:
            print(f"Evaluated {len(results)} experiment(s)")
            for ev in results:
                print(f"  {ev.experiment_id} {ev.verdict.value} score={ev.composite_score} — {ev.summary}")
            if not apply_u:
                print("(dry-run: no experiment files updated)", file=sys.stderr)
        return 0

    if sub == "propose":
        pid = getattr(args, "propose_product_id", None)
        inv = build_inventory(r)
        if pid is not None and pid not in inv.valid:
            print(f"Unknown or invalid product: {pid!r}", file=sys.stderr)
            return 1
        run = propose_experiments(r, product_id=pid, inventory=inv)
        if args.json:
            print(dumps_json(to_jsonable(run)))
        else:
            sys.stdout.write(format_proposals_text(run))
        return 0

    if sub == "apply-execution":
        rep = apply_execution_outcomes(r)
        if args.json:
            print(dumps_json(to_jsonable(rep)))
        else:
            print(
                f"Execution apply: {rep.files_applied} file(s) updated experiment(s), "
                f"{rep.skipped_no_link} skipped (no experiment link), "
                f"{rep.files_seen} execution file(s) on disk."
            )
            if rep.experiments_updated:
                print("  experiments: " + ", ".join(rep.experiments_updated))
        return 0

    if sub == "rank":
        pid = getattr(args, "rank_product_id", None)
        inv = build_inventory(r)
        if pid is not None and pid not in inv.valid:
            print(f"Unknown or invalid product: {pid!r}", file=sys.stderr)
            return 1
        run = prioritize_experiments(r, product_id=pid, inventory=inv)
        if args.json:
            print(dumps_json(to_jsonable(run)))
        else:
            sys.stdout.write(format_prioritization_text(run))
        return 0

    print("Unknown experiments subcommand.", file=sys.stderr)
    return 2
