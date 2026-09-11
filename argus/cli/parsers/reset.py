"""``argus reset`` — controlled local state reset."""

from __future__ import annotations


def register_reset_commands(sub) -> None:
    from argus.cli.reset_cmd import CONFIRM_ALL, CONFIRM_PORTFOLIO

    r = sub.add_parser(
        "reset",
        help=(
            "Controlled reset of local Argus state (runs/, products/, optional caches). "
            "Always use --dry-run first."
        ),
    )
    r.add_argument(
        "--dry-run",
        action="store_true",
        help="List targets and approximate counts only; no deletions.",
    )
    g = r.add_mutually_exclusive_group(required=True)
    g.add_argument(
        "--soft",
        action="store_true",
        help="Remove everything under runs/ except runs/README.md (runtime artifacts only).",
    )
    g.add_argument(
        "--portfolio",
        action="store_true",
        help="Soft reset plus remove every child under products/ (empty portfolio).",
    )
    g.add_argument(
        "--all",
        action="store_true",
        help=(
            "Portfolio reset plus conservative extras (e.g. .import_cache/). "
            "Does not delete config/, .env*, or source. Requires strong confirmation."
        ),
    )
    r.add_argument(
        "--confirm",
        metavar="TOKEN",
        default=None,
        help=(
            f"When not using --dry-run: --portfolio requires {CONFIRM_PORTFOLIO}; "
            f"--all requires {CONFIRM_ALL}. Soft mode does not require a token."
        ),
    )
