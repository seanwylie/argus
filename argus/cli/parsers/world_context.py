"""CLI: ``argus world-context`` — advisory external signal ingestion."""

from __future__ import annotations

from pathlib import Path


def register_world_context_commands(sub) -> None:
    wc = sub.add_parser(
        "world-context",
        help="Ingest and show advisory external world-context signals (runs/world_context/latest.json).",
    )
    wc_sub = wc.add_subparsers(dest="world_context_command", required=True)
    ingest = wc_sub.add_parser(
        "ingest",
        help="Validate signals from a JSON array file and write the world context artifact (all-or-nothing).",
    )
    ingest.add_argument(
        "--file",
        "-f",
        required=True,
        type=Path,
        metavar="PATH",
        help="JSON array of signal objects (see argus.world_context.signal)",
    )
    wc_sub.add_parser(
        "interpret",
        help="Rebuild runs/world_context/interpretation/latest.json from runs/world_context/latest.json.",
    )
    show = wc_sub.add_parser("show", help="Print the current world context artifact")
    show.add_argument(
        "--json",
        action="store_true",
        help="Emit raw JSON (stdout); exits 1 if missing or wrong schema",
    )
