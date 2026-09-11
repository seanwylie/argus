"""Security angle — bounded static inventory (lockfiles, manifests, local policy hints).

Not a vulnerability scan; no network; no dependency databases.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

# Repo root + product root only; fixed names (no directory walks).
_LOCKFILE_BASENAMES: tuple[str, ...] = (
    "package-lock.json",
    "npm-shrinkwrap.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "Cargo.lock",
    "poetry.lock",
    "Pipfile.lock",
    "uv.lock",
    "go.sum",
    "composer.lock",
)
_MANIFEST_BASENAMES: tuple[str, ...] = (
    "package.json",
    "pyproject.toml",
    "requirements.txt",
    "Cargo.toml",
    "go.mod",
    "composer.json",
)

_GITIGNORE_READ_MAX = 4096
_GITIGNORE_HINT_MARKERS: tuple[tuple[str, str], ...] = (
    (".env", "dotenv"),
    ("secret", "secrets"),
    ("credential", "credentials"),
    (".pem", "pem"),
    ("id_rsa", "ssh_key"),
    ("passwd", "password_file"),
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


def _gitignore_hygiene_hints(repo_root: Path) -> list[str]:
    """Bounded read of root ``.gitignore`` for common secret-related patterns (presence only)."""
    p = repo_root / ".gitignore"
    if not p.is_file():
        return []
    try:
        chunk = p.read_text(encoding="utf-8", errors="replace")[:_GITIGNORE_READ_MAX]
    except OSError:
        return []
    low = chunk.lower()
    hints: list[str] = []
    for needle, label in _GITIGNORE_HINT_MARKERS:
        if needle.lower() in low:
            hints.append(label)
    return sorted(set(hints))[:12]


def _approval_record_json_count(repo_root: Path) -> int:
    d = repo_root / "runs" / "approval" / "records"
    if not d.is_dir():
        return 0
    n = 0
    for p in sorted(d.iterdir()):
        if p.is_file() and p.suffix.lower() == ".json":
            n += 1
            if n >= 64:
                break
    return n


def _autonomy_policy_present(repo_root: Path) -> bool:
    p = repo_root / "runs" / "autonomy" / "autonomy.json"
    return p.is_file()


def fingerprint_security_inputs(repo_root: Path, product_root: Path) -> str:
    root = repo_root.resolve()
    pr = product_root.resolve()
    parts: list[str] = []
    for name in sorted(set(_LOCKFILE_BASENAMES + _MANIFEST_BASENAMES)):
        for label, base in (("root", root), ("prod", pr)):
            p = base / name
            if p.is_file():
                parts.append(f"{label}:{name}:{_mtime_ns(p)}:{_safe_size(p)}")
    gi = root / ".gitignore"
    if gi.is_file():
        parts.append(f"gitignore:{_mtime_ns(gi)}:{_safe_size(gi)}")
    parts.append(f"approval_json:{_approval_record_json_count(root)}")
    parts.append(f"autonomy_json:{int(_autonomy_policy_present(root))}")
    raw = "|".join(sorted(parts)) if parts else "empty"
    return hashlib.sha256(raw.encode()).hexdigest()[:20]


def run_security_angle(repo_root: Path, product_root: Path) -> tuple[dict[str, Any], str]:
    fp = fingerprint_security_inputs(repo_root, product_root)
    root = repo_root.resolve()
    pr = product_root.resolve()

    lockfiles: list[str] = []
    manifests: list[str] = []
    for name in _LOCKFILE_BASENAMES:
        for label, base in (("root", root), ("prod", pr)):
            p = base / name
            if p.is_file():
                lockfiles.append(f"{label}:{name}")
    for name in _MANIFEST_BASENAMES:
        for label, base in (("root", root), ("prod", pr)):
            p = base / name
            if p.is_file():
                manifests.append(f"{label}:{name}")
    lockfiles = sorted(set(lockfiles))[:24]
    manifests = sorted(set(manifests))[:24]

    hints = _gitignore_hygiene_hints(root)
    appr = _approval_record_json_count(root)
    auton = _autonomy_policy_present(root)

    lines: list[str] = [
        f"security: lockfiles_found={len(lockfiles)}",
        f"security: manifests_found={len(manifests)}",
        f"security: gitignore_secret_hints={','.join(hints) if hints else 'none'}",
        f"security: approval_records_json~{appr} (runs/approval/records/)",
        f"security: autonomy_policy_present={'yes' if auton else 'no'} (runs/autonomy/autonomy.json)",
    ]
    if lockfiles:
        lines.append(f"security: lockfile_paths={','.join(lockfiles[:6])}{'…' if len(lockfiles) > 6 else ''}")
    else:
        lines.append("security: no known lockfiles at repo or product root (static inventory)")
    lines = lines[:8]

    # Honest status: partial when no dependency pinning files and no manifests — limited visibility only.
    has_dep_surface = bool(lockfiles or manifests)
    angle_status = "active" if has_dep_surface else "partial"

    payload: dict[str, Any] = {
        "schema": "argus.audit_angle.security.v1",
        "angle_status": angle_status,
        "summary_lines": lines,
        "lockfiles_found": lockfiles,
        "manifests_found": manifests,
        "gitignore_secret_hint_labels": hints,
        "approval_records_json_count": appr,
        "autonomy_policy_present": auton,
        "notes": [
            "static inventory at repo and product roots only; not a vulnerability or penetration assessment",
        ],
    }
    return payload, fp
