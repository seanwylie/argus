"""Quality angle — local CI / test tooling signals (shallow)."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any


def _mtime_ns(p: Path) -> int:
    try:
        return p.stat().st_mtime_ns
    except OSError:
        return 0


def fingerprint_quality_inputs(repo_root: Path, product_root: Path) -> str:
    parts: list[str] = []
    root = repo_root.resolve()
    pr = product_root.resolve()
    for name in ("pyproject.toml", "package.json", "Cargo.toml", "tox.ini", "pytest.ini", "setup.cfg"):
        p = root / name
        if p.is_file():
            parts.append(f"root:{name}:{_mtime_ns(p)}")
        p2 = pr / name
        if p2.is_file() and p2 != p:
            parts.append(f"prod:{name}:{_mtime_ns(p2)}")
    wf = root / ".github" / "workflows"
    if wf.is_dir():
        n = 0
        sm = 0
        for p in sorted(wf.glob("*.yml")) + sorted(wf.glob("*.yaml")):
            if p.is_file():
                n += 1
                sm += _mtime_ns(p)
        parts.append(f"workflows:{n}:{sm}")
    raw = "|".join(sorted(parts)) or "empty"
    return hashlib.sha256(raw.encode()).hexdigest()[:20]


def run_quality_angle(repo_root: Path, product_root: Path) -> tuple[dict[str, Any], str]:
    fp = fingerprint_quality_inputs(repo_root, product_root)
    root = repo_root.resolve()
    pr = product_root.resolve()

    present: list[str] = []
    for name in ("pyproject.toml", "package.json", "Cargo.toml"):
        if (root / name).is_file() or (pr / name).is_file():
            present.append(name)

    wf = root / ".github" / "workflows"
    n_wf = 0
    if wf.is_dir():
        n_wf = len([p for p in wf.iterdir() if p.is_file() and p.suffix in (".yml", ".yaml")])

    lines = [
        f"quality: config_files={','.join(present) or 'none'}",
        f"quality: ci_workflow_files ~{n_wf}",
    ]
    if n_wf == 0:
        lines.append("quality: no CI workflows detected under .github/workflows (repo root)")
    lines = lines[:8]

    payload: dict[str, Any] = {
        "schema": "argus.audit_angle.quality.v1",
        "angle_status": "active",
        "summary_lines": lines,
        "config_files_present": present,
        "github_workflow_file_count": n_wf,
    }
    return payload, fp
