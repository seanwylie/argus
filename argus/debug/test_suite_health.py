"""
Pytest failure triage — read-only classification for stabilization prioritization.

Consumes JUnit XML from pytest or lightweight ``FAILED ...`` text; does not mutate tests.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Final

from argus.core.serialize import dumps_json, to_jsonable

TEST_SUITE_HEALTH_SCHEMA: Final = "argus.test_suite_health.v1"


def test_suite_health_dir(repo_root: Path) -> Path:
    return Path(repo_root).resolve() / "runs" / "debug" / "test_suite_health"


@dataclass(frozen=True)
class FailureRecord:
    test_id: str
    message: str | None
    kind: str  # "failure" | "error"


_FAILED_LINE = re.compile(
    r"^(FAILED|ERROR)\s+(\S+)\s*(?:-\s*(.*))?$",
    re.MULTILINE,
)


def _utc_run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]


def classify_failure(test_id: str, message: str | None) -> tuple[str, bool, str]:
    """
    Return (category, blocking_for_stabilization, short_reason).

    Conservative: docs/wording drift is not blocking; core orchestration/portfolio paths are.
    """
    tid = test_id.replace("\\", "/")
    tlower = tid.lower()
    msg = (message or "").lower()

    # Documentation / north-star / context wording (not runtime product code)
    if "north_star" in tlower or "north-star" in tlower:
        return "docs_word", False, "north-star / context doc assertion"
    if "argus_context" in tlower or "argus-context" in tlower:
        return "docs_word", False, "argus-context documentation assertion"
    if "test_docs" in tlower or "/docs/" in tlower and "test_" in tlower:
        return "docs_word", False, "documentation-focused test"
    if "burn-down" in msg or "burn_down" in msg:
        return "docs_word", False, "wording / burn-down table assertion"
    if "not found in" in msg and (".md" in msg or "north" in msg):
        return "docs_word", False, "markdown content assertion drift"

    # Orchestration step executor — high leverage for loop correctness
    if "orchestration_step_executor" in tlower or "step_executor" in tlower:
        return "orchestration_executor", True, "orchestration step executor (runtime chain)"

    if "orchestrat" in tlower:
        return "orchestration", True, "orchestration subsystem"

    # Portfolio / autonomy / coherence / cycle
    if "artifact_coherence" in tlower:
        return "coherence", True, "artifact coherence stabilization"
    if "autonomous_runner" in tlower or "runner_service" in tlower:
        return "runner", True, "autonomous runner / runner service"
    if "portfolio" in tlower and "test_" in tlower:
        if "cycle" in tlower or "test_portfolio_cycle" in tlower:
            return "portfolio_cycle", True, "portfolio cycle"
        return "portfolio", True, "portfolio subsystem"
    if "/cycle" in tlower or "test_mission_experiment" in tlower:
        return "portfolio_cycle", True, "cycle / mission experiment surface"

    # Operator surfaces
    if (
        "dashboard" in tlower
        or "operator_console" in tlower
        or "operator_summary" in tlower
        or "operator_narrative" in tlower
    ):
        return "dashboard", True, "operator dashboard / summary / narrative"

    # Policy / strategy / lifecycle
    if "policy" in tlower and "test_" in tlower:
        return "policy", True, "policy control plane tests"
    if "lifecycle" in tlower and "test_" in tlower:
        return "lifecycle", True, "lifecycle integrity"
    if "strategy" in tlower and "test_" in tlower:
        return "strategy", True, "strategy surfaces"

    if "autonomy" in tlower or "autonomous" in tlower:
        return "autonomy", True, "autonomy boundary / runner integration"

    return "other", False, "uncategorized — manual triage"


def parse_pytest_text(content: str) -> list[FailureRecord]:
    """Parse ``pytest -q`` style output with ``FAILED path::node - msg`` lines."""
    out: list[FailureRecord] = []
    for m in _FAILED_LINE.finditer(content):
        kind = m.group(1).lower()
        test_id = m.group(2).strip()
        msg = (m.group(3) or "").strip() or None
        out.append(FailureRecord(test_id=test_id, message=msg, kind=kind))
    return out


def _junit_test_id(case: ET.Element) -> str:
    """Build a pytest-like node id from JUnit fields (``file`` may be absent)."""
    file_attr = (case.attrib.get("file") or "").strip()
    cls = (case.attrib.get("classname") or "").strip()
    name = (case.attrib.get("name") or "").strip()
    if file_attr and name:
        if cls and "." in cls:
            last = cls.rsplit(".", 1)[-1]
            if last and last[0].isupper():
                return f"{file_attr}::{last}::{name}"
        return f"{file_attr}::{name}"
    if not cls:
        return name or "unknown"
    parts = cls.split(".")
    if len(parts) >= 2 and parts[-1] and parts[-1][0].isupper():
        mod = ".".join(parts[:-1])
        cname = parts[-1]
        path = mod.replace(".", "/") + ".py"
        return f"{path}::{cname}::{name}"
    path = cls.replace(".", "/") + ".py"
    return f"{path}::{name}"


def parse_junit_xml(path: Path) -> tuple[list[FailureRecord], dict[str, int]]:
    """
    Parse pytest ``--junitxml`` output.

    Returns failures plus counts: tests, failures, errors, skipped (when present).
    """
    raw = path.read_text(encoding="utf-8", errors="replace")
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return [], {}
    failures: list[FailureRecord] = []
    counts: dict[str, int] = {}

    # pytest uses <testsuites> wrapper sometimes
    suites = root.findall(".//testsuite")
    if not suites:
        suites = [root]

    for ts in suites:
        for k in ("tests", "failures", "errors", "skipped"):
            v = ts.attrib.get(k)
            if v is not None:
                try:
                    counts[k] = int(v)
                except ValueError:
                    pass
        for case in ts.findall("testcase"):
            test_id = _junit_test_id(case)

            msg_parts: list[str] = []
            for tag in ("failure", "error"):
                el = case.find(tag)
                if el is not None:
                    txt = (el.attrib.get("message") or "").strip()
                    body = (el.text or "").strip()
                    if txt:
                        msg_parts.append(txt)
                    if body:
                        msg_parts.append(body[:2000])
                    failures.append(
                        FailureRecord(
                            test_id=test_id,
                            message="\n".join(msg_parts) if msg_parts else None,
                            kind="failure" if tag == "failure" else "error",
                        )
                    )
                    break

    return failures, counts


def _group_by_module(test_ids: list[str]) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = {}
    for tid in test_ids:
        mod = tid.split("::", 1)[0] if "::" in tid else tid
        grouped.setdefault(mod, []).append(tid)
    for k in grouped:
        grouped[k] = sorted(grouped[k])
    return dict(sorted(grouped.items(), key=lambda x: (-len(x[1]), x[0])))


def build_health_payload(
    *,
    failures: list[FailureRecord],
    total_passed: int | None,
    total_errors: int | None,
    total_tests: int | None,
    source: str,
    pytest_cmd: list[str] | None,
    junit_path: str | None,
    text_path: str | None,
) -> dict[str, Any]:
    run_id = _utc_run_id()
    evaluated = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    failed_details: list[dict[str, Any]] = []
    cat_counts: Counter[str] = Counter()
    blocking: list[dict[str, Any]] = []
    known_candidates: list[dict[str, Any]] = []

    for fr in failures:
        cat, is_blocking, reason = classify_failure(fr.test_id, fr.message)
        cat_counts[cat] += 1
        row = {
            "test_id": fr.test_id,
            "kind": fr.kind,
            "category": cat,
            "blocking_for_stabilization": is_blocking,
            "reason": reason,
            "message_excerpt": (fr.message or "")[:500],
        }
        failed_details.append(row)
        if is_blocking:
            blocking.append(
                {
                    "test_id": fr.test_id,
                    "category": cat,
                    "reason": reason,
                }
            )
        else:
            if cat in ("docs_word", "docs", "wording"):
                known_candidates.append(
                    {
                        "test_id": fr.test_id,
                        "category": cat,
                        "note": "likely documentation or wording drift",
                    }
                )
            elif cat == "other":
                known_candidates.append(
                    {
                        "test_id": fr.test_id,
                        "category": cat,
                        "note": "uncategorized — may be unrelated or needs manual bucket",
                    }
                )

    test_ids = [f.test_id for f in failures]
    grouped = _group_by_module(test_ids)

    blocking_by_cat: Counter[str] = Counter()
    for b in blocking:
        blocking_by_cat[b["category"]] += 1

    suggested: list[str] = []
    if blocking_by_cat:
        for cat, n in blocking_by_cat.most_common(5):
            suggested.append(f"Address {cat} cluster ({n} blocking failure(s)) — see grouped_failures by file.")
    if known_candidates and not suggested:
        suggested.append("Review known_failure_candidates (mostly non-blocking drift) before deep runtime fixes.")

    summary_parts = [
        f"failures={len(failures)}",
        f"blocking={len(blocking)}",
        f"non_blocking_hint={len(known_candidates)}",
    ]
    if total_tests is not None:
        summary_parts.append(f"total_tests~={total_tests}")

    payload: dict[str, Any] = {
        "schema": TEST_SUITE_HEALTH_SCHEMA,
        "run_id": run_id,
        "evaluated_at_utc": evaluated,
        "total_failed": len(failures),
        "total_passed": total_passed,
        "total_errors": total_errors,
        "total_tests_reported": total_tests,
        "failed_tests": failed_details,
        "grouped_failures": grouped,
        "categories": dict(sorted(cat_counts.items(), key=lambda x: (-x[1], x[0]))),
        "likely_blocking_for_stabilization": blocking,
        "known_failure_candidates": known_candidates,
        "summary": "; ".join(summary_parts),
        "suggested_next_targets": suggested[:8],
        "inputs": {
            "source": source,
            "pytest_cmd": pytest_cmd,
            "junit_xml_path": junit_path,
            "pytest_text_path": text_path,
        },
    }
    return payload


def write_test_suite_health_artifact(repo_root: Path, payload: dict[str, Any]) -> Path:
    d = test_suite_health_dir(repo_root)
    d.mkdir(parents=True, exist_ok=True)
    rid = str(payload.get("run_id") or "latest")
    body = dumps_json(to_jsonable(payload)) + "\n"
    stamped = d / f"{rid}.json"
    latest = d / "latest.json"
    stamped.write_text(body, encoding="utf-8")
    shutil.copyfile(stamped, latest)
    return latest


def run_pytest_junit(
    repo_root: Path,
    extra_args: list[str],
    *,
    python: str | None = None,
) -> tuple[list[FailureRecord], dict[str, int | None], list[str], Path | None]:
    """
    Run pytest with JUnit XML in a temp file. Returns failures, count dict, command used, junit path.
    """
    exe = python or sys.executable
    fd, tmp_name = tempfile.mkstemp(suffix=".xml")
    os.close(fd)
    tmp = Path(tmp_name)
    cmd = [
        exe,
        "-m",
        "pytest",
        "--tb=no",
        "-q",
        f"--junitxml={tmp}",
        "--no-header",
        *extra_args,
    ]
    subprocess.run(
        cmd,
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        timeout=7200,
    )
    failures: list[FailureRecord] = []
    counts: dict[str, int | None] = {
        "failures": None,
        "passed": None,
        "tests": None,
        "errors": None,
    }
    junit_path: Path | None = None
    try:
        if tmp.is_file() and tmp.stat().st_size > 0:
            failures, raw_counts = parse_junit_xml(tmp)
            counts["failures"] = raw_counts.get("failures")
            counts["errors"] = raw_counts.get("errors")
            counts["tests"] = raw_counts.get("tests")
            if counts["tests"] is not None:
                fail_n = (counts["failures"] or 0) + (counts["errors"] or 0)
                skip_n = raw_counts.get("skipped") or 0
                counts["passed"] = max(0, counts["tests"] - fail_n - skip_n)
            # Durable copy for inspection (temp file is removed).
            d = test_suite_health_dir(repo_root)
            d.mkdir(parents=True, exist_ok=True)
            dest = d / "last_pytest_junit.xml"
            shutil.copyfile(tmp, dest)
            junit_path = dest
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
    return failures, counts, cmd, junit_path


def render_test_suite_health_terminal(payload: dict[str, Any]) -> str:
    """Concise human report for operators."""
    lines = [
        "# Test suite health (triage)",
        "",
        f"**Failures:** {payload.get('total_failed', 0)}",
    ]
    tp = payload.get("total_passed")
    if tp is not None:
        lines.append(f"**Passed (from JUnit):** {tp}")
    tt = payload.get("total_tests_reported")
    if tt is not None:
        lines.append(f"**Tests (reported):** {tt}")

    lines.extend(["", "## Category mix", ""])
    cats = payload.get("categories") or {}
    for k, v in list(cats.items())[:16]:
        lines.append(f"- `{k}`: **{v}**")

    lines.extend(["", "## Likely blocking (stabilization-critical heuristics)", ""])
    blocking = payload.get("likely_blocking_for_stabilization") or []
    if not blocking:
        lines.append("- *(none classified as blocking)*")
    else:
        by_cat: dict[str, list[str]] = {}
        for b in blocking:
            c = str(b.get("category") or "other")
            by_cat.setdefault(c, []).append(str(b.get("test_id") or ""))
        for cat in sorted(by_cat.keys(), key=lambda x: (-len(by_cat[x]), x)):
            lines.append(f"- **{cat}** ({len(by_cat[cat])}): `{by_cat[cat][0]}` …")

    lines.extend(["", "## Likely unrelated / doc drift (heuristic)", ""])
    kc = payload.get("known_failure_candidates") or []
    if not kc:
        lines.append("- *(none flagged as known drift candidates)*")
    else:
        for row in kc[:12]:
            lines.append(f"- `{row.get('test_id')}` — {row.get('note') or row.get('category')}")

    lines.extend(["", "## Top file clusters", ""])
    grouped = payload.get("grouped_failures") or {}
    top = sorted(grouped.items(), key=lambda x: (-len(x[1]), x[0]))[:12]
    for mod, ids in top:
        lines.append(f"- `{mod}` — **{len(ids)}** failure(s)")

    lines.extend(["", "## Suggested next targets", ""])
    for s in payload.get("suggested_next_targets") or []:
        lines.append(f"- {s}")

    lines.extend(["", f"*Schema:* `{payload.get('schema')}` · *run_id:* `{payload.get('run_id')}`"])
    return "\n".join(lines) + "\n"


__all__ = [
    "TEST_SUITE_HEALTH_SCHEMA",
    "FailureRecord",
    "build_health_payload",
    "classify_failure",
    "parse_junit_xml",
    "parse_pytest_text",
    "render_test_suite_health_terminal",
    "run_pytest_junit",
    "test_suite_health_dir",
    "write_test_suite_health_artifact",
]
