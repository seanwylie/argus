"""CLI: ``argus escalation`` (generate, show, list packets)."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from argus.cli.repo import repo_root
from argus.core.serialize import dumps_json
from argus.escalation.dedupe import find_recent_duplicate_packet
from argus.escalation.packet import (
    generate_packet_for_product,
    list_packets,
    load_packet,
    packet_from_dict,
    save_packet,
)
from argus.escalation.render import render_json, render_markdown, render_text
from argus.findings.persistence import load_latest_findings
from argus.products.inventory import build_inventory


def _products_dir(repo: Path, override: Path | None) -> Path | None:
    if override is None:
        return None
    return override.resolve()


def cmd_escalation_generate(repo: Path, args: Any) -> int:
    inv = build_inventory(repo, products_dir=_products_dir(repo, args.products_dir))
    pid = args.product_id
    if pid not in inv.valid:
        print(f"Unknown or invalid product: {pid!r}", file=sys.stderr)
        return 1
    node = inv.valid[pid].node
    bundle = load_latest_findings(repo, pid)
    if bundle is None:
        print(
            f"No findings for {pid!r}. Run: argus findings generate {pid}",
            file=sys.stderr,
        )
        return 1

    pkt, matches = generate_packet_for_product(repo, node, bundle.findings)
    if pkt is None:
        msg = f"No escalation triggers met for {pid!r} (policy allows continuing without a packet)."
        if args.json:
            print(dumps_json({"ok": True, "escalation_needed": False, "message": msg}))
        else:
            print(msg)
        return 0

    rule_ids = [m.rule_id for m in matches]
    dup = None
    if not args.no_save and not getattr(args, "force_save", False):
        dup = find_recent_duplicate_packet(repo, pid, rule_ids, hours=float(getattr(args, "dedupe_hours", 24.0)))
    written = None
    if dup and not args.no_save:
        msg = (
            f"Skipping write: duplicate escalation within {getattr(args, 'dedupe_hours', 24.0)}h for this "
            f"product + trigger set (existing {dup.get('packet_id')}). Use --force-save to write anyway."
        )
        if args.json:
            print(
                dumps_json(
                    {
                        "ok": True,
                        "escalation_needed": True,
                        "written": False,
                        "dedupe_skipped": True,
                        "duplicate_of": dup,
                        "message": msg,
                        "packet_preview": render_json(pkt),
                    }
                )
            )
        else:
            print(render_text(pkt))
            print(f"\n{msg}", file=sys.stderr)
        return 0

    if not args.no_save:
        path = save_packet(repo, pkt)
        written = str(path)
    else:
        written = None

    if args.json:
        print(render_json(pkt))
    elif getattr(args, "markdown", False):
        print(render_markdown(pkt))
    else:
        print(render_text(pkt))
        if written:
            print(f"\nWrote: {written}", file=sys.stderr)
        elif args.no_save:
            print("\n(--no-save: not written to runs/escalations/)", file=sys.stderr)

    return 0


def cmd_escalation_show(repo: Path, args: Any) -> int:
    raw = load_packet(repo, args.packet_id)
    if raw is None:
        print(f"Unknown packet id: {args.packet_id!r}", file=sys.stderr)
        return 1
    pkt = packet_from_dict(raw)
    if args.json:
        print(render_json(pkt))
    elif getattr(args, "markdown", False):
        print(render_markdown(pkt))
    else:
        print(render_text(pkt))
    return 0


def cmd_escalation_list(repo: Path, args: Any) -> int:
    rows = list_packets(repo)
    if args.json:
        print(dumps_json({"packets": rows}))
        return 0
    if not rows:
        print("No escalation packets under runs/escalations/latest/")
        return 0
    for r in rows:
        print(
            f"{r.get('packet_id')}\t{r.get('product_id')}\t{r.get('risk_level')}\t{r.get('created_at')}"
        )
    return 0


def run_escalation_subcommand(args: Any) -> int:
    repo = repo_root()
    if args.escalation_command == "generate":
        return cmd_escalation_generate(repo, args)
    if args.escalation_command == "show":
        return cmd_escalation_show(repo, args)
    if args.escalation_command == "list":
        return cmd_escalation_list(repo, args)
    return 2
