"""CLI: ``argus capabilities request``."""

from __future__ import annotations

import sys
from typing import Any

from argus.autonomy.controller import remove_pause_for_request
from argus.capabilities.requests.models import (
    CapabilityRequestSource,
    CapabilityRequestStatus,
    is_terminal_status,
)
from argus.capabilities.requests.store import (
    create_request,
    list_requests,
    load_request,
    update_request_status,
)


def _parse_source(raw: str) -> CapabilityRequestSource:
    s = raw.strip().lower()
    try:
        return CapabilityRequestSource(s)
    except ValueError:
        return CapabilityRequestSource.MANUAL


def _format_request(rec: Any) -> str:
    lines = [
        f"request_id:    {rec.request_id}",
        f"status:        {rec.status.value}",
        f"source:        {rec.source.value}",
        f"title:         {rec.title}",
        f"product_id:    {rec.product_id or '(none)'}",
        f"capability:    {rec.capability_hint or '(none)'}",
        f"created_at:    {rec.created_at}",
        f"updated_at:    {rec.updated_at}",
        "",
        "description:",
        rec.description,
        "",
    ]
    if rec.source_ref:
        lines.append("source_ref:")
        for k, v in rec.source_ref.items():
            lines.append(f"  {k}: {v}")
        lines.append("")
    if rec.resolution_note:
        lines.extend(["resolution_note:", rec.resolution_note, ""])
    return "\n".join(lines)


def run_capability_requests_command(args: Any) -> int:
    from argus.cli.repo import repo_root as rr
    from argus.core.serialize import dumps_json, to_jsonable

    r = rr()
    sub = args.request_command

    if sub == "list":
        st = getattr(args, "status", None)
        filt: CapabilityRequestStatus | None = None
        if st:
            try:
                filt = CapabilityRequestStatus(str(st).strip().lower())
            except ValueError:
                print(f"Invalid status: {st!r}", file=sys.stderr)
                return 1
        rows = list_requests(r, status=filt)
        if args.json:
            print(dumps_json([to_jsonable(x) for x in rows]))
        else:
            if not rows:
                print("No capability requests (runs/capabilities/requests/).")
                return 0
            for rec in rows:
                print(
                    f"{rec.request_id}\t{rec.status.value}\t{rec.source.value}\t"
                    f"{rec.product_id or '-'}\t{rec.title[:60]}"
                )
        return 0

    if sub == "create":
        title = str(args.title).strip()
        desc = str(args.description or "").strip()
        if not title:
            print("--title is required", file=sys.stderr)
            return 1
        if not desc:
            print("--description is required", file=sys.stderr)
            return 1
        src = _parse_source(str(args.source or "manual"))
        pid = str(args.product_id).strip() if getattr(args, "product_id", None) else None
        hint = str(args.capability_hint or "").strip()
        ref: dict[str, Any] = {}
        for pair in getattr(args, "ref", None) or []:
            if "=" in pair:
                k, _, v = pair.partition("=")
                ref[k.strip()] = v.strip()
        rec = create_request(
            r,
            title=title,
            description=desc,
            source=src,
            capability_hint=hint,
            product_id=pid,
            source_ref=ref,
        )
        if args.json:
            print(dumps_json(to_jsonable(rec)))
        else:
            print(f"Created {rec.request_id}")
        return 0

    if sub == "show":
        rid = str(args.request_id).strip()
        rec = load_request(r, rid)
        if rec is None:
            print(f"Unknown request_id: {rid!r}", file=sys.stderr)
            return 1
        if args.json:
            print(dumps_json(to_jsonable(rec)))
        else:
            print(_format_request(rec), end="")
        return 0

    if sub == "set-status":
        rid = str(args.request_id).strip()
        try:
            st = CapabilityRequestStatus(str(args.status).strip().lower())
        except ValueError:
            print(f"Invalid status: {args.status!r}", file=sys.stderr)
            return 1
        try:
            rec = update_request_status(
                r,
                rid,
                status=st,
                resolution_note=str(getattr(args, "note", "") or ""),
            )
            if is_terminal_status(rec.status):
                remove_pause_for_request(r, rid)
        except KeyError as e:
            print(str(e), file=sys.stderr)
            return 1
        except ValueError as e:
            print(str(e), file=sys.stderr)
            return 1
        if args.json:
            print(dumps_json(to_jsonable(rec)))
        else:
            print(f"Updated {rec.request_id} -> {rec.status.value}")
        return 0

    print("Unknown capability request subcommand.", file=sys.stderr)
    return 2
