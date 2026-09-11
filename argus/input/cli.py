"""CLI: ``argus input`` (add, list, show, remove)."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from typing import Any

from argus.cli.repo import repo_root
from argus.core.serialize import dumps_json, to_jsonable
from argus.input.models import HumanInput
from argus.input.store import (
    find_input_path,
    list_inputs,
    load_input,
    new_input_id,
    remove_input,
    save_input,
)


def _parse_structured(pairs: list[str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for p in pairs:
        if "=" not in p:
            continue
        k, v = p.split("=", 1)
        k, v = k.strip(), v.strip()
        if v.lower() in ("true", "false"):
            out[k] = v.lower() == "true"
        elif v and v[0] in "[{":
            try:
                out[k] = json.loads(v)
            except json.JSONDecodeError:
                out[k] = v
        else:
            try:
                if "." in v and v.replace(".", "", 1).replace("-", "", 1).isdigit():
                    out[k] = float(v)
                elif v.isdigit():
                    out[k] = int(v)
                else:
                    out[k] = v
            except ValueError:
                out[k] = v
    return out


def run_input_command(args: Any) -> int:
    r = repo_root()
    sub = args.input_command

    if sub == "add":
        scope = args.scope
        pid = getattr(args, "product_id", None)
        if scope == "product" and not pid:
            print("--product-id is required when --scope product", file=sys.stderr)
            return 2
        sf = _parse_structured(list(getattr(args, "structured", []) or []))
        if getattr(args, "structured_json", None):
            try:
                extra = json.loads(args.structured_json)
                if isinstance(extra, dict):
                    sf.update(extra)
            except json.JSONDecodeError as e:
                print(f"Invalid --structured-json: {e}", file=sys.stderr)
                return 2
        if getattr(args, "hard", False):
            sf["hard_override"] = True
        now = datetime.now(timezone.utc).isoformat()
        inp = HumanInput(
            id=new_input_id(),
            scope=scope,
            product_id=pid,
            type=args.type,
            content=str(args.content or ""),
            structured_fields=sf,
            created_at=now,
            expires_at=(str(args.expires_at) if getattr(args, "expires_at", None) else None),
            priority_weight=float(getattr(args, "weight", 1.0)),
        )
        path = save_input(r, inp)
        if args.json:
            print(dumps_json(to_jsonable(inp)))
        else:
            print(f"Saved {inp.id}")
            print(f"  {path.relative_to(r)}")
        return 0

    if sub == "list":
        rows = list_inputs(r)
        if getattr(args, "product_id", None):
            pid = args.product_id
            rows = [x for x in rows if x.scope == "global" or x.product_id == pid]
        if args.json:
            print(dumps_json([to_jsonable(x) for x in rows]))
        else:
            for x in rows:
                exp = f" exp={x.expires_at}" if x.expires_at else ""
                sc = f" [{x.scope}]"
                pr = f" product={x.product_id}" if x.product_id else ""
                print(f"{x.id}  type={x.type}{sc}{pr}  w={x.priority_weight}{exp}")
                if x.content:
                    line = x.content.replace("\n", " ")[:120]
                    print(f"  {line}")
        return 0

    if sub == "show":
        path = find_input_path(r, args.input_id)
        if path is None:
            print(f"Unknown input id: {args.input_id!r}", file=sys.stderr)
            return 1
        inp = load_input(path)
        if args.json:
            print(dumps_json(to_jsonable(inp)))
        else:
            print(f"id: {inp.id}")
            print(f"scope: {inp.scope}")
            if inp.product_id:
                print(f"product_id: {inp.product_id}")
            print(f"type: {inp.type}")
            print(f"created_at: {inp.created_at}")
            print(f"expires_at: {inp.expires_at or '(none)'}")
            print(f"priority_weight: {inp.priority_weight}")
            print(f"structured_fields: {json.dumps(inp.structured_fields, sort_keys=True)}")
            print("content:")
            print(inp.content)
        return 0

    if sub == "remove":
        if not remove_input(r, args.input_id):
            print(f"Unknown input id: {args.input_id!r}", file=sys.stderr)
            return 1
        if not args.json:
            print(f"Removed {args.input_id}")
        return 0

    print("Unknown input subcommand.", file=sys.stderr)
    return 2
