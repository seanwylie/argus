"""UX evidence angle — bounded static inventory (docs, paths, filenames).

Not a UX quality assessment; no screenshots, network, or interpretation of taste.
Reports presence of local artifacts that *may* support UX work (structure, docs, tests).
"""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path
from typing import Any

from argus.audit.scanner import default_scope_roots
from argus.products.loader import load_yaml_file
from argus.products.paths import join_under_product

_SKIP_DIR_NAMES = frozenset(
    {
        ".git",
        "__pycache__",
        ".venv",
        "venv",
        "node_modules",
        "dist",
        "build",
        ".egg-info",
    }
)
_MAX_DEPTH = 5
_MAX_TOTAL_FILES = 360
_READ_CHUNK = 6144
_MAX_MD_FILES_TO_SCAN = 40

# Documentation lines (substring match, lowercased) — topic presence only, not merit.
_DOC_TOPIC_MARKERS: tuple[str, ...] = (
    "onboarding",
    "navigation",
    "accessibility",
    "a11y",
    "user flow",
    "design system",
    "routing",
    "empty state",
    "i18n",
    "l10n",
    "responsive",
    "wcag",
    "aria",
)

# Path/filename signals for “UX structure / tooling” evidence (presence only).
_COMPONENT_DIR_RE = re.compile(r"(^|/)components(/|$)", re.I)
_ROUTES_BASENAMES: frozenset[str] = frozenset(
    {
        "routes.tsx",
        "routes.ts",
        "routes.jsx",
        "routes.js",
        "_routes.ts",
        "_routes.tsx",
    }
)
_TAILWIND_PREFIX = "tailwind.config."
_TOKEN_BASENAMES: frozenset[str] = frozenset(
    {
        "tokens.json",
        "design-tokens.json",
        "theme.ts",
        "theme.js",
    }
)

# Test / tooling filename hints (bounded inventory).
_TEST_NAME_SUBSTR: tuple[str, ...] = (
    "e2e",
    "a11y",
    "accessibility",
    "playwright",
    "cypress",
    "vitest",
    "storybook",
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


def _load_optional_audit_ux_roots(product_root: Path) -> list[str]:
    """Optional ``audit.ux.scope_roots`` in ``product.yaml`` (repo-relative paths under product)."""
    cfg = product_root / "product.yaml"
    raw, err = load_yaml_file(cfg)
    if err or not raw:
        return []
    audit = raw.get("audit")
    if not isinstance(audit, dict):
        return []
    ux = audit.get("ux")
    if not isinstance(ux, dict):
        return []
    roots = ux.get("scope_roots")
    if roots is None:
        roots = ux.get("roots")
    if not isinstance(roots, list):
        return []
    out: list[str] = []
    for item in roots:
        if not isinstance(item, str):
            continue
        s = item.strip().strip("/")
        if not s or s == ".":
            continue
        try:
            join_under_product(product_root, s)
        except ValueError:
            continue
        out.append(s)
    # Stable unique order
    seen: set[str] = set()
    unique: list[str] = []
    for r in out:
        if r not in seen:
            seen.add(r)
            unique.append(r)
    return unique


def _merge_scan_roots(product_root: Path, declared: list[str]) -> list[str]:
    auto = default_scope_roots(product_root)
    extras = ("web", "frontend", "ui", "packages")
    merged: list[str] = []
    for r in list(declared) + list(auto) + list(extras):
        if r in merged:
            continue
        p = product_root / r
        if p.exists():
            merged.append(r)
    if not merged:
        merged = ["."]
    return merged


def _bounded_relpaths(product_root: Path, roots: list[str]) -> tuple[list[str], list[str]]:
    """Return relative posix paths under ``product_root`` (bounded total file count)."""
    scanned: list[str] = []
    excluded: list[str] = []
    root_res = product_root.resolve()
    count = 0
    for rel in roots:
        base = (product_root / rel).resolve()
        try:
            if not str(base).startswith(str(root_res)):
                excluded.append(f"skip_outside:{rel}")
                continue
        except Exception:
            continue
        if base.is_file():
            try:
                scanned.append(base.relative_to(product_root).as_posix())
            except ValueError:
                pass
            count += 1
            if count >= _MAX_TOTAL_FILES:
                excluded.append(f"cap:max_files={_MAX_TOTAL_FILES}")
                return scanned, excluded
            continue
        if not base.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(base, topdown=True):
            dp = Path(dirpath)
            try:
                rel_depth = len(dp.relative_to(product_root).parts)
            except ValueError:
                continue
            if rel_depth > _MAX_DEPTH:
                dirnames[:] = []
                continue
            dirnames[:] = [d for d in dirnames if d not in _SKIP_DIR_NAMES]
            for fn in filenames:
                fp = dp / fn
                try:
                    scanned.append(fp.relative_to(product_root).as_posix())
                except ValueError:
                    continue
                count += 1
                if count >= _MAX_TOTAL_FILES:
                    excluded.append(f"cap:max_files={_MAX_TOTAL_FILES}")
                    return scanned, excluded
    return scanned, excluded


def _md_topic_hits(product_root: Path, rel_paths: list[str]) -> tuple[int, int]:
    """Return (markdown_files_scanned, files_with_at_least_one_topic_marker_line)."""
    md_files = sorted([r for r in rel_paths if r.lower().endswith(".md")])[:_MAX_MD_FILES_TO_SCAN]
    hits = 0
    for rel in md_files:
        p = product_root / rel
        if not p.is_file():
            continue
        try:
            chunk = p.read_text(encoding="utf-8", errors="replace")[:_READ_CHUNK]
        except OSError:
            continue
        low = chunk.lower()
        if any(m in low for m in _DOC_TOPIC_MARKERS):
            hits += 1
    return len(md_files), hits


def _structure_signals(rel_paths: list[str]) -> list[str]:
    sig: set[str] = set()
    for rel in rel_paths:
        if _COMPONENT_DIR_RE.search(rel.replace("\\", "/")):
            sig.add("path_segment_components")
        base = rel.split("/")[-1].lower()
        if base in _ROUTES_BASENAMES:
            sig.add(f"routes_like_file:{base}")
        if base in _TOKEN_BASENAMES:
            sig.add(f"token_theme_file:{base}")
        low = rel.lower()
        if low.startswith(_TAILWIND_PREFIX) or "/" + _TAILWIND_PREFIX in low:
            sig.add("tailwind_config_like")
    return sorted(sig)[:16]


def _test_tooling_hits(rel_paths: list[str]) -> list[str]:
    out: list[str] = []
    for rel in rel_paths:
        lower = rel.lower()
        if any(s in lower for s in _TEST_NAME_SUBSTR):
            out.append(rel)
    return sorted(set(out))[:20]


def fingerprint_ux_inputs(repo_root: Path, product_root: Path) -> str:
    """Stable hash over declared roots + bounded file inventory."""
    root = repo_root.resolve()
    pr = product_root.resolve()
    parts: list[str] = []
    cfg = pr / "product.yaml"
    if cfg.is_file():
        parts.append(f"py:{_mtime_ns(cfg)}:{_safe_size(cfg)}")
    declared = _load_optional_audit_ux_roots(pr)
    parts.append(f"declared:{','.join(declared)}")
    roots = _merge_scan_roots(pr, declared)
    rels, _ = _bounded_relpaths(pr, roots)
    for rel in sorted(rels)[:400]:
        p = pr / rel
        parts.append(f"f:{rel}:{_mtime_ns(p)}:{_safe_size(p)}")
    # Repo-level UX docs (optional): root README only
    readme = root / "README.md"
    if readme.is_file():
        parts.append(f"readme:{_mtime_ns(readme)}:{_safe_size(readme)}")
    raw = "|".join(parts) if parts else "empty"
    return hashlib.sha256(raw.encode()).hexdigest()[:20]


def run_ux_angle(repo_root: Path, product_root: Path) -> tuple[dict[str, Any], str]:
    fp = fingerprint_ux_inputs(repo_root, product_root)
    root = repo_root.resolve()
    pr = product_root.resolve()

    declared = _load_optional_audit_ux_roots(pr)
    roots = _merge_scan_roots(pr, declared)
    rel_paths, excluded = _bounded_relpaths(pr, roots)

    md_scanned, md_hits = _md_topic_hits(pr, rel_paths)
    struct = _structure_signals(rel_paths)
    tests = _test_tooling_hits(rel_paths)

    readme_hit = False
    rdm = root / "README.md"
    if rdm.is_file():
        try:
            chunk = rdm.read_text(encoding="utf-8", errors="replace")[:_READ_CHUNK]
            low = chunk.lower()
            readme_hit = any(m in low for m in _DOC_TOPIC_MARKERS)
        except OSError:
            pass

    doc_files_with_hits = md_hits + (1 if readme_hit else 0)
    doc_scanned_total = md_scanned + (1 if rdm.is_file() else 0)

    lines: list[str] = [
        "ux: evidence=bounded local inventory only (not a judgment of UX quality)",
        f"ux: declared_audit.ux.scope_roots={len(declared)}"
        + (f" ({','.join(declared[:4])}{'…' if len(declared) > 4 else ''})" if declared else ""),
        f"ux: markdown_topic_scan_files~{doc_scanned_total} with_topic_marker_hits~{doc_files_with_hits} (presence only)",
        f"ux: structure_path_or_filename_signals={len(struct)} ({','.join(struct[:4])}{'…' if len(struct) > 4 else ''})"
        if struct
        else "ux: structure_path_or_filename_signals=0",
        f"ux: ui_test_or_tooling_paths~{len(tests)} (filename/path substring matches)",
    ]
    if excluded:
        lines.append(f"ux: scan_notes={excluded[0]}")
    lines = [ln for ln in lines if ln][:8]

    has_evidence = bool(
        declared
        or doc_files_with_hits > 0
        or struct
        or tests
    )
    angle_status = "active" if has_evidence else "partial"

    payload: dict[str, Any] = {
        "schema": "argus.audit_angle.ux.v1",
        "angle_status": angle_status,
        "summary_lines": lines,
        "declared_scope_roots": declared,
        "markdown_files_scanned_approx": doc_scanned_total,
        "markdown_topic_marker_hit_files_approx": doc_files_with_hits,
        "repo_readme_topic_hit": readme_hit,
        "structure_signals": struct,
        "ui_test_or_tooling_paths": tests,
        "notes": [
            "evidence of UX-related structure or documentation support in-repo; markers are topic presence only",
            "does not assess clarity, aesthetics, or real-world usability",
        ],
    }
    return payload, fp
