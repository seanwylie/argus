"""CLI for ``argus trends``."""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from argus.cli.repo import repo_root
from argus.trends.analyze import analyze_all_with_history, analyze_product
from argus.trends.models import TrendsRunPayload
from argus.trends.render import (
    format_drift_report,
    format_full_text,
    format_summary_line,
    payload_to_json,
)


def trends_dir(r: Path) -> Path:
    return r.resolve() / "runs" / "trends"


def _write_artifacts(repo: Path, payload: TrendsRunPayload, *, text_body: str) -> Path:
    d = trends_dir(repo)
    d.mkdir(parents=True, exist_ok=True)
    latest_json = d / "latest.json"
    latest_txt = d / "latest.txt"
    latest_json.write_text(payload_to_json(payload), encoding="utf-8")
    latest_txt.write_text(text_body, encoding="utf-8")
    return latest_json


def run_trends_command(args: Any) -> int:
    r = repo_root()
    sub = args.trends_command
    now = datetime.now(timezone.utc).isoformat()

    if sub == "analyze":
        pid = getattr(args, "product_id", None)
        if pid:
            summaries = [analyze_product(r, pid)]
            cmd = f"analyze {pid}"
        else:
            summaries = analyze_all_with_history(r)
            cmd = "analyze"
        payload = TrendsRunPayload(
            generated_at_utc=now,
            repo_root=str(r.resolve()),
            command=cmd,
            summaries=summaries,
        )
        text_body = format_full_text(payload)
        out_path = _write_artifacts(r, payload, text_body=text_body)
        if args.json:
            print(payload_to_json(payload))
        else:
            print(text_body)
            print(f"\nWrote {out_path.relative_to(r.resolve()).as_posix()}", file=sys.stderr)
        return 0

    if sub == "summary":
        summaries = analyze_all_with_history(r)
        payload = TrendsRunPayload(
            generated_at_utc=now,
            repo_root=str(r.resolve()),
            command="summary",
            summaries=summaries,
        )
        lines = [format_summary_line(s) for s in summaries]
        text_body = "\n".join(lines) + ("\n" if lines else "No products with snapshot history.\n")
        _write_artifacts(r, payload, text_body=text_body)
        if args.json:
            print(payload_to_json(payload))
        else:
            print(text_body, end="")
        return 0

    if sub == "drift":
        summaries = analyze_all_with_history(r)
        payload = TrendsRunPayload(
            generated_at_utc=now,
            repo_root=str(r.resolve()),
            command="drift",
            summaries=summaries,
        )
        text_body = format_drift_report(summaries)
        _write_artifacts(r, payload, text_body=text_body)
        if args.json:
            # JSON: only products with drift
            drift_only = [s for s in summaries if s.drift_signals]
            print(
                payload_to_json(
                    TrendsRunPayload(
                        generated_at_utc=payload.generated_at_utc,
                        repo_root=payload.repo_root,
                        command="drift",
                        summaries=drift_only,
                    )
                )
            )
        else:
            print(text_body, end="")
        return 0

    print(f"Unknown trends subcommand: {sub}", file=sys.stderr)
    return 2
