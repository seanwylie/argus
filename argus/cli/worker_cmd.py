"""CLI handlers for ``argus worker``."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from argus.cli.repo import repo_root
from argus.core.serialize import dumps_json
from argus.products.instrumentation_work_orders import issue_instrumentation_work_orders
from argus.worker.execute_work_order import execute_work_order
from argus.worker.work_order_actions import apply_work_order_action
from argus.worker.work_orders import (
    find_work_order,
    load_latest_work_orders,
    work_orders_dir,
)


def _products_dir(repo: Path, override: Path | None) -> Path | None:
    if override is None:
        return None
    return override.resolve()


_WORK_ORDER_ACTION_COMMANDS: dict[str, str] = {
    "approve-work-order": "approve",
    "reject-work-order": "reject",
    "cancel-work-order": "cancel",
    "reopen-work-order": "reopen",
}


def run_worker_subcommand(args: Any) -> int:
    repo = repo_root()
    cmd = getattr(args, "worker_command", None)

    if cmd in _WORK_ORDER_ACTION_COMMANDS:
        woid = str(getattr(args, "work_order_id", "") or "").strip()
        report = apply_work_order_action(
            repo,
            work_order_id=woid,
            action_type=_WORK_ORDER_ACTION_COMMANDS[cmd],
            acted_by=str(getattr(args, "acted_by", "steward") or "steward").strip(),
            note=str(getattr(args, "note", "") or ""),
            save=not getattr(args, "no_save", False),
        )
        if getattr(args, "json", False):
            print(dumps_json(report))
        else:
            act = report.get("action") or {}
            if report.get("exit_code") == 0:
                print(
                    f"action_id={act.get('action_id')} "
                    f"work_order_id={act.get('work_order_id')} "
                    f"{act.get('previous_status')} -> {act.get('resulting_status')}"
                )
                sp = report.get("saved_path")
                if sp:
                    print(f"wrote {sp}")
                elif getattr(args, "no_save", False):
                    print("(no-save: action not written)")
            else:
                print(str(report.get("error") or "error"), file=sys.stderr)
        return int(report.get("exit_code") or 1)

    if cmd == "execute-work-order":
        pdir = _products_dir(repo, getattr(args, "products_dir", None))
        woid = getattr(args, "work_order_id", None)
        pid = getattr(args, "product_id", None)
        rt = getattr(args, "request_type", None)
        report = execute_work_order(
            repo,
            work_order_id=str(woid).strip() if woid else None,
            product_id=str(pid).strip() if pid else None,
            request_type=str(rt).strip() if rt else None,
            allow_pending_approval=bool(getattr(args, "allow_pending_approval", False)),
            save_artifacts=not getattr(args, "no_save", False),
            products_dir=pdir,
        )
        if getattr(args, "json", False):
            print(dumps_json(report))
        else:
            oc = report.get("outcome") or {}
            print(
                f"execution_id={oc.get('execution_id')} "
                f"work_order_id={oc.get('work_order_id')} "
                f"status={oc.get('execution_status')}"
            )
            if report.get("error"):
                print(str(report["error"]), file=sys.stderr)
            sp = report.get("saved_paths") or {}
            if sp.get("stamped_json"):
                print(f"wrote {sp.get('stamped_json')}")
        return int(report.get("exit_code") or 0)

    if cmd == "issue-instrumentation-work-orders":
        pdir = _products_dir(repo, getattr(args, "products_dir", None))
        pid = getattr(args, "product_id", None)
        pl = issue_instrumentation_work_orders(
            repo,
            write_artifacts=not getattr(args, "no_save", False),
            products_dir=pdir,
            product_id=str(pid).strip() if pid else None,
        )
        if getattr(args, "json", False):
            print(dumps_json(pl))
        else:
            n = len(pl.get("issued_work_orders") or [])
            print(f"Issued {n} work order(s).")
            for w in pl.get("issued_work_orders") or []:
                print(f"  {w.get('work_order_id')}\t{w.get('product_id')}\t{w.get('status')}")
            for sk in pl.get("skipped") or []:
                print(f"  (skip) {sk.get('product_id')}\t{sk.get('reason')}", file=sys.stderr)
        return 0

    if cmd != "show-work-orders":
        print("Unknown worker subcommand", file=sys.stderr)
        return 2

    pdir = _products_dir(repo, getattr(args, "products_dir", None))
    wo_id = getattr(args, "work_order_id", None)
    pid_filter = getattr(args, "product_id", None)

    if wo_id:
        pl = find_work_order(repo, str(wo_id).strip())
        payload: dict[str, Any] = {
            "schema": "argus.worker.show_work_orders.v1",
            "mode": "single",
            "work_order": pl,
            "found": pl is not None,
            "inputs": {
                "products_dir": str(pdir) if pdir is not None else None,
                "work_orders_dir": str(work_orders_dir(repo)),
            },
        }
        if args.json:
            print(dumps_json(payload))
        else:
            if pl:
                st = pl.get("status")
                st0 = pl.get("status_stamped")
                st_part = f"status={st}"
                if st0 is not None and st0 != st:
                    st_part = f"status={st} (stamped={st0})"
                print(
                    f"work_order_id={pl.get('work_order_id')} "
                    f"product_id={pl.get('product_id')} "
                    f"{st_part}"
                )
                print(pl.get("rationale") or "")
            else:
                print(f"No work order found for id {wo_id!r}", file=sys.stderr)
        return 0 if pl else 1

    rows = load_latest_work_orders(repo, product_id=pid_filter)
    payload = {
        "schema": "argus.worker.show_work_orders.v1",
        "mode": "latest",
        "work_orders": rows,
        "count": len(rows),
        "inputs": {
            "products_dir": str(pdir) if pdir is not None else None,
            "product_id_filter": pid_filter,
            "work_orders_dir": str(work_orders_dir(repo)),
        },
    }
    if args.json:
        print(dumps_json(payload))
    else:
        if not rows:
            print("No work orders under runs/worker/work_orders/latest/")
            return 0
        for r in rows:
            print(
                f"{r.get('work_order_id')}\t{r.get('product_id')}\t{r.get('request_type')}\t{r.get('status')}"
            )
    return 0
