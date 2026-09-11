"""CLI handler for ``argus portfolio allocate``."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from argus.cli.repo import repo_root
from argus.core.serialize import dumps_json, to_jsonable
from argus.portfolio.allocate import run_allocation
from argus.portfolio.models import PortfolioAllocationResult


def _format_text(result: PortfolioAllocationResult) -> str:
    lines = [
        "Portfolio attention allocation",
        "============================",
        "",
        f"Generated (UTC): {result.generated_at_utc}",
        "",
        "Focus (% of your time / attention)",
        "-----------------------------------",
    ]
    for row in sorted(result.products, key=lambda x: (-x.focus_pct, x.product_id)):
        band = row.band.value.upper()
        lines.append(
            f"  {row.product_id:24} {row.focus_pct:6.2f}%  [{band}]  "
            f"priority={row.priority_score:.1f}"
        )

    lines.extend(
        [
            "",
            "Push hard (double down)",
            "-------------------------",
        ]
    )
    if result.push_hard_product_ids:
        for pid in result.push_hard_product_ids:
            lines.append(f"  - {pid}")
    else:
        lines.append("  (none)")

    lines.extend(
        [
            "",
            "De-prioritize / ignore for now",
            "-------------------------------",
        ]
    )
    if result.ignore_product_ids:
        for pid in result.ignore_product_ids:
            lines.append(f"  - {pid}")
    else:
        lines.append("  (none)")

    lines.append("")
    lines.append(
        "Argus blends decision priority, economics, trends (when present), "
        "and open experiments — refresh signals/findings or run "
        "`argus portfolio refresh` for fresher inputs."
    )
    lines.append("")
    return "\n".join(lines)


def run_portfolio_allocate_command(args: Any) -> int:
    repo = repo_root()
    products_dir: Path | None = getattr(args, "products_dir", None)
    result, _gather = run_allocation(repo, products_dir=products_dir)

    if not result.products:
        print(
            "No valid products in inventory — nothing to allocate.",
            file=sys.stderr,
        )
        return 1

    if args.json:
        payload = to_jsonable(result)
        print(dumps_json(payload))
        return 0

    print(_format_text(result))
    return 0
