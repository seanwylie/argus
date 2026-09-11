"""CLI handlers for ``argus self improve`` (findings / propose / plan)."""

from __future__ import annotations

import sys
from typing import Any

from argus.cli.repo import repo_root
from argus.core.serialize import dumps_json
from argus.self_improvement.findings import generate_self_findings
from argus.self_improvement.models import finding_to_jsonable, proposal_to_jsonable
from argus.self_improvement.planning import (
    build_self_improvement_plan,
    render_plan_markdown,
    write_plan_artifacts,
)
from argus.self_improvement.prioritize import rank_proposals, resolve_strategy_mode
from argus.self_improvement.propose import proposals_from_findings


def run_self_improve_command(args: Any) -> int:
    root = repo_root()
    cmd = getattr(args, "self_improve_command", None) or getattr(args, "improve_command", None)
    if not cmd:
        print("Missing self-improve subcommand.", file=sys.stderr)
        return 2

    if cmd == "findings":
        findings = generate_self_findings(root)
        if getattr(args, "json", False):
            print(dumps_json({"schema": "argus.self_improvement_findings.v1", "findings": [finding_to_jsonable(f) for f in findings]}))
            return 0
        print(f"Self-improvement findings ({len(findings)})")
        for f in findings:
            print(f"- [{f.kind.value}] {f.title}")
            print(f"  {f.summary[:500]}")
        return 0

    if cmd == "propose":
        findings = generate_self_findings(root)
        proposals = proposals_from_findings(findings)
        mode = resolve_strategy_mode(root)
        ranked = rank_proposals(proposals, strategy_mode=mode)
        if getattr(args, "json", False):
            print(
                dumps_json(
                    {
                        "schema": "argus.self_improvement_proposals.v1",
                        "strategy_mode": mode.value if mode else None,
                        "proposals": [proposal_to_jsonable(p) for p in proposals],
                        "ranked": [
                            {"proposal_id": r.proposal.id, "total_score": round(r.total_score, 4)}
                            for r in ranked
                        ],
                    }
                )
            )
            return 0
        print("Ranked self-improvement proposals (planning only)")
        for r in ranked[:20]:
            p = r.proposal
            print(f"{r.total_score:.3f}  {p.kind.value}  {p.title}")
            print(f"         {p.summary[:240]}")
        return 0

    if cmd == "plan":
        plan = build_self_improvement_plan(root)
        paths = write_plan_artifacts(root, plan)
        if getattr(args, "json", False):
            print(dumps_json(plan.to_jsonable()))
            return 0
        print(render_plan_markdown(plan))
        print("", file=sys.stderr)
        print(f"Wrote {paths.get('plan_latest.json')} and {paths.get('plan_latest.md')}", file=sys.stderr)
        return 0

    print(f"Unknown self-improve command: {cmd}", file=sys.stderr)
    return 2
