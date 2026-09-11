"""CLI: ``argus decisions`` (generate, portfolio, show)."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from argus.cli.repo import repo_root
from argus.core.serialize import dumps_json, to_jsonable
from argus.decision.engine import generate_decisions
from argus.decision.evolution import build_decision_lineage_payload
from argus.decision.history.cli import run_decision_history_commands
from argus.decision.persistence import (
    load_latest_product_decisions,
    save_portfolio_report,
    save_product_decisions,
)
from argus.decision.portfolio import build_portfolio_from_inventory
from argus.decision_assessment.evaluate import evaluate_decision_context
from argus.decision_assessment.persistence import save_assessment
from argus.findings.experiment_surfaced import merged_findings_for_decisions
from argus.findings.persistence import load_latest_findings
from argus.products.inventory import build_inventory


def _products_dir(repo: Path, override: Path | None) -> Path | None:
    if override is None:
        return None
    return override.resolve()


def cmd_decisions_generate(repo: Path, args: Any) -> int:
    inv = build_inventory(repo, products_dir=_products_dir(repo, args.products_dir))
    if args.product_id:
        if args.product_id not in inv.valid:
            print(f"Unknown or invalid product: {args.product_id!r}", file=sys.stderr)
            return 1
        targets = [(args.product_id, inv.valid[args.product_id].node)]
    else:
        targets = [(pid, rec.node) for pid, rec in inv.valid.items()]

    all_out: dict[str, Any] = {}
    for pid, node in targets:
        bundle = load_latest_findings(repo, pid)
        if bundle is None:
            print(f"Skipping {pid}: no findings (run: argus findings generate {pid})", file=sys.stderr)
            continue
        findings = merged_findings_for_decisions(repo, pid)
        a, cands = generate_decisions(node, findings, repo_root=repo)
        ctx_obj = evaluate_decision_context(repo, pid)
        ctx_payload = ctx_obj.to_jsonable()
        if not args.no_save:
            save_assessment(repo, ctx_obj)
            lin = build_decision_lineage_payload(repo, pid, cands)
            save_product_decisions(
                repo,
                pid,
                a,
                cands,
                decision_context=ctx_payload,
                lineage_bundle_extras=lin["bundle_extras"],
                lineage_candidate_augmentations=lin["candidate_augmentations"],
            )
        all_out[pid] = {
            "lifecycle": {
                "stage": a.stage.value,
                "scores": a.as_dict(),
                "kill_candidate": a.kill_candidate,
                "reasoning": a.reasoning,
            },
            "candidates": [to_jsonable(c) for c in cands],
            "decision_context": ctx_payload,
        }

    if not all_out:
        print("No decisions generated (need findings).", file=sys.stderr)
        return 1

    if args.json:
        print(dumps_json(all_out))
    else:
        for pid, data in all_out.items():
            print(f"=== {pid} ===")
            lc = data["lifecycle"]
            print(f"lifecycle: {lc['stage']}  scores={lc['scores']}  kill_candidate={lc['kill_candidate']}")
            for c in data["candidates"][:8]:
                md = c.get("metadata") or {}
                fw = md.get("freshness_warnings") or []
                extra = ""
                if fw:
                    extra = "  [freshness] " + "; ".join(str(x) for x in fw[:2])
                print(
                    f"  [{md.get('intent', '?')}] "
                    f"score={c.get('priority_score')}  {c.get('summary', '')[:80]}{extra}"
                )
    return 0


def cmd_decisions_portfolio(repo: Path, args: Any) -> int:
    rows, per_product = build_portfolio_from_inventory(
        repo, products_dir=_products_dir(repo, args.products_dir)
    )
    if not rows:
        print("No portfolio rows (generate findings and decisions for products first).", file=sys.stderr)
        return 1
    if not args.no_save:
        save_portfolio_report(repo, rows, per_product)

    if args.json:
        print(
            dumps_json(
                {
                    "ranked": [
                        {
                            "rank": r.rank,
                            "product_id": r.product_id,
                            "lifecycle_stage": r.lifecycle_stage,
                            "top_intent": r.top_intent,
                            "priority_score": r.priority_score,
                            "summary": r.summary,
                            "kill_candidate": r.assessment.kill_candidate,
                            "scores": r.assessment.as_dict(),
                            "freshness_warnings": list(r.freshness_warnings),
                        }
                        for r in rows
                    ]
                }
            )
        )
    else:
        print("Ranked portfolio (top recommendation per product)")
        for r in rows:
            kc = " [kill_candidate]" if r.assessment.kill_candidate else ""
            print(
                f"{r.rank}. {r.product_id} ({r.lifecycle_stage}) -> {r.top_intent} "
                f"score={r.priority_score}{kc}"
            )
            print(f"   {r.summary}")
            for w in r.freshness_warnings:
                print(f"   [freshness] {w}")
    return 0


def cmd_decisions_show(repo: Path, args: Any) -> int:
    raw = load_latest_product_decisions(repo, args.product_id)
    if raw is None:
        print(
            f"No saved decisions for {args.product_id!r}. Run: argus decisions generate {args.product_id}",
            file=sys.stderr,
        )
        return 1
    if args.json:
        print(dumps_json(raw))
        return 0
    print(f"product_id: {raw.get('product_id')}")
    print(f"generated_at_utc: {raw.get('generated_at_utc')}")
    lc = raw.get("lifecycle") or {}
    print(f"lifecycle stage: {lc.get('stage')}  kill_candidate={lc.get('kill_candidate')}")
    print(f"scores: {lc.get('scores')}")
    for c in raw.get("candidates") or []:
        md = c.get("metadata") or {}
        print(f"- [{md.get('intent')}] score={c.get('priority_score')}  {c.get('summary')}")
        fw = md.get("freshness_warnings") or []
        for w in fw:
            print(f"    freshness: {w}")
        if md.get("stale_data_affected_confidence"):
            print("    stale_data_affected_confidence: true")
    return 0


def run_decisions_subcommand(args: Any) -> int:
    repo = repo_root()
    if args.decisions_command == "generate":
        return cmd_decisions_generate(repo, args)
    if args.decisions_command == "portfolio":
        return cmd_decisions_portfolio(repo, args)
    if args.decisions_command == "show":
        return cmd_decisions_show(repo, args)
    if args.decisions_command in ("history", "churn"):
        return run_decision_history_commands(repo, args)
    return 2
