"""Reliability angle — bounded local operational readiness signals.

Not a runtime SLO or availability assessment; no network; static paths only.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

_README_MAX = 8192
_DOCKER_READ_MAX = 12288
_WORKFLOW_READ_MAX = 6144
_CONFIG_READ_MAX = 12288
_DOCS_READ_MAX = 6144
_DOCS_MD_CAP = 6
_SCRIPTS_LIST_CAP = 64
_WORKFLOW_FILES_CAP = 16

# Script basenames suggesting run/stop/verify/ops (shallow filenames only).
_OPS_NAME_HINT = re.compile(
    r"(^|[^a-z])(start|stop|run|test|dev|smoke|health|migrate|deploy|serve|validate|check)([^a-z]|$)",
    re.I,
)

_README_MARKERS: tuple[str, ...] = (
    "operation",
    "runbook",
    "health",
    "deploy",
    "restart",
    "recovery",
    "troubleshoot",
    "uptime",
    "incident",
    "rollback",
)


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


def _read_head(p: Path, limit: int) -> str:
    try:
        return p.read_text(encoding="utf-8", errors="replace")[:limit]
    except OSError:
        return ""


def _scripts_ops_basenames(scripts_dir: Path) -> list[str]:
    if not scripts_dir.is_dir():
        return []
    out: list[str] = []
    for p in sorted(scripts_dir.iterdir()):
        if not p.is_file():
            continue
        name = p.name
        if _OPS_NAME_HINT.search(name):
            out.append(name)
        if len(out) >= 24:
            break
    return out


def _readme_operational_hits(repo_or_product: Path) -> tuple[bool, list[str]]:
    hits: list[str] = []
    for rn in ("README.md", "README.rst", "readme.md"):
        p = repo_or_product / rn
        if not p.is_file():
            continue
        text = _read_head(p, _README_MAX).lower()
        for m in _README_MARKERS:
            if m in text:
                hits.append(m)
    hits = sorted(set(hits))[:16]
    return (bool(hits), hits)


def _docs_markdown_operational_hits(docs_dir: Path) -> tuple[bool, list[str]]:
    """Bounded: ``docs/*.md`` only, shallow, first N files by name."""
    if not docs_dir.is_dir():
        return False, []
    hits: list[str] = []
    for p in sorted(docs_dir.glob("*.md"))[:_DOCS_MD_CAP]:
        if not p.is_file():
            continue
        text = _read_head(p, _DOCS_READ_MAX).lower()
        for m in _README_MARKERS:
            if m in text:
                hits.append(m)
    hits = sorted(set(hits))[:16]
    return (bool(hits), hits)


def _dockerfile_signals(path: Path) -> bool:
    if not path.is_file():
        return False
    t = _read_head(path, _DOCKER_READ_MAX)
    return "healthcheck" in t.lower()


def _compose_signals(path: Path) -> tuple[bool, bool]:
    if not path.is_file():
        return False, False
    t = _read_head(path, _DOCKER_READ_MAX).lower()
    return ("healthcheck:" in t, "restart:" in t)


def _workflow_verification_hints(wf_dir: Path) -> tuple[int, list[str]]:
    if not wf_dir.is_dir():
        return 0, []
    paths = sorted(wf_dir.glob("*.yml")) + sorted(wf_dir.glob("*.yaml"))
    paths = [p for p in paths if p.is_file()][: _WORKFLOW_FILES_CAP]
    names: list[str] = []
    n = 0
    for p in paths:
        low = p.name.lower()
        chunk = _read_head(p, _WORKFLOW_READ_MAX).lower()
        hit = (
            "test" in low
            or "smoke" in low
            or "ci" in low
            or "pytest" in chunk
            or "npm test" in chunk
            or "smoke" in chunk
            or "workflow_dispatch" in chunk
        )
        if hit:
            n += 1
            if len(names) < 12:
                names.append(p.name)
    return n, names


def _config_resilience_markers(base: Path) -> tuple[bool, bool, list[str]]:
    """Returns (retry_hit, timeout_hit, file_labels)."""
    retry_hit = False
    timeout_hit = False
    labels: list[str] = []
    for name in ("pyproject.toml", "package.json", "Cargo.toml"):
        p = base / name
        if not p.is_file():
            continue
        t = _read_head(p, _CONFIG_READ_MAX).lower()
        if "retry" in t:
            retry_hit = True
            labels.append(f"{name}:retry")
        if "timeout" in t:
            timeout_hit = True
            labels.append(f"{name}:timeout")
    return retry_hit, timeout_hit, sorted(set(labels))[:12]


def fingerprint_reliability_inputs(repo_root: Path, product_root: Path) -> str:
    parts: list[str] = []
    root = repo_root.resolve()
    pr = product_root.resolve()
    for label, base in (("root", root), ("prod", pr)):
        sd = base / "scripts"
        if sd.is_dir():
            for p in sorted(sd.iterdir())[:_SCRIPTS_LIST_CAP]:
                if p.is_file():
                    parts.append(f"{label}:scripts:{p.name}:{_mtime_ns(p)}:{_safe_size(p)}")
        for rn in ("README.md", "README.rst", "readme.md"):
            rp = base / rn
            if rp.is_file():
                parts.append(f"{label}:{rn.lower()}:{_mtime_ns(rp)}:{_safe_size(rp)}")
        for dn in ("Dockerfile", "dockerfile"):
            dp = base / dn
            if dp.is_file():
                parts.append(f"{label}:dockerfile:{_mtime_ns(dp)}:{_safe_size(dp)}")
        for cn in ("docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml"):
            cp = base / cn
            if cp.is_file():
                parts.append(f"{label}:{cn}:{_mtime_ns(cp)}:{_safe_size(cp)}")
        dd = base / "docs"
        if dd.is_dir():
            for p in sorted(dd.glob("*.md"))[:_DOCS_MD_CAP]:
                if p.is_file():
                    parts.append(f"{label}:docs:{p.name}:{_mtime_ns(p)}:{_safe_size(p)}")
    wf = root / ".github" / "workflows"
    if wf.is_dir():
        seen = 0
        for p in sorted(wf.glob("*.yml")) + sorted(wf.glob("*.yaml")):
            if p.is_file():
                parts.append(f"wf:{p.name}:{_mtime_ns(p)}:{_safe_size(p)}")
                seen += 1
                if seen >= _WORKFLOW_FILES_CAP:
                    break
    for name in ("pyproject.toml", "package.json", "Cargo.toml"):
        for label, base in (("root", root), ("prod", pr)):
            p = base / name
            if p.is_file():
                parts.append(f"{label}:{name}:{_mtime_ns(p)}:{_safe_size(p)}")
    raw = "|".join(sorted(parts)) if parts else "empty"
    return hashlib.sha256(raw.encode()).hexdigest()[:20]


def run_reliability_angle(repo_root: Path, product_root: Path) -> tuple[dict[str, Any], str]:
    fp = fingerprint_reliability_inputs(repo_root, product_root)
    root = repo_root.resolve()
    pr = product_root.resolve()

    ops_root = _scripts_ops_basenames(root / "scripts")
    ops_prod = _scripts_ops_basenames(pr / "scripts")
    ops_union = sorted(set(ops_root + ops_prod))[:24]

    r_readme, readme_hits = _readme_operational_hits(root)
    p_readme, prod_readme_hits = _readme_operational_hits(pr)
    r_docs, docs_root_hits = _docs_markdown_operational_hits(root / "docs")
    p_docs, docs_prod_hits = _docs_markdown_operational_hits(pr / "docs")
    readme_any = r_readme or p_readme or r_docs or p_docs
    readme_hits_combined = sorted(set(readme_hits + prod_readme_hits))[:16]
    docs_hits_combined = sorted(set(docs_root_hits + docs_prod_hits))[:16]

    df_health = False
    compose_hc = False
    compose_restart = False
    for label, base in (("root", root), ("prod", pr)):
        for dn in ("Dockerfile", "dockerfile"):
            if _dockerfile_signals(base / dn):
                df_health = True
        for cn in ("docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml"):
            hc, rst = _compose_signals(base / cn)
            compose_hc = compose_hc or hc
            compose_restart = compose_restart or rst

    wf_n, wf_names = _workflow_verification_hints(root / ".github" / "workflows")

    r_retry, r_timeout, r_labels = _config_resilience_markers(root)
    p_retry, p_timeout, p_labels = _config_resilience_markers(pr)
    cfg_retry = r_retry or p_retry
    cfg_timeout = r_timeout or p_timeout
    cfg_labels = sorted(set(r_labels + p_labels))[:16]

    cat_scripts = len(ops_union) >= 1
    cat_readme = readme_any  # readme, readme at product, or docs/*.md markers
    cat_container = df_health or compose_hc or compose_restart
    cat_ci = wf_n >= 1
    cat_cfg = cfg_retry or cfg_timeout

    score = sum(
        (
            int(cat_scripts),
            int(cat_readme),
            int(cat_container),
            int(cat_ci),
            int(cat_cfg),
        )
    )
    angle_status = "active" if score >= 2 else "partial"

    lines: list[str] = [
        f"reliability: ops_script_name_hints~{len(ops_union)} (repo+product scripts/)",
        f"reliability: readme_or_docs_operational_markers={'yes' if readme_any else 'no'}",
        f"reliability: dockerfile_healthcheck={'yes' if df_health else 'no'}",
        f"reliability: compose_health_or_restart={'yes' if (compose_hc or compose_restart) else 'no'}",
        f"reliability: ci_workflow_verification_hints~{wf_n}",
        f"reliability: config_retry_or_timeout_markers={','.join(cfg_labels) if cfg_labels else 'none'}",
    ]
    if ops_union:
        shown = ",".join(ops_union[:8])
        lines.append(f"reliability: sample_script_names={shown}{'…' if len(ops_union) > 8 else ''}")
    else:
        lines.append("reliability: no script name hints under scripts/ (filenames only)")
    lines = lines[:8]

    payload: dict[str, Any] = {
        "schema": "argus.audit_angle.reliability.v1",
        "angle_status": angle_status,
        "summary_lines": lines,
        "ops_script_basenames_hinted": ops_union,
        "readme_operational_markers_hit": readme_hits_combined,
        "docs_markdown_operational_hits": docs_hits_combined,
        "dockerfile_healthcheck_detected": df_health,
        "compose_healthcheck_detected": compose_hc,
        "compose_restart_policy_detected": compose_restart,
        "ci_workflow_verification_file_count": wf_n,
        "ci_workflow_verification_filenames_sample": wf_names,
        "config_retry_marker_present": cfg_retry,
        "config_timeout_marker_present": cfg_timeout,
        "config_marker_file_hints": cfg_labels,
        "notes": [
            "static signals from bounded paths only; not an error-budget or availability assessment",
        ],
    }
    return payload, fp
