"""CLI: ``argus products`` (discovery, validation, show)."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from argus.cli.repo import repo_root
from argus.core.serialize import dumps_json, to_jsonable
from argus.products.inventory import build_inventory, inventory_to_jsonable
from argus.products.reality_bootstrap import write_reality_bootstrap
from argus.products.reporting import format_inventory_text, format_product_summary
from argus.products.scaffold import create_product_scaffold
from argus.products.signal_instrumentation import run_product_signal_instrumentation
from argus.project_permissions.gate import phase1_summary_for_product


def _products_dir(repo: Path, override: Path | None) -> Path | None:
    if override is None:
        return None
    return override.resolve()


def cmd_products_list(repo: Path, args: Any) -> int:
    inv = build_inventory(repo, products_dir=_products_dir(repo, args.products_dir))
    if args.json:
        payload: dict[str, Any] = {
            "valid": {
                pid: {
                    "id": rec.node.id,
                    "name": rec.node.name,
                    "type": rec.node.type_info.type if rec.node.type_info else None,
                    "status": rec.node.type_info.status if rec.node.type_info else None,
                    "state": rec.node.type_info.state if rec.node.type_info else None,
                    "lifecycle_stage": rec.node.lifecycle.stage.value,
                    "warnings": rec.warnings,
                }
                for pid, rec in inv.valid.items()
            },
            "invalid": [
                {
                    "product_id": x.product_id,
                    "product_root": x.product_root,
                    "config_path": x.config_path,
                    "errors": x.errors,
                    "warnings": x.warnings,
                }
                for x in inv.invalid
            ],
            "summary": {
                "total_candidates": inv.summary.total_candidates,
                "valid_count": inv.summary.valid_count,
                "invalid_count": inv.summary.invalid_count,
                "by_lifecycle_stage": inv.summary.by_lifecycle_stage,
                "by_status": inv.summary.by_status,
            },
        }
        print(dumps_json(payload))
    else:
        rows = ["id\tlifecycle\tstatus\ttype"]
        for pid in sorted(inv.valid.keys()):
            n = inv.valid[pid].node
            lt = n.type_info.type if n.type_info else "?"
            st = n.type_info.status if n.type_info else "?"
            rows.append(f"{pid}\t{n.lifecycle.stage.value}\t{st}\t{lt}")
        for x in inv.invalid:
            label = x.product_id or "(unknown id)"
            rows.append(f"{label}\t-\t-\tINVALID ({x.config_path})")
        print("\n".join(rows))
        if inv.summary.total_candidates == 0:
            print(
                f"# No product.yaml manifests found under {inv.products_dir}.\n"
                "# Next: add products/<id>/product.yaml, or run: argus products create --name <slug>",
                file=sys.stderr,
            )
    if args.write:
        path = Path(args.write)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(dumps_json(inventory_to_jsonable(inv)), encoding="utf-8")
        if not args.json:
            print(f"Wrote inventory snapshot to {path}", file=sys.stderr)
    return 0


def cmd_products_validate(repo: Path, args: Any) -> int:
    inv = build_inventory(repo, products_dir=_products_dir(repo, args.products_dir))
    if args.json:
        print(dumps_json(inventory_to_jsonable(inv)))
    else:
        sys.stdout.write(format_inventory_text(inv))
    if args.write:
        path = Path(args.write)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(dumps_json(inventory_to_jsonable(inv)), encoding="utf-8")
        if not args.json:
            print(f"Wrote inventory snapshot to {path}", file=sys.stderr)
    return 1 if inv.invalid else 0


def cmd_products_create(repo: Path, args: Any) -> int:
    code, msg, git_info = create_product_scaffold(
        repo,
        args.name,
        template_type=args.template_type,
        products_dir=_products_dir(repo, args.products_dir),
        force=args.force,
        init_git=not bool(getattr(args, "no_git", False)),
    )
    if code == 0:
        if args.json:
            print(
                dumps_json(
                    {
                        "ok": True,
                        "product_root": msg,
                        "template_type": args.template_type,
                        "name": args.name,
                        "git": git_info,
                    }
                )
            )
        else:
            print(f"Created product scaffold at {msg}")
            if git_info.get("git_init_ok") and git_info.get("initial_commit_ok"):
                print("Initialized local git repo with initial commit (no remote).")
            elif git_info.get("note") and not git_info.get("initial_commit_ok"):
                print(f"Git: {git_info.get('note')}", file=sys.stderr)
        return 0
    if args.json:
        print(dumps_json({"ok": False, "error": msg}))
    else:
        print(msg, file=sys.stderr)
    return code


def cmd_products_instrument_signals(repo: Path, args: Any) -> int:
    payload = run_product_signal_instrumentation(
        repo,
        product_id=args.product_id,
        write_artifacts=not args.no_save,
        products_dir=_products_dir(repo, args.products_dir),
    )
    if args.json:
        print(dumps_json(payload))
    else:
        status = payload.get("instrumentation_status", "?")
        ok = payload.get("ok")
        print(f"product={args.product_id!r} ok={ok} instrumentation_status={status!r}")
        if payload.get("errors"):
            for e in payload["errors"]:
                print(f"  error: {e}", file=sys.stderr)
        print(payload.get("recommended_next_step", ""))
        if not args.no_save and ok:
            print(
                f"Wrote runs/products/signal_instrumentation/latest/{args.product_id}.json",
                file=sys.stderr,
            )
    return 0 if ok else 1


def cmd_products_bootstrap(repo: Path, args: Any) -> int:
    code, msg, detail = write_reality_bootstrap(
        repo,
        args.product_id,
        products_dir=_products_dir(repo, args.products_dir),
        force=args.force,
    )
    if args.json:
        print(dumps_json({"ok": code == 0, "message": msg, **detail}))
    else:
        print(msg)
        for c in detail.get("created") or []:
            print(f"  + {c}")
        for s in detail.get("skipped") or []:
            print(f"  (skip) {s}")
    return code


def cmd_products_show(repo: Path, args: Any) -> int:
    inv = build_inventory(repo, products_dir=_products_dir(repo, args.products_dir))
    pid = args.product_id
    if pid in inv.valid:
        rec = inv.valid[pid]
        if args.json:
            print(
                dumps_json(
                    {
                        "product": to_jsonable(rec.node),
                        "warnings": rec.warnings,
                    }
                )
            )
        else:
            print(format_product_summary(rec.node))
            if rec.warnings:
                print("warnings:")
                for w in rec.warnings:
                    print(f"  - {w}")
        return 0

    for x in inv.invalid:
        if x.product_id == pid:
            if args.json:
                print(
                    dumps_json(
                        {
                            "product_id": x.product_id,
                            "product_root": x.product_root,
                            "config_path": x.config_path,
                            "errors": x.errors,
                            "warnings": x.warnings,
                        }
                    )
                )
            else:
                print(f"Product {pid!r} is invalid.", file=sys.stderr)
                for e in x.errors:
                    print(f"  error: {e}", file=sys.stderr)
            return 1

    print(f"Unknown product id: {pid!r}", file=sys.stderr)
    return 2


def cmd_products_permissions_respond(repo: Path, args: Any) -> int:
    from argus.project_permissions.approvals import record_operator_response

    try:
        out = record_operator_response(
            repo,
            product_id=str(args.product_id),
            phase1_policy_field=str(args.phase1_field),
            response=str(args.response_kind),  # type: ignore[arg-type]
            orchestration_action_id=getattr(args, "orchestration_action_id", None),
            note=getattr(args, "note", None),
        )
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 2
    if args.json:
        print(dumps_json(to_jsonable(out)))
        return 0
    print(dumps_json(to_jsonable(out)))
    return 0


def cmd_products_permissions_show(repo: Path, args: Any) -> int:
    payload = phase1_summary_for_product(
        repo,
        args.product_id,
        products_dir=_products_dir(repo, args.products_dir),
    )
    if payload.get("policy_load_error"):
        if args.json:
            print(dumps_json(to_jsonable(payload)))
        else:
            err = str(payload.get("policy_load_error") or "").strip()
            print(err, file=sys.stderr)
            pp = payload.get("policy_path")
            if pp:
                print(f"Policy file: {pp}", file=sys.stderr)
        return 1
    pol = payload.get("policy") or {}
    perms = pol.get("permissions") or {}
    if args.json:
        print(dumps_json(to_jsonable(payload)))
        return 0
    print(f"Product `{args.product_id}` — Phase 1 project permissions")
    path = pol.get("policy_path") or f"products/{args.product_id}/argus.policy.yaml"
    print(f"Policy file: {path}")
    if pol.get("load_warnings"):
        for w in pol["load_warnings"]:
            print(f"  note: {w}", file=sys.stderr)
    print("Stance:")
    for k in sorted(perms.keys()):
        print(f"  {k}: {perms[k]}")
    mism = payload.get("policy_environment_mismatches") or []
    if mism:
        print("Policy allows but environment lacks support:")
        for row in mism:
            print(f"  - {row.get('permission_key')}: {row.get('reason')}")
    else:
        print("(No policy-vs-environment mismatches on recorded checks.)")
    print("Edit the YAML file to revise; Argus reads it on each run (no cache).")
    return 0


def cmd_products_propose_creation(repo: Path, args: Any) -> int:
    from argus.products.creation import (
        render_creation_proposals_markdown,
        run_creation_proposals,
    )

    payload = run_creation_proposals(repo, write_artifacts=not args.no_save)
    if args.json:
        print(dumps_json(payload))
    else:
        sys.stdout.write(render_creation_proposals_markdown(payload))
    return 0


def run_products_subcommand(args: Any) -> int:
    repo = repo_root()
    if args.products_command == "list":
        return cmd_products_list(repo, args)
    if args.products_command == "validate":
        return cmd_products_validate(repo, args)
    if args.products_command == "create":
        return cmd_products_create(repo, args)
    if args.products_command == "bootstrap":
        return cmd_products_bootstrap(repo, args)
    if args.products_command == "instrument-signals":
        return cmd_products_instrument_signals(repo, args)
    if args.products_command == "show":
        return cmd_products_show(repo, args)
    if args.products_command == "permissions":
        if args.permissions_command == "show":
            return cmd_products_permissions_show(repo, args)
        if args.permissions_command == "respond":
            return cmd_products_permissions_respond(repo, args)
    if args.products_command == "propose-creation":
        return cmd_products_propose_creation(repo, args)
    if args.products_command == "scaffold-creation":
        return cmd_products_scaffold_creation(repo, args)
    if args.products_command == "bootstrap-creation":
        return cmd_products_bootstrap_creation(repo, args)
    if args.products_command == "propose-deprecation":
        return cmd_products_propose_deprecation(repo, args)
    if args.products_command == "plan-deprecation":
        return cmd_products_plan_deprecation(repo, args)
    if args.products_command == "promote-creation":
        return cmd_products_promote_creation(repo, args)
    if args.products_command == "promote-bootstrap":
        return cmd_products_promote_bootstrap(repo, args)
    if args.products_command == "promote-deprecation":
        return cmd_products_promote_deprecation(repo, args)
    return 2


def cmd_products_scaffold_creation(repo: Path, args: Any) -> int:
    from argus.products.creation_bootstrap import run_product_creation_bootstrap
    from argus.products.creation_scaffold import (
        render_product_creation_scaffold_markdown,
        run_product_creation_scaffold,
    )

    payload = run_product_creation_scaffold(
        repo,
        proposal_id=str(args.creation_proposal_id),
        product_id=getattr(args, "creation_product_id", None),
        dry_run=bool(args.dry_run),
        write_artifacts=not bool(args.no_save),
        init_git=not bool(getattr(args, "no_git", False)),
    )
    if args.json:
        print(dumps_json(payload))
        if not payload.get("ok"):
            return 1
        code = 0
    else:
        if not args.no_save and payload.get("schema"):
            d = repo / "runs" / "products" / "creation_scaffold"
            print(f"Wrote {d / 'latest.json'}")
            print(f"Wrote {d / 'latest.md'}")
            print()
        if not payload.get("ok"):
            print(payload.get("error") or "scaffold failed", file=sys.stderr)
            return 1
        print(render_product_creation_scaffold_markdown(payload))
        code = 0

    bootstrap = bool(getattr(args, "bootstrap", False)) or bool(
        getattr(args, "bootstrap_minimal", False)
    )
    if bootstrap and payload.get("ok") and not payload.get("dry_run"):
        minimal = bool(getattr(args, "bootstrap_minimal", False))
        pdir = _products_dir(repo, getattr(args, "products_dir", None))
        boot = run_product_creation_bootstrap(
            repo,
            product_id=str(payload.get("product_id") or ""),
            minimal=minimal,
            dry_run=False,
            write_artifacts=not bool(args.no_save),
            products_dir=pdir,
        )
        if not args.json:
            bd = repo / "runs" / "products" / "creation_bootstrap"
            if boot.get("ok") and not args.no_save:
                print(f"Creation bootstrap: {bd / 'latest.json'}")
            elif not boot.get("ok"):
                print(
                    boot.get("error") or "creation bootstrap failed",
                    file=sys.stderr,
                )
        if not boot.get("ok"):
            return 1
    return code


def cmd_products_plan_deprecation(repo: Path, args: Any) -> int:
    from argus.products.deprecation_plan import (
        render_deprecation_plan_markdown,
        run_deprecation_plan,
    )

    payload = run_deprecation_plan(
        repo,
        proposal_id=str(args.deprecation_plan_proposal_id),
        write_artifacts=not bool(args.no_save),
        products_dir=_products_dir(repo, getattr(args, "products_dir", None)),
    )
    if args.json:
        print(dumps_json(payload))
        return 0 if payload.get("ok") else 1
    if not payload.get("ok"):
        print(payload.get("error") or "plan failed", file=sys.stderr)
        return 1
    if not args.no_save:
        d = repo / "runs" / "products" / "deprecation_plan"
        print(f"Wrote {d / 'latest.json'}")
        print(f"Wrote {d / 'latest.md'}")
        print()
    sys.stdout.write(render_deprecation_plan_markdown(payload))
    return 0


def cmd_products_propose_deprecation(repo: Path, args: Any) -> int:
    from argus.products.deprecation import (
        render_deprecation_proposals_markdown,
        run_deprecation_proposals,
    )

    payload = run_deprecation_proposals(
        repo,
        write_artifacts=not bool(args.no_save),
        products_dir=_products_dir(repo, getattr(args, "products_dir", None)),
    )
    if args.json:
        print(dumps_json(payload))
        return 0
    if not args.no_save:
        d = repo / "runs" / "products" / "deprecation"
        print(f"Wrote {d / 'latest.json'}")
        print(f"Wrote {d / 'latest.md'}")
        print()
    sys.stdout.write(render_deprecation_proposals_markdown(payload))
    return 0


def cmd_products_promote_creation(repo: Path, args: Any) -> int:
    from argus.products.promotion import render_lifecycle_promotion_markdown, run_promote_creation

    pdir = _products_dir(repo, getattr(args, "products_dir", None))
    ns = bool(args.no_save)
    pl = run_promote_creation(
        repo,
        proposal_id=str(args.proposal_id),
        product_id=getattr(args, "product_id", None),
        bootstrap=bool(args.bootstrap),
        bootstrap_minimal=bool(args.bootstrap_minimal),
        dry_run=bool(args.dry_run),
        write_promotion_artifact=not ns,
        write_stage_artifacts=not ns,
        products_dir=pdir,
    )
    if args.json:
        print(dumps_json(pl))
        return 0 if pl.get("result_status") == "success" else 1
    if not ns:
        d = repo / "runs" / "products" / "promotion_actions"
        print(f"Wrote {d / 'latest.json'}")
        print(f"Wrote {d / 'latest.md'}")
        print()
    print(render_lifecycle_promotion_markdown(pl))
    return 0 if pl.get("result_status") == "success" else 1


def cmd_products_promote_bootstrap(repo: Path, args: Any) -> int:
    from argus.products.promotion import render_lifecycle_promotion_markdown, run_promote_bootstrap

    pdir = _products_dir(repo, getattr(args, "products_dir", None))
    ns = bool(args.no_save)
    pl = run_promote_bootstrap(
        repo,
        product_id=str(args.product_id),
        minimal=bool(args.minimal),
        dry_run=bool(args.dry_run),
        write_promotion_artifact=not ns,
        write_stage_artifacts=not ns,
        products_dir=pdir,
    )
    if args.json:
        print(dumps_json(pl))
        return 0 if pl.get("result_status") == "success" else 1
    if not ns:
        d = repo / "runs" / "products" / "promotion_actions"
        print(f"Wrote {d / 'latest.json'}")
        print(f"Wrote {d / 'latest.md'}")
        print()
    print(render_lifecycle_promotion_markdown(pl))
    return 0 if pl.get("result_status") == "success" else 1


def cmd_products_promote_deprecation(repo: Path, args: Any) -> int:
    from argus.products.promotion import (
        render_lifecycle_promotion_markdown,
        run_promote_deprecation,
    )

    pdir = _products_dir(repo, getattr(args, "products_dir", None))
    ns = bool(args.no_save)
    pl = run_promote_deprecation(
        repo,
        proposal_id=str(args.proposal_id),
        dry_run=bool(args.dry_run),
        write_promotion_artifact=not ns,
        write_stage_artifacts=not ns and not bool(args.dry_run),
        products_dir=pdir,
    )
    if args.json:
        print(dumps_json(pl))
        return 0 if pl.get("result_status") == "success" else 1
    if not ns:
        d = repo / "runs" / "products" / "promotion_actions"
        print(f"Wrote {d / 'latest.json'}")
        print(f"Wrote {d / 'latest.md'}")
        print()
    print(render_lifecycle_promotion_markdown(pl))
    return 0 if pl.get("result_status") == "success" else 1


def cmd_products_bootstrap_creation(repo: Path, args: Any) -> int:
    from argus.products.creation_bootstrap import (
        render_product_creation_bootstrap_markdown,
        run_product_creation_bootstrap,
    )

    pdir = _products_dir(repo, getattr(args, "products_dir", None))
    payload = run_product_creation_bootstrap(
        repo,
        product_id=str(args.creation_bootstrap_product_id),
        minimal=bool(args.minimal),
        dry_run=bool(args.dry_run),
        write_artifacts=not bool(args.no_save),
        products_dir=pdir,
    )
    if args.json:
        print(dumps_json(payload))
        return 0 if payload.get("ok") else 1
    if not payload.get("ok"):
        print(payload.get("error") or "bootstrap failed", file=sys.stderr)
        return 1
    if not args.no_save and not args.dry_run:
        d = repo / "runs" / "products" / "creation_bootstrap"
        print(f"Wrote {d / 'latest.json'}")
        print(f"Wrote {d / 'latest.md'}")
        print()
    print(render_product_creation_bootstrap_markdown(payload))
    return 0
