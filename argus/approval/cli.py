"""CLI: ``argus approval`` (list, request, approve, reject, evaluate)."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from argus.actions.validate import load_action_file
from argus.approval.rules import AutoApprovalEvaluation, evaluate_auto_approval
from argus.approval.store import approve, create_pending, list_records, reject
from argus.cli.repo import repo_root
from argus.core.serialize import dumps_json, to_jsonable


def _format_evaluation(ev: AutoApprovalEvaluation) -> str:
    lines = [
        f"auto_approve: {'yes' if ev.auto_approve else 'no'}",
        "",
        "reasons:",
    ]
    for r in ev.reasons:
        lines.append(f"  - {r}")
    lines.append("")
    return "\n".join(lines)


def run_approval_command(args: Any) -> int:
    r = repo_root()
    sub = getattr(args, "approval_command", None)

    if sub == "list":
        rows = list_records(r)
        if getattr(args, "status", None):
            filt = str(args.status).strip().lower()
            rows = [x for x in rows if x.status.value == filt]
        if args.json:
            print(dumps_json([to_jsonable(x) for x in rows]))
        else:
            if not rows:
                print("No approval records (runs/approval/records/).")
                return 0
            for rec in rows:
                print(
                    f"{rec.approval_id}\t{rec.status.value}\t{rec.action_id}\t{rec.product_id}\t"
                    f"{rec.created_at[:19] if rec.created_at else ''}"
                )
        return 0

    if sub == "request":
        aid = str(args.action_id).strip()
        pid = str(args.product_id).strip()
        if not aid or not pid:
            print("--action-id and --product are required", file=sys.stderr)
            return 1
        rec = create_pending(r, action_id=aid, product_id=pid, reason=str(args.reason or ""))
        if args.json:
            print(dumps_json(to_jsonable(rec)))
        else:
            print(f"Created pending approval {rec.approval_id}")
        return 0

    if sub == "approve":
        apid = str(args.approval_id).strip()
        try:
            rec = approve(r, apid, note=str(getattr(args, "note", "") or ""))
        except KeyError as e:
            print(str(e), file=sys.stderr)
            return 1
        except ValueError as e:
            print(str(e), file=sys.stderr)
            return 1
        if args.json:
            print(dumps_json(to_jsonable(rec)))
        else:
            print(f"Approved {rec.approval_id} for action {rec.action_id} / product {rec.product_id}")
        return 0

    if sub == "evaluate":
        path = Path(args.action_file).expanduser()
        if not path.is_file():
            print(f"Not a file: {path}", file=sys.stderr)
            return 1
        contract, err = load_action_file(path)
        if err is not None:
            print(err, file=sys.stderr)
            return 1
        assert contract is not None
        ev = evaluate_auto_approval(contract, repo_root=r)
        if args.json:
            print(dumps_json(to_jsonable(ev)))
        else:
            print(_format_evaluation(ev), end="")
        return 0

    if sub == "reject":
        apid = str(args.approval_id).strip()
        reason = str(getattr(args, "reason", "") or "").strip()
        if not reason:
            print("--reason is required for reject", file=sys.stderr)
            return 1
        try:
            rec = reject(r, apid, reason=reason)
        except KeyError as e:
            print(str(e), file=sys.stderr)
            return 1
        except ValueError as e:
            print(str(e), file=sys.stderr)
            return 1
        if args.json:
            print(dumps_json(to_jsonable(rec)))
        else:
            print(f"Rejected {rec.approval_id}")
        return 0

    print("Unknown approval subcommand.", file=sys.stderr)
    return 2
