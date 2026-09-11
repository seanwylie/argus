"""Performance angle — declared budgets and local performance-adjacent signals only.

No benchmarking, no network, no measured latency claims. Scans bounded local text
for timeouts, caching, concurrency, and similar markers; optionally notes timing-shaped
keys in existing JSON artifacts under ``runs/`` when present.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any

# Coarse substrings (lowercased text); counts are hygiene signals, not diagnoses.
_PERF_SUBSTRINGS: tuple[str, ...] = (
    "timeout",
    "timeouts",
    "deadline",
    "latency",
    "latencies",
    "slo",
    "p95",
    "p99",
    "millisecond",
    "milliseconds",
    "throttle",
    "throttling",
    "rate_limit",
    "ratelimit",
    "cache",
    "caching",
    "batch",
    "batching",
    "concurrency",
    "parallel",
    "pool",
    "pooled",
    "queue",
    "worker",
    "workers",
    "backoff",
    "retry",
    "retries",
)

# Filenames to check at repo root and product root only (no directory walks).
_CONFIG_BASENAMES: tuple[str, ...] = (
    "pyproject.toml",
    "package.json",
    "docker-compose.yml",
    "docker-compose.yaml",
    "Dockerfile",
    "nginx.conf",
    ".env.example",
)

_READ_LIMIT_YAML = 200_000
_READ_LIMIT_CONFIG = 24_000
_READ_LIMIT_DOC = 8_000
_READ_LIMIT_WORKFLOW = 6_000
_READ_LIMIT_JSON = 48_000
_MAX_DOC_FILES = 14
_MAX_WORKFLOW_FILES = 10
_MAX_SUMMARY_JSON_FILES = 10

_TIME_LITERAL_RE = re.compile(r"\b\d+(?:\.\d+)?\s*(?:ms|s|sec|seconds|minutes|m)\b", re.I)


def _mtime_ns(p: Path) -> int:
    try:
        return p.stat().st_mtime_ns
    except OSError:
        return 0


def _safe_size(p: Path) -> int:
    try:
        return p.stat().st_size if p.is_file() else 0
    except OSError:
        return 0


def _read_bounded(p: Path, limit: int) -> str:
    try:
        return p.read_text(encoding="utf-8", errors="replace")[:limit]
    except OSError:
        return ""


def _marker_score(text: str) -> int:
    low = text.lower()
    return sum(low.count(s) for s in _PERF_SUBSTRINGS)


def _time_literal_count(text: str) -> int:
    return len(_TIME_LITERAL_RE.findall(text))


def _timing_key_ish(key: str) -> bool:
    """Avoid bare ``\"ms\"`` (matches ``items``); key-name surface only."""
    ks = str(key).lower()
    if any(
        x in ks
        for x in (
            "duration",
            "elapsed",
            "latency",
            "timing",
            "millis",
            "milliseconds",
            "seconds",
            "timeout",
        )
    ):
        return True
    if ks.endswith("_ms") or ks.endswith("duration") or "duration_" in ks:
        return True
    return False


def _timing_key_surface(obj: Any, depth: int = 0) -> bool:
    """True if dict keys (shallow + one nested dict) look timing-related — not a measurement."""
    if depth > 2:
        return False
    if not isinstance(obj, dict):
        return False
    for k, v in obj.items():
        if _timing_key_ish(k):
            return True
        if isinstance(v, dict) and _timing_key_surface(v, depth + 1):
            return True
    return False


def _metrics_name_hints(product_root: Path) -> tuple[int, int]:
    """Returns (file_count, files_with_perfish_basename)."""
    d = product_root / "metrics"
    if not d.is_dir():
        return 0, 0
    n_files = 0
    n_hints = 0
    for p in sorted(d.rglob("*")):
        if not p.is_file():
            continue
        n_files += 1
        if n_files > 64:
            break
        name = p.name.lower()
        if any(x in name for x in ("perf", "latency", "timing", "duration", "slow")):
            n_hints += 1
    return n_files, n_hints


def _scan_summary_json_timing_surfaces(repo_root: Path) -> tuple[int, int]:
    """
    Bounded scan of ``runs/**/summary.json`` for timing-related key names only.

    Returns (files_read, files_with_timing_key_surface). Uses a bounded ``os.walk``
    (first ``_MAX_SUMMARY_JSON_FILES`` files found) — not a full-tree inventory.
    """
    base = repo_root / "runs"
    if not base.is_dir():
        return 0, 0
    paths: list[Path] = []
    for dirpath, _, filenames in os.walk(base):
        if "summary.json" in filenames:
            p = Path(dirpath) / "summary.json"
            if p.is_file():
                paths.append(p)
                if len(paths) >= _MAX_SUMMARY_JSON_FILES:
                    break
    paths = sorted(paths)
    files_read = len(paths)
    with_surface = 0
    for p in paths:
        try:
            raw = p.read_text(encoding="utf-8", errors="replace")[:_READ_LIMIT_JSON]
            data = json.loads(raw)
        except (OSError, json.JSONDecodeError):
            continue
        if _timing_key_surface(data):
            with_surface += 1
    return files_read, with_surface


def _fingerprint_parts(repo_root: Path, product_root: Path) -> list[str]:
    root = repo_root.resolve()
    pr = product_root.resolve()
    parts: list[str] = []
    for label, base in (("root", root), ("prod", pr)):
        for name in _CONFIG_BASENAMES:
            p = base / name
            if p.is_file():
                parts.append(f"{label}:{name}:{_mtime_ns(p)}:{_safe_size(p)}")
    for rel in ("product.yaml", "doctrine.yaml"):
        p = pr / rel
        if p.is_file():
            parts.append(f"yaml:{rel}:{_mtime_ns(p)}:{_safe_size(p)}")
    dd = root / "docs"
    if dd.is_dir():
        sm = 0
        n = 0
        for p in sorted(dd.glob("*.md"))[:_MAX_DOC_FILES]:
            if p.is_file():
                n += 1
                sm += _mtime_ns(p)
        parts.append(f"docs_md:{n}:{sm}")
    wf = root / ".github" / "workflows"
    if wf.is_dir():
        n = 0
        sm = 0
        for p in sorted(wf.glob("*.yml")) + sorted(wf.glob("*.yaml")):
            if p.is_file():
                n += 1
                sm += _mtime_ns(p)
                if n >= _MAX_WORKFLOW_FILES:
                    break
        parts.append(f"workflows:{n}:{sm}")
    md = pr / "metrics"
    if md.is_dir():
        parts.append(f"metrics_dir:{int(md.is_dir())}")
    runs = root / "runs"
    if runs.is_dir():
        parts.append(f"runs_dir:{_mtime_ns(runs)}")
    return parts


def fingerprint_performance_inputs(repo_root: Path, product_root: Path) -> str:
    raw = "|".join(sorted(_fingerprint_parts(repo_root, product_root))) or "empty"
    return hashlib.sha256(raw.encode()).hexdigest()[:20]


def run_performance_angle(repo_root: Path, product_root: Path) -> tuple[dict[str, Any], str]:
    fp = fingerprint_performance_inputs(repo_root, product_root)
    root = repo_root.resolve()
    pr = product_root.resolve()

    py_text = _read_bounded(pr / "product.yaml", _READ_LIMIT_YAML)
    doc_text = _read_bounded(pr / "doctrine.yaml", _READ_LIMIT_YAML) if (pr / "doctrine.yaml").is_file() else ""

    m_py = _marker_score(py_text)
    m_doc = _marker_score(doc_text)
    t_py = _time_literal_count(py_text)

    cfg_hits = 0
    cfg_files = 0
    for label, base in (("root", root), ("prod", pr)):
        for name in _CONFIG_BASENAMES:
            p = base / name
            if not p.is_file():
                continue
            cfg_files += 1
            cfg_hits += _marker_score(_read_bounded(p, _READ_LIMIT_CONFIG))

    docs_scanned = 0
    doc_hits = 0
    dd = root / "docs"
    if dd.is_dir():
        for p in sorted(dd.glob("*.md"))[:_MAX_DOC_FILES]:
            if not p.is_file():
                continue
            docs_scanned += 1
            doc_hits += _marker_score(_read_bounded(p, _READ_LIMIT_DOC))

    wf_hits = 0
    wf_files = 0
    wf_dir = root / ".github" / "workflows"
    if wf_dir.is_dir():
        for p in sorted(wf_dir.glob("*.yml")) + sorted(wf_dir.glob("*.yaml")):
            if not p.is_file():
                continue
            wf_files += 1
            if wf_files > _MAX_WORKFLOW_FILES:
                break
            wf_hits += _marker_score(_read_bounded(p, _READ_LIMIT_WORKFLOW))

    met_files, met_hints = _metrics_name_hints(pr)
    sum_read, sum_timing = _scan_summary_json_timing_surfaces(root)

    total_markers = m_py + m_doc + cfg_hits + doc_hits + wf_hits
    total_signals = total_markers + t_py + met_hints + sum_timing

    lines: list[str] = [
        f"performance: yaml_marker_hits product={m_py} doctrine={m_doc}",
        f"performance: time_like_literals_in_product_yaml≈{t_py} (declared text, not measured)",
        f"performance: config_files_scanned≈{cfg_files} marker_hits≈{cfg_hits}",
        f"performance: docs_md_scanned={docs_scanned} marker_hits≈{doc_hits}",
        f"performance: ci_workflows_scanned≈{wf_files} marker_hits≈{wf_hits}",
        f"performance: metrics_files≈{met_files} basename_hints≈{met_hints}",
        f"performance: runs_summary_json_scanned≈{sum_read} timing_key_surface≈{sum_timing} (keys only, not measured)",
    ]

    has_evidence = total_signals > 0
    angle_status = "active" if has_evidence else "partial"

    notes = [
        "declared markers and local file signals only; substring counts are coarse and not diagnoses",
        "timing_key_surface refers to JSON key names in existing artifacts, not observed runtime",
    ]

    payload: dict[str, Any] = {
        "schema": "argus.audit_angle.performance.v1",
        "angle_status": angle_status,
        "summary_lines": lines[:8],
        "notes": notes,
        "counts": {
            "markers_product_yaml": m_py,
            "markers_doctrine_yaml": m_doc,
            "time_like_literals_product_yaml": t_py,
            "config_marker_hits": cfg_hits,
            "docs_md_files": docs_scanned,
            "docs_marker_hits": doc_hits,
            "workflow_files": wf_files,
            "workflow_marker_hits": wf_hits,
            "metrics_files": met_files,
            "metrics_basename_hints": met_hints,
            "runs_summary_json_files": sum_read,
            "runs_summary_json_timing_key_surface": sum_timing,
        },
    }
    return payload, fp
