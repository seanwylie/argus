"""CLI: ``argus dashboard`` — generate local HTML portfolio view."""

from __future__ import annotations

import sys
import webbrowser
from pathlib import Path
from typing import Any

from argus.cli.repo import repo_root
from argus.core.serialize import dumps_json
from argus.dashboard.render import write_dashboard_html


def _products_dir(repo: Path, override: Path | None) -> Path | None:
    if override is None:
        return None
    return override.resolve()


def run_dashboard_command(args: Any) -> int:
    repo = repo_root()
    if getattr(args, "dashboard_command", None) == "summary":
        from argus.dashboard.operator_summary import (
            render_operator_summary_markdown,
            run_operator_summary,
        )

        pd = getattr(args, "products_dir", None)
        pd = pd.resolve() if pd is not None else None
        write_art = not bool(getattr(args, "no_save", False))
        payload = run_operator_summary(
            repo,
            limit_history=int(getattr(args, "limit_history", 30)),
            products_dir=pd,
            write_artifacts=write_art,
        )
        # Portfolio Builder activity rollup: observational only (same hook as reporting, not outcomes/strategy).
        if write_art:
            from argus.portfolio.builder_activity import write_portfolio_builder_activity_artifacts

            ba_stamped, ba_latest, ba_md = write_portfolio_builder_activity_artifacts(
                repo,
                products_dir=pd,
            )
        if args.json:
            print(dumps_json(payload))
            return 0
        if write_art:
            d = repo / "runs" / "dashboard" / "operator_summary"
            print(f"Wrote {d / 'latest.json'}")
            print(f"Wrote {d / 'latest.md'}")
            print(f"Wrote {ba_latest}")
            print(f"Wrote {ba_stamped}")
            print(f"Wrote {ba_md}")
            print()
        print(render_operator_summary_markdown(payload))
        return 0
    if getattr(args, "dashboard_command", None) == "narrative":
        from argus.dashboard.narrative import (
            render_operator_narrative_markdown,
            run_operator_narrative,
        )

        pd = getattr(args, "products_dir", None)
        pd = pd.resolve() if pd is not None else None
        payload = run_operator_narrative(
            repo,
            limit_history=int(getattr(args, "limit_history", 30)),
            products_dir=pd,
            write_artifacts=not bool(getattr(args, "no_save", False)),
        )
        if args.json:
            print(dumps_json(payload))
            return 0
        if not getattr(args, "no_save", False):
            d = repo / "runs" / "dashboard" / "narrative"
            print(f"Wrote {d / 'latest.json'}")
            print(f"Wrote {d / 'latest.md'}")
            print()
        print(render_operator_narrative_markdown(payload))
        return 0
    try:
        path, payload = write_dashboard_html(
            repo,
            args.out,
            products_dir=_products_dir(repo, args.products_dir),
            strict=bool(getattr(args, "strict", False)),
        )
    except OSError as e:
        print(f"Failed to write dashboard: {e}", file=sys.stderr)
        return 1
    print(path)
    if args.open:
        webbrowser.open(path.as_uri())
    diag = payload.get("diagnostics") or {}
    errs = diag.get("errors") or []
    if getattr(args, "strict", False) and errs:
        for e in errs:
            msg = e.get("message") or e.get("code") or str(e)
            print(f"dashboard strict: {msg}", file=sys.stderr)
        return 1
    return 0
