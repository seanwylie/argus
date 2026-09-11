"""CLI: ``argus lifecycle`` (read-only lifecycle assessment)."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from argus.cli.repo import repo_root
from argus.core.serialize import dumps_json
from argus.decision.engine import generate_decisions
from argus.decision.persistence import load_latest_product_decisions
from argus.findings.persistence import load_latest_findings
from argus.lifecycle.kill import (
    compute_kill_score_for_product,
    compute_kill_scores_inventory,
    results_to_jsonable,
)
from argus.lifecycle.report import (
    build_kill_justification_report,
    render_kill_justification_markdown,
    report_to_json_string,
)
from argus.products.inventory import build_inventory


def _products_dir(repo: Path, override: Path | None) -> Path | None:
    if override is None:
        return None
    return override.resolve()


def cmd_lifecycle_show(repo: Path, args: Any) -> int:
    inv = build_inventory(repo, products_dir=_products_dir(repo, args.products_dir))
    pid = args.product_id
    if pid not in inv.valid:
        print(f"Unknown or invalid product: {pid!r}", file=sys.stderr)
        return 1

    node = inv.valid[pid].node
    raw = load_latest_product_decisions(repo, pid)
    if raw is not None and (raw.get("lifecycle") or raw.get("candidates") is not None):
        lc = raw.get("lifecycle") or {}
        if args.json:
            print(
                dumps_json(
                    {
                        "source": "saved_decisions",
                        "product_id": pid,
                        "lifecycle": lc,
                    }
                )
            )
        else:
            print(f"product_id: {pid}  (from saved decisions)")
            print(f"stage: {lc.get('stage')}")
            print(f"kill_candidate: {lc.get('kill_candidate')}")
            print(f"scores: {lc.get('scores')}")
            for rk, rv in (lc.get("reasoning") or {}).items():
                print(f"  reasoning: {rk}: {rv}")
        return 0

    bundle = load_latest_findings(repo, pid)
    if bundle is None:
        print(
            f"No findings for {pid!r}. Run: argus findings generate {pid} "
            "or argus portfolio refresh",
            file=sys.stderr,
        )
        return 1

    a, _cands = generate_decisions(node, bundle.findings, repo_root=repo)
    if args.json:
        print(
            dumps_json(
                {
                    "source": "computed_from_findings",
                    "product_id": pid,
                    "lifecycle": {
                        "stage": a.stage.value,
                        "scores": a.as_dict(),
                        "kill_candidate": a.kill_candidate,
                        "reasoning": dict(a.reasoning),
                    },
                }
            )
        )
    else:
        print(f"product_id: {pid}  (computed from latest findings, not saved)")
        print(f"stage: {a.stage.value}")
        print(f"kill_candidate: {a.kill_candidate}")
        print(f"scores: {a.as_dict()}")
        for k, v in a.reasoning.items():
            print(f"  reasoning: {k}: {v}")
    return 0


def cmd_lifecycle_kill_score(repo: Path, args: Any) -> int:
    if getattr(args, "product_id", None):
        inv = build_inventory(repo, products_dir=_products_dir(repo, args.products_dir))
        pid = args.product_id
        if pid not in inv.valid:
            print(f"Unknown or invalid product: {pid!r}", file=sys.stderr)
            return 1
        r = compute_kill_score_for_product(repo, pid, inv.valid[pid].node)
        if args.json:
            print(dumps_json(r.to_jsonable()))
        else:
            print(f"product_id: {r.product_id}")
            print(f"kill_score: {r.kill_score} (0–100, higher = stronger wind-down signal)")
            print(f"recommendation: {r.recommendation.value}")
            print("dimensions (0–1, higher = worse):")
            for k, v in sorted(r.dimensions.items()):
                print(f"  {k}: {v:.3f}")
            for k, v in sorted(r.notes.items()):
                if k == "trend_flags" and not v:
                    continue
                print(f"  note {k}: {v}")
        return 0

    results = compute_kill_scores_inventory(repo, products_dir=_products_dir(repo, args.products_dir))
    if args.json:
        print(dumps_json(results_to_jsonable(results)))
    else:
        print("kill_score  recommendation  product_id")
        for r in results:
            print(f"{r.kill_score:10}  {r.recommendation.value:12}  {r.product_id}")
    return 0


def cmd_lifecycle_report(repo: Path, args: Any) -> int:
    inv = build_inventory(repo, products_dir=_products_dir(repo, args.products_dir))
    pid = args.product_id
    if pid not in inv.valid:
        print(f"Unknown or invalid product: {pid!r}", file=sys.stderr)
        return 1

    report = build_kill_justification_report(repo, pid, inv.valid[pid].node)
    if getattr(args, "json", False):
        print(report_to_json_string(report))
        return 0

    md = render_kill_justification_markdown(report)
    out = getattr(args, "out", None)
    if out is not None:
        outp = out.resolve()
        if outp.is_dir():
            outp = outp / f"kill_justification_{pid}.md"
        outp.parent.mkdir(parents=True, exist_ok=True)
        outp.write_text(md, encoding="utf-8")
        print(f"Wrote {outp.relative_to(repo.resolve())}", file=sys.stderr)
    print(md)
    return 0


def run_lifecycle_subcommand(args: Any) -> int:
    repo = repo_root()
    if args.lifecycle_command == "show":
        return cmd_lifecycle_show(repo, args)
    if args.lifecycle_command == "kill-score":
        return cmd_lifecycle_kill_score(repo, args)
    if args.lifecycle_command == "report":
        return cmd_lifecycle_report(repo, args)
    return 2
