"""CLI: ``argus debug`` — diagnostics (test suite health, etc.)."""

from __future__ import annotations


def register_debug_commands(sub) -> None:
    dbg = sub.add_parser(
        "debug",
        help="Operator diagnostics and repo introspection (test health triage, etc.)",
    )
    dbg_sub = dbg.add_subparsers(dest="debug_command", required=True, metavar="DEBUG_CMD")

    tsh = dbg_sub.add_parser(
        "test-suite-health",
        help="Triage pytest failures (JUnit XML or captured text) and write runs/debug/test_suite_health/latest.json",
    )
    tsh.add_argument(
        "--json",
        action="store_true",
        help="Emit argus.test_suite_health.v1 JSON to stdout",
    )
    tsh.add_argument(
        "--no-write-artifact",
        action="store_true",
        help="Do not write runs/debug/test_suite_health/* (still prints report)",
    )
    tsh.add_argument(
        "--junit-xml",
        metavar="PATH",
        default=None,
        help="Parse an existing pytest --junitxml file instead of running pytest",
    )
    tsh.add_argument(
        "--pytest-text",
        metavar="PATH",
        default=None,
        help="Parse a log that contains FAILED/ERROR lines (best-effort; prefer JUnit)",
    )
    tsh.add_argument(
        "pytest_args",
        nargs="*",
        default=None,
        help="When not using --junit-xml/--pytest-text: extra args for pytest (default: tests)",
    )

    soak = dbg_sub.add_parser(
        "substrate-soak",
        help="Summarize recent autonomous-runner sessions and coherence stamps (stabilization soak triage)",
    )
    soak.add_argument(
        "--json",
        action="store_true",
        help="Emit argus.substrate_soak_summary.v1 JSON to stdout",
    )
    soak.add_argument(
        "--limit-sessions",
        type=int,
        default=5,
        metavar="N",
        help="How many stamped autonomous session JSON files to include (default: 5)",
    )
