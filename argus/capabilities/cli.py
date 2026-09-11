"""CLI: ``argus capabilities``."""

from __future__ import annotations

import sys
from typing import Any

from argus.capabilities.evaluate import (
    evaluate_capabilities,
    evaluation_to_json,
    write_evaluation_artifact,
)
from argus.capabilities.registry import current_capabilities
from argus.capabilities.requests.cli import run_capability_requests_command


def run_capabilities_command(args: Any) -> int:
    from argus.cli.repo import repo_root
    from argus.core.serialize import dumps_json, to_jsonable

    r = repo_root()
    sub = args.capabilities_command

    if sub == "request":
        return run_capability_requests_command(args)

    if sub == "list":
        caps = current_capabilities()
        if args.json:
            print(dumps_json([to_jsonable(c) for c in caps]))
        else:
            for c in caps:
                print(f"{c.id}")
                print(f"  {c.name} [{c.category}] {c.maturity}")
                print(f"  {c.coverage}")
                if c.known_gaps:
                    print(f"  gaps: {'; '.join(c.known_gaps)}")
                print("")
        return 0

    if sub == "evaluate":
        ev = evaluate_capabilities(r)
        path = write_evaluation_artifact(r, ev)
        if args.json:
            print(evaluation_to_json(ev))
        else:
            print(f"Capabilities: {len(ev.capabilities)}")
            print(f"Missing / inferred gaps: {len(ev.missing_capabilities)}")
            print(f"Suggested next build: {ev.suggested_next or '(none)'}")
            print(f"Gap findings emitted: {len(ev.gap_findings)}")
            print(f"Finding aggregate: {ev.finding_aggregate}")
            print(f"Wrote {path.relative_to(r)}", file=sys.stderr)
        return 0

    if sub == "resume":
        from argus.capabilities.resume import append_resume_event, resume_blocked_actions

        pid = getattr(args, "product_id", None)
        if pid is not None:
            pid = str(pid).strip() or None
        products_dir = getattr(args, "products_dir", None)
        pd = products_dir.resolve() if products_dir else None
        auto_queue = not bool(getattr(args, "no_queue", False))
        res = resume_blocked_actions(r, product_id=pid, products_dir=pd, auto_queue=auto_queue)
        append_resume_event(
            r,
            {
                "event": "capability_resume",
                "product_filter": pid,
                "cleared": res.cleared_pauses,
                "queued": res.queued,
            },
        )
        if args.json:
            print(
                dumps_json(
                    {
                        "cleared_pauses": res.cleared_pauses,
                        "revalidated": res.revalidated,
                        "queued": res.queued,
                        "skipped": res.skipped,
                    }
                )
            )
        else:
            print(f"Cleared pauses: {len(res.cleared_pauses)}")
            print(f"Queued executable: {len(res.queued)}")
            for q in res.queued:
                print(f"  + {q}")
            if res.skipped:
                print("Skipped:")
                for s in res.skipped[:12]:
                    print(f"  - {s}")
        return 0

    if sub == "gaps":
        ev = evaluate_capabilities(r)
        if args.json:
            print(
                dumps_json(
                    {
                        "missing_capabilities": [to_jsonable(m) for m in ev.missing_capabilities],
                        "suggested_next": ev.suggested_next,
                        "finding_aggregate": ev.finding_aggregate,
                    }
                )
            )
        else:
            print("Inferred / declared gaps (merged with finding heuristics):")
            for m in ev.missing_capabilities:
                print(f"  [{m.priority}] {m.id}")
                print(f"    {m.name}")
                print(f"    {m.reason}")
            print("")
            print(f"Suggested next capability to build: {ev.suggested_next or '(none)'}")
        return 0

    print("Unknown capabilities subcommand.", file=sys.stderr)
    return 2
