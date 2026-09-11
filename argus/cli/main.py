"""Argus CLI: unified operator surface for products, signals, findings, decisions, and portfolio."""

from __future__ import annotations

import argparse

from argus.cli.dispatch import dispatch
from argus.cli.parser_common import CLI_EPILOG, ArgusHelpFormatter
from argus.cli.parsers import register_all_commands


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="argus",
        description=(
            "Argus: observe and evaluate products in the monorepo — discovery, signals, "
            "findings, lifecycle scoring, ranked decisions, portfolio refresh, economics, "
            "human input, experiments, strategy modes, orchestrated loop, and static dashboard. "
            "All local and inspectable."
        ),
        epilog=CLI_EPILOG,
        formatter_class=ArgusHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")
    register_all_commands(sub)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return dispatch(args)


if __name__ == "__main__":
    raise SystemExit(main())
