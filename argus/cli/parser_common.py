"""Shared argparse helpers for the Argus CLI."""

from __future__ import annotations

import argparse
import re
from pathlib import Path


class ArgusHelpFormatter(
    argparse.RawDescriptionHelpFormatter,
    argparse.ArgumentDefaultsHelpFormatter,
):
    """Preserve epilog formatting and show defaults."""


CLI_EPILOG = """
examples:
  uv run argus products list
  uv run argus products validate --json
  uv run argus products create my-app --type micro_saas
  uv run argus signals snapshot-types
  uv run argus signals ingest-snapshots
  uv run argus signals collect
  uv run argus signals show myproduct
  uv run argus audit run --product-id myproduct
  uv run argus audit prompt --product-id myproduct
  uv run argus audit ingest-cursor --product-id myproduct --file scan.json
  uv run argus audit show myproduct --cursor-scan
  uv run argus temporal freshness myproduct
  uv run argus temporal adapters
  uv run argus temporal ingest
  uv run argus temporal ingest myproduct
  uv run argus adapters list
  uv run argus adapters run execution --product-id myproduct
  uv run argus findings generate --fresh-signals
  uv run argus decisions portfolio --json
  uv run argus decisions history myproduct
  uv run argus decisions churn myproduct
  uv run argus lifecycle show myproduct
  uv run argus lifecycle kill-score
  uv run argus lifecycle kill-score myproduct --json
  uv run argus lifecycle report myproduct --json
  uv run argus portfolio refresh
  uv run argus portfolio allocate
  uv run argus doctor
  uv run argus dashboard --open
  uv run argus dashboard --strict
  uv run argus escalation generate myproduct
  uv run argus actions validate path/to/action.yaml
  uv run argus actions dry-run path/to/action.yaml
  uv run argus approval request --action-id my_act --product myproduct
  uv run argus approval approve appr_20260101T120000Z_ab12cd34
  uv run argus approval evaluate path/to/action.yaml
  uv run argus actions execute path/to/action.yaml
  uv run argus execution dry-run path/to/action.yaml
  uv run argus execution run path/to/action.yaml --enable-execution
  uv run argus execution run path/to/action.yaml --autonomous
  uv run argus execution show exec_20260101T120000Z_a1b2c3d4
  uv run argus history snapshot --label nightly
  uv run argus history diff runs/history/snapshots/A/snapshot.json runs/history/snapshots/B/snapshot.json
  uv run argus trends summary
  uv run argus temporal show myproduct
  uv run argus temporal freshness myproduct
  uv run argus temporal summary
  uv run argus trends drift --json
  uv run argus planning weekly
  uv run argus planning weekly --out runs/planning/custom.md
  uv run argus planning actions
  uv run argus planning actions --json
  uv run argus autonomy show
  uv run argus autonomy set supervised
  uv run argus autonomy policy --json
  uv run argus autonomy spawn
  uv run argus autonomy spawn --apply --approve-spawn
  uv run argus autonomy shutdown --all-candidates --json
  uv run argus autonomy shutdown --product myapp --apply --approve
  uv run argus input add --scope global --type strategy --content "Do not kill any products this week" --structured no_kill=true
  uv run argus input list
  uv run argus loop run
  uv run argus loop run --product myproduct
  uv run argus loop full
  uv run argus loop full --product myproduct
  uv run argus run safe-profile
  uv run argus run summary
  uv run argus run summary <run_id> --write
  uv run argus validate artifacts
  uv run argus validate artifacts 20260412T120000Z_ab12cd34
  uv run argus orchestration state --product-id myproduct
  uv run argus orchestration state --all
  uv run argus orchestration advance --product-id myproduct
  uv run argus orchestration run-progression --product-id myproduct
  uv run argus orchestration cursor-prompt --product-id myproduct
  uv run argus orchestration cursor-ingest --product-id myproduct --file review.json
  uv run argus capabilities resume
  uv run argus capabilities resume myproduct
  uv run argus autonomy activate
  uv run argus economics analyze
  uv run argus economics portfolio --json
  uv run argus economics resources
  uv run argus economics resources --json
  uv run argus capabilities list
  uv run argus capabilities evaluate
  uv run argus capabilities gaps --json
  uv run argus capabilities request list
  uv run argus capabilities request create --title "..." --description "..."
  uv run argus self audit
  uv run argus self audit --json
  uv run argus self improve findings
  uv run argus self improve propose --json
  uv run argus self improve plan
  uv run argus confidence assess
  uv run argus confidence explain myproduct
  uv run argus confidence summary --json
  uv run argus advisors list
  uv run argus advisors run myproduct
  uv run argus advisors consensus myproduct --json
  uv run argus advisors consult myproduct
  uv run argus advisors consult myproduct --stub-only --json
  uv run argus experiments create --product-id myapp --hypothesis "X" --type growth
  uv run argus experiments list
  uv run argus experiments evaluate
  uv run argus experiments evaluate myapp --no-apply
  uv run argus experiments apply-execution
  uv run argus experiments propose
  uv run argus experiments propose myapp --json
  uv run argus experiments rank
  uv run argus experiments rank myapp --json
  uv run argus strategy show
  uv run argus strategy set growth
  uv run argus simulate myproduct
  uv run argus simulate --experiment exp_20260101T120000Z_ab12cd34

Typical loop (local, inspectable artifacts under runs/):
  uv run argus portfolio refresh
  uv run argus portfolio show
  uv run argus doctor
  uv run argus dashboard
"""


def parse_product_id(text: str) -> str | None:
    """Extract ``id:`` from YAML text (legacy scan helper)."""
    for line in text.splitlines():
        m = re.match(r"^[ \t]*id:\s*(\S+)", line)
        if m:
            return m.group(1).strip("\"'")
    return None


def discover_product_ids(products_dir: Path) -> list[tuple[Path, str]]:
    """Return ``(path_to_product_yaml, id)`` for each ``products/*/product.yaml``."""
    found: list[tuple[Path, str]] = []
    if not products_dir.is_dir():
        return found
    for product_yaml in sorted(products_dir.glob("*/product.yaml")):
        try:
            raw = product_yaml.read_text(encoding="utf-8")
        except OSError:
            continue
        pid = parse_product_id(raw)
        if pid is not None:
            found.append((product_yaml, pid))
    return found


def add_products_dir(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--products-dir",
        type=Path,
        default=None,
        help="Override products directory (default: <repo>/products)",
    )
