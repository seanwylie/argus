"""CLI for ``argus decisions history`` and ``argus decisions churn``."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from argus.core.serialize import dumps_json, to_jsonable
from argus.decision.history.analyze import analyze_churn, churn_report_to_jsonable
from argus.decision.history.store import (
    load_all_products_with_history,
    load_product_decision_history,
)


def run_decision_history_commands(repo: Path, args: Any) -> int:
    sub = args.decisions_command
    pid = getattr(args, "product_id", None)

    if sub == "history":
        if pid:
            entries = load_product_decision_history(repo, pid)
            if args.json:
                print(
                    dumps_json(
                        {
                            "schema": "argus.decision_history.v1",
                            "product_id": pid,
                            "entries": [to_jsonable(e) for e in entries],
                        }
                    )
                )
            else:
                if not entries:
                    print(
                        f"No decision generations found for {pid!r} under "
                        f"runs/decisions/generations/ (expected *_{pid}.json).",
                        file=sys.stderr,
                    )
                    return 1
                print(f"Decision history for {pid} ({len(entries)} run(s), oldest first)\n")
                for e in entries:
                    print(f"--- {e.generated_at_utc}  ({e.source_path})")
                    print(f"  top: [{e.top_intent}] {e.top_recommended_action[:120]}")
                    print(
                        f"  score={e.priority_score}  conf={e.confidence}  "
                        f"vs_prev={e.compared_to_previous}  stage={e.lifecycle_stage}"
                    )
                    if e.alternatives:
                        print(f"  alternatives: {len(e.alternatives)} candidate(s)")
                print("")
            return 0

        # all products
        all_e = load_all_products_with_history(repo)
        if args.json:
            print(
                dumps_json(
                    {
                        "schema": "argus.decision_history.v1",
                        "products": {
                            p: [to_jsonable(e) for e in es] for p, es in sorted(all_e.items())
                        },
                    }
                )
            )
        else:
            if not all_e:
                print(
                    "No decision generation files under runs/decisions/generations/.",
                    file=sys.stderr,
                )
                return 1
            for p, es in sorted(all_e.items()):
                print(f"{p}: {len(es)} generation(s)")
        return 0

    if sub == "churn":
        if pid:
            entries = load_product_decision_history(repo, pid)
            report = analyze_churn(pid, entries, repo_root=repo)
            if args.json:
                print(dumps_json(churn_report_to_jsonable(report)))
            else:
                if report.run_count == 0:
                    print(
                        f"No decision generations for {pid!r} under runs/decisions/generations/.",
                        file=sys.stderr,
                    )
                    return 1
                print(f"Churn analysis: {pid}")
                print(f"  runs: {report.run_count}")
                print(f"  churn_score: {report.churn_score}  stability_score: {report.stability_score}")
                for line in report.summary_lines:
                    print(f"  · {line}")
            return 0

        all_e = load_all_products_with_history(repo)
        if not all_e:
            print("No decision history; run argus decisions generate first.", file=sys.stderr)
            return 1
        reports = []
        for p, es in sorted(all_e.items()):
            reports.append(analyze_churn(p, es, repo_root=repo))
        if args.json:
            print(
                dumps_json(
                    {
                        "schema": "argus.decision_churn_batch.v1",
                        "reports": [churn_report_to_jsonable(r) for r in reports],
                    }
                )
            )
        else:
            for r in reports:
                print(f"=== {r.product_id}  churn={r.churn_score}  stability={r.stability_score}")
                for line in r.summary_lines[:5]:
                    print(f"  · {line}")
                print("")
        return 0

    return 2
