"""CLI: ``argus confidence`` — assess decision context for products."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from argus.core.serialize import dumps_json
from argus.decision_assessment.evaluate import evaluate_decision_context
from argus.decision_assessment.persistence import load_latest_assessment, save_assessment
from argus.products.inventory import build_inventory


def dispatch_confidence(args: argparse.Namespace, repo_root: Path) -> int:
    """Dispatch from argparse namespace (``argus confidence <sub>``)."""
    sub = args.confidence_command
    if sub == "assess":
        rest: list[str] = []
        if getattr(args, "product_id", None):
            rest.append(str(args.product_id))
        if getattr(args, "json", False):
            rest.append("--json")
        if getattr(args, "no_save", False):
            rest.append("--no-save")
        return run_confidence_command(repo_root, ["assess"] + rest)
    if sub == "summary":
        rest = ["--json"] if getattr(args, "json", False) else []
        return run_confidence_command(repo_root, ["summary"] + rest)
    if sub == "explain":
        rest = [str(args.product_id)]
        if getattr(args, "json", False):
            rest.append("--json")
        return run_confidence_command(repo_root, ["explain"] + rest)
    return 1


def run_confidence_command(repo_root: Path, argv: list[str]) -> int:
    if not argv:
        return _cmd_help()
    sub = argv[0]
    rest = argv[1:]
    if sub in ("-h", "--help", "help"):
        return _cmd_help()
    if sub == "assess":
        return _cmd_assess(repo_root, rest)
    if sub == "summary":
        return _cmd_summary(repo_root, rest)
    if sub == "explain":
        return _cmd_explain(repo_root, rest)
    print(f"Unknown confidence subcommand: {sub!r}", file=sys.stderr)
    return _cmd_help(1)


def _cmd_help(code: int = 0) -> int:
    print(
        "usage: argus confidence assess [product_id] [--json] [--no-save]\n"
        "       argus confidence summary [--json]\n"
        "       argus confidence explain <product_id> [--json]\n",
        end="",
    )
    return code


def _json_flag(rest: list[str]) -> tuple[list[str], bool]:
    want = "--json" in rest
    rest2 = [x for x in rest if x != "--json"]
    return rest2, want


def _cmd_assess(repo_root: Path, rest: list[str]) -> int:
    rest, as_json = _json_flag(rest)
    no_save = "--no-save" in rest
    rest = [x for x in rest if x != "--no-save"]
    pid = rest[0] if rest else None
    if pid and pid.startswith("-"):
        print(f"Unexpected argument: {pid!r}", file=sys.stderr)
        return 1

    inv = build_inventory(repo_root)
    targets = [pid] if pid else sorted(inv.valid.keys())

    out: dict[str, Any] = {}
    for p in targets:
        if p not in inv.valid:
            print(f"Unknown or invalid product: {p!r}", file=sys.stderr)
            return 1
        a = evaluate_decision_context(repo_root, p)
        if not no_save:
            save_assessment(repo_root, a)
        out[p] = a.to_jsonable()

    if as_json:
        print(dumps_json(out if len(out) > 1 else next(iter(out.values()))))
        return 0

    for p, payload in out.items():
        print(f"=== {p} ===")
        print(f"  confidence={payload['confidence_score']} ({payload['confidence_bucket']})")
        print(f"  uncertainty={payload['uncertainty_score']}  risk={payload['risk_score']}")
        print(f"  recurrence={payload['recurrence_risk_score']}  momentum={payload['momentum_score']}")
        print(f"  friction={payload['friction_score']}  escalation_pressure={payload['escalation_pressure']}")
        print(f"  escalation_recommendation={payload['escalation_recommendation']}")
        print(f"  exploratory={payload['exploratory_action_recommended']} — {payload['exploratory_action_reason'][:120]}")
    return 0


def _cmd_summary(repo_root: Path, rest: list[str]) -> int:
    rest, as_json = _json_flag(rest)
    inv = build_inventory(repo_root)
    rows: list[dict[str, Any]] = []
    for pid in sorted(inv.valid.keys()):
        a = load_latest_assessment(repo_root, pid)
        if a is None:
            a = evaluate_decision_context(repo_root, pid)
            save_assessment(repo_root, a)
        rows.append(
            {
                "product_id": pid,
                "confidence_score": a.confidence_score,
                "uncertainty_score": a.uncertainty_score,
                "risk_score": a.risk_score,
                "escalation_pressure": a.escalation_pressure,
                "escalation_recommendation": a.escalation_recommendation.value,
            }
        )
    payload = {"products": rows}
    if as_json:
        print(dumps_json(payload))
        return 0
    print("product_id | conf | unc | risk | esc_pressure | esc_rec")
    for r in rows:
        print(
            f"{r['product_id']:16} | {r['confidence_score']:.2f} | {r['uncertainty_score']:.2f} | "
            f"{r['risk_score']:.2f} | {r['escalation_pressure']:.2f} | {r['escalation_recommendation']}"
        )
    return 0


def _cmd_explain(repo_root: Path, rest: list[str]) -> int:
    rest, as_json = _json_flag(rest)
    if not rest:
        print("usage: argus confidence explain <product_id> [--json]", file=sys.stderr)
        return 1
    pid = rest[0]
    inv = build_inventory(repo_root)
    if pid not in inv.valid:
        print(f"Unknown or invalid product: {pid!r}", file=sys.stderr)
        return 1
    a = evaluate_decision_context(repo_root, pid)
    save_assessment(repo_root, a)
    if as_json:
        print(dumps_json(a.to_jsonable()))
        return 0
    print(a.rationale)
    print("\n-- confidence factors --")
    for f in a.confidence_factors[:12]:
        print(f"  [{f.factor_id}] {f.label} ({f.direction})")
    print("\n-- uncertainty factors --")
    for f in a.uncertainty_factors[:12]:
        print(f"  [{f.factor_id}] {f.label} ({f.direction})")
    print("\n-- risk factors --")
    for f in a.risk_factors[:8]:
        print(f"  [{f.factor_id}] {f.label} ({f.direction})")
    return 0
