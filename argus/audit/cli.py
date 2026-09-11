"""CLI: ``argus audit`` — deterministic product audits."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from argus.audit.agent_prompt import build_cursor_scan_prompt, build_cursor_scan_prompt_for_angle
from argus.audit.bundle import load_audit_bundle
from argus.audit.cache import list_audited_products, load_latest_audit, run_audit
from argus.audit.ingest_agent import ingest_agent_angles, load_ingest_json
from argus.core.serialize import dumps_json
from argus.products.inventory import build_inventory


def _parse_angle_csv(s: str | None) -> list[str] | None:
    if not s:
        return None
    return [x.strip() for x in str(s).split(",") if x.strip()]


def run_audit_command(args: Any, repo_root: Path) -> int:
    r = repo_root.resolve()
    cmd = getattr(args, "audit_command", None)
    if cmd is None:
        print("audit: missing subcommand", file=sys.stderr)
        return 2

    if cmd == "run":
        if getattr(args, "all_products", False):
            inv = build_inventory(r)
            errs = 0
            for pid in sorted(inv.valid.keys()):
                try:
                    s = run_audit(r, pid)
                    if not getattr(args, "json", False):
                        print(f"{pid}: ok ({s.counts_by_status})")
                except Exception as e:
                    errs += 1
                    print(f"{pid}: error {e}", file=sys.stderr)
            return 1 if errs else 0
        pid = getattr(args, "product_id", None)
        if not pid:
            print("audit run: --product-id required unless --all", file=sys.stderr)
            return 2
        try:
            s = run_audit(r, pid)
        except Exception as e:
            print(str(e), file=sys.stderr)
            return 1
        if getattr(args, "json", False):
            print(dumps_json(s.to_dict()))
        else:
            print(f"Wrote runs/audit/{pid}/bundle.json + latest.json (Product Gap)")
            print(f"counts: {s.counts_by_status}")
        return 0

    if cmd == "show":
        pid = getattr(args, "product_id", None)
        if not pid:
            print("audit show: product_id required", file=sys.stderr)
            return 2
        if getattr(args, "legacy_product_gap", False):
            s = load_latest_audit(r, pid)
            if s is None:
                print(f"No audit for {pid!r}; run `argus audit run --product-id {pid}`", file=sys.stderr)
                return 1
            print(dumps_json(s.to_dict()))
            return 0
        b = load_audit_bundle(r, pid)
        if b is not None:
            if getattr(args, "cursor_scan", False):
                angles: dict[str, Any] = {}
                for aid, block in (b.get("angles") or {}).items():
                    if isinstance(block, dict):
                        cs = block.get("cursor_scan")
                        angles[aid] = cs if isinstance(cs, dict) else None
                payload = {
                    "schema": "argus.audit_cursor_scan_view.v1",
                    "product_id": pid,
                    "inputs_fingerprint_bundle": b.get("inputs_fingerprint_bundle"),
                    "angles": angles,
                }
                print(dumps_json(payload))
                return 0
            if getattr(args, "merged_text", False):
                lines_out: list[str] = [
                    f"product_id={pid}",
                    f"bundle_fp={b.get('inputs_fingerprint_bundle', '')}",
                    "",
                ]
                for aid, block in (b.get("angles") or {}).items():
                    if not isinstance(block, dict):
                        continue
                    src = block.get("sources") or {}
                    cs = bool(src.get("cursor_scan"))
                    lines_out.append(f"## {aid}  [cursor_scan={cs}]")
                    for ln in block.get("summary_lines") or []:
                        lines_out.append(f"  {ln}")
                    lines_out.append("")
                print("\n".join(lines_out).rstrip())
                return 0
            print(dumps_json(b))
            return 0
        s = load_latest_audit(r, pid)
        if s is None:
            print(f"No audit for {pid!r}; run `argus audit run --product-id {pid}`", file=sys.stderr)
            return 1
        print(dumps_json(s.to_dict()))
        return 0

    if cmd == "list":
        ids = list_audited_products(r)
        if getattr(args, "json", False):
            print(dumps_json({"schema": "argus.audit_list.v1", "product_ids": ids}))
        else:
            for x in ids:
                print(x)
        return 0

    if cmd == "summary":
        rows = []
        for pid in list_audited_products(r):
            s = load_latest_audit(r, pid)
            if s:
                rows.append(
                    {
                        "product_id": pid,
                        "generated_at_utc": s.generated_at_utc,
                        "counts": s.counts_by_status,
                        "fingerprint": s.inputs_fingerprint,
                    }
                )
        if getattr(args, "json", False):
            print(dumps_json({"schema": "argus.audit_summary_portfolio.v1", "audits": rows}))
        else:
            for row in rows:
                print(f"{row['product_id']}\t{row['generated_at_utc']}\t{row['counts']}")
        return 0

    if cmd in ("prompt", "cursor-prompt"):
        pid = getattr(args, "product_id", None)
        if not pid:
            print("audit prompt: --product-id required", file=sys.stderr)
            return 2
        single = getattr(args, "angle", None)
        if single:
            if getattr(args, "angles", None):
                print("audit prompt: use either --angle or --angles, not both", file=sys.stderr)
                return 2
        try:
            if single:
                text = build_cursor_scan_prompt_for_angle(r, pid, str(single))
            else:
                text = build_cursor_scan_prompt(r, pid, angle_ids=_parse_angle_csv(getattr(args, "angles", None)))
        except Exception as e:
            print(str(e), file=sys.stderr)
            return 1
        print(text)
        return 0

    if cmd in ("ingest-agent", "ingest-cursor"):
        pid = getattr(args, "product_id", None)
        path = getattr(args, "file", None)
        if not pid or not path:
            print(
                f"audit {cmd}: --product-id and --file required",
                file=sys.stderr,
            )
            return 2
        p = Path(path)
        if not p.is_file():
            print(f"not a file: {p}", file=sys.stderr)
            return 1
        try:
            raw = load_ingest_json(p)
            out = ingest_agent_angles(
                r,
                pid,
                raw,
                single_angle=getattr(args, "angle", None),
                overwrite=not getattr(args, "no_overwrite", False),
            )
        except Exception as e:
            print(str(e), file=sys.stderr)
            return 1
        if getattr(args, "json", False):
            print(dumps_json(out))
        else:
            print(
                f"Merged validated Cursor scan(s) into runs/audit/{pid}/bundle.json "
                "(deterministic audit preserved; re-run `audit run` keeps cursor_scan)",
            )
        return 0

    print("Unknown audit command.", file=sys.stderr)
    return 2
