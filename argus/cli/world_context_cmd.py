"""CLI for ``argus world-context`` — ingest advisory external signals."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from argus.core.serialize import dumps_json
from argus.world_context.adapters.manual import load_signals_from_json_path
from argus.world_context.persist import creation_candidates_output_dir, interpretation_output_dir
from argus.world_context.service import (
    load_world_context,
    write_interpretation_from_world_payload,
    write_world_context_from_signals,
)


def run_world_context_command(args: Any, repo_root: Path) -> int:
    repo = repo_root.resolve()
    cmd = str(args.world_context_command or "")

    if cmd == "ingest":
        fpath = Path(str(args.file)).expanduser().resolve()
        if not fpath.is_file():
            print(f"error: file not found: {fpath}", file=sys.stderr)
            return 2
        try:
            signals = load_signals_from_json_path(fpath)
        except (OSError, ValueError) as e:
            print(f"error: {e}", file=sys.stderr)
            return 2
        out, payload, errors = write_world_context_from_signals(repo, signals)
        if errors or out is None:
            print("Validation errors (nothing written):", file=sys.stderr)
            for e in errors:
                print(f"  {e}", file=sys.stderr)
            return 1
        print(f"Wrote {out}")
        print(f"Signals: {payload.get('summary', {}).get('signal_count', 0)}")
        ip = interpretation_output_dir(repo) / "latest.json"
        cp = creation_candidates_output_dir(repo) / "latest.json"
        if ip.is_file():
            print(f"Interpretation: {ip.resolve()}")
        if cp.is_file():
            print(f"Creation candidates: {cp.resolve()}")
        return 0

    if cmd == "interpret":
        raw = load_world_context(repo)
        if not raw:
            print("No runs/world_context/latest.json (or wrong schema). Ingest signals first.", file=sys.stderr)
            return 1
        ip = write_interpretation_from_world_payload(repo, raw)
        if not ip:
            print("Could not build interpretation from world context.", file=sys.stderr)
            return 1
        print(f"Wrote {ip}")
        cp = creation_candidates_output_dir(repo) / "latest.json"
        if cp.is_file():
            print(f"Creation candidates: {cp.resolve()}")
        return 0

    if cmd == "show":
        raw = load_world_context(repo)
        if args.json:
            if not raw:
                print("{}", file=sys.stderr)
                return 1
            print(dumps_json(raw))
            return 0
        if not raw:
            print("No runs/world_context/latest.json (or wrong schema). Run: argus world-context ingest --file …")
            return 1
        summ = raw.get("summary") or {}
        print("World context (advisory only)")
        print("===========================")
        print(raw.get("disclaimer") or "")
        print()
        print(f"Generated: {raw.get('generated_at_utc')}")
        print(f"Headline: {summ.get('headline')}")
        print(f"Signals: {summ.get('signal_count')} (fresh={summ.get('fresh_count')}, stale={summ.get('stale_count')})")
        print()
        for i, s in enumerate(raw.get("signals") or [], 1):
            print(f"{i}. [{s.get('source')}] {s.get('entity')} · {s.get('signal_type')} = {s.get('value')} {s.get('unit')}")
            print(f"   observed: {s.get('observed_at_utc')} · {s.get('freshness_status')} · confidence={s.get('confidence')}")
            print(f"   provenance: {s.get('provenance')}")
        return 0

    print("error: unknown world-context subcommand", file=sys.stderr)
    return 2
