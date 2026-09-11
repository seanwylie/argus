"""Handlers for ``argus debug``."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from argus.cli.repo import repo_root
from argus.core.serialize import dumps_json
from argus.debug.substrate_soak_summary import summarize_substrate_soak
from argus.debug.test_suite_health import (
    build_health_payload,
    parse_junit_xml,
    parse_pytest_text,
    render_test_suite_health_terminal,
    run_pytest_junit,
    write_test_suite_health_artifact,
)


def run_debug_subcommand(args: Any) -> int:
    if args.debug_command == "test-suite-health":
        return _cmd_test_suite_health(args)
    if args.debug_command == "substrate-soak":
        return _cmd_substrate_soak(args)
    print("Unknown debug subcommand.", file=sys.stderr)
    return 2


def _cmd_substrate_soak(args: Any) -> int:
    root = repo_root()
    limit = int(getattr(args, "limit_sessions", 5) or 5)
    payload = summarize_substrate_soak(root, limit_sessions=limit)
    if getattr(args, "json", False):
        print(dumps_json(payload))
    else:
        counts = payload.get("substrate_status_counts_from_sessions") or {}
        print("# Substrate soak (recent autonomous sessions)", end="\n\n")
        print(f"- sessions_inspected: {payload.get('sessions_inspected')}")
        print(f"- status_counts (from session payloads): {counts}")
        print(f"- substrate_policy_state: {payload.get('substrate_policy_state')}")
        if payload.get("recent_sessions_newest_first"):
            print("\n## Recent sessions (newest first)\n")
            for row in payload["recent_sessions_newest_first"]:
                print(
                    f"- `{row.get('session_id')}` · stop `{row.get('stop_reason')}` · "
                    f"substrate `{row.get('substrate_overall_status')}`"
                )
        if payload.get("coherence_stamps_newest_first"):
            print("\n## Recent coherence stamps (newest first)\n")
            for row in payload["coherence_stamps_newest_first"]:
                print(f"- `{row.get('file')}` · overall `{row.get('overall_status')}`")
    return 0


def _cmd_test_suite_health(args: Any) -> int:
    root = repo_root()
    junit_arg = getattr(args, "junit_xml", None)
    text_arg = getattr(args, "pytest_text", None)
    pytest_args = getattr(args, "pytest_args", None)
    if not pytest_args:
        pytest_args = ["tests"]

    source = "pytest_run"
    failures: list = []
    total_passed = None
    total_errors = None
    total_tests = None
    pytest_cmd = None
    junit_input_path = None
    text_input_path = None

    if junit_arg:
        p = Path(junit_arg).resolve()
        if not p.is_file():
            print(f"JUnit file not found: {p}", file=sys.stderr)
            return 1
        source = "junit_xml"
        junit_input_path = str(p)
        failures, raw_counts = parse_junit_xml(p)
        fail_ct = raw_counts.get("failures")
        total_errors = raw_counts.get("errors")
        total_tests = raw_counts.get("tests")
        if total_tests is not None:
            fail_n = (fail_ct or 0) + (total_errors or 0)
            skip_n = raw_counts.get("skipped") or 0
            total_passed = max(0, total_tests - fail_n - skip_n)
    elif text_arg:
        p = Path(text_arg).resolve()
        if not p.is_file():
            print(f"Text log not found: {p}", file=sys.stderr)
            return 1
        source = "pytest_text"
        text_input_path = str(p)
        content = p.read_text(encoding="utf-8", errors="replace")
        failures = parse_pytest_text(content)
    else:
        failures, counts, pytest_cmd, junit_dest = run_pytest_junit(root, list(pytest_args))
        source = "pytest_run"
        total_passed = counts.get("passed")
        total_tests = counts.get("tests")
        if junit_dest is not None:
            junit_input_path = str(junit_dest)

    payload = build_health_payload(
        failures=failures,
        total_passed=total_passed,
        total_errors=total_errors,
        total_tests=total_tests,
        source=source,
        pytest_cmd=pytest_cmd,
        junit_path=junit_input_path,
        text_path=text_input_path,
    )

    if not getattr(args, "no_write_artifact", False):
        write_test_suite_health_artifact(root, payload)

    if getattr(args, "json", False):
        print(dumps_json(payload))
    else:
        print(render_test_suite_health_terminal(payload), end="")

    return 0
