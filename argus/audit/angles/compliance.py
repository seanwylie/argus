"""Compliance angle — bounded local evidence only (files, markers, gating artifacts).

Does not assess legal or regulatory compliance; reports presence/absence of repo-local signals only.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

_READ_CHUNK = 8192
_DOCS_MAX_MD = 24
_DOCS_MAX_DEPTH = 4
_TOTAL_READ_CAP = 120_000

# Filenames (repo or product root) — existence checks only.
_GOVERNANCE_NAMES: tuple[str, ...] = (
    "CODEOWNERS",
    "SECURITY.md",
    "GOVERNANCE.md",
    "PRIVACY.md",
    "COMPLIANCE.md",
    "CONTRIBUTING.md",
)

_GITHUB_PATHS: tuple[str, ...] = (
    ".github/CODEOWNERS",
    ".github/PULL_REQUEST_TEMPLATE.md",
    ".github/pull_request_template.md",
)

# Substrings in product.yaml / doctrine.yaml (lowercase match) — "marker" only, not a claim.
_YAML_MARKERS: tuple[str, ...] = (
    "constraint",
    "policy",
    "governance",
    "approval",
    "autonomy",
    "audit",
    "retention",
    "privacy",
    "pii",
    "logging",
    "encrypt",
    "gdpr",
    "soc",
)

# Markdown heading lines (line start with #) — substring match after lowercasing the line.
_DOC_HEADING_HINTS: tuple[str, ...] = (
    "privacy",
    "security",
    "compliance",
    "governance",
    "policy",
    "audit",
    "retention",
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


def _read_bounded(p: Path, limit: int) -> str:
    if not p.is_file():
        return ""
    try:
        return p.read_text(encoding="utf-8", errors="replace")[:limit]
    except OSError:
        return ""


def _yaml_markers(text: str) -> list[str]:
    low = text.lower()
    found = [m for m in _YAML_MARKERS if m in low]
    return sorted(set(found))[:16]


def _approval_json_count(repo_root: Path) -> int:
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


def _workflow_yml_count(repo_root: Path) -> int:
    wd = repo_root / ".github" / "workflows"
    if not wd.is_dir():
        return 0
    n = 0
    for p in sorted(wd.iterdir()):
        if p.is_file() and p.suffix.lower() in (".yml", ".yaml"):
            n += 1
            if n >= 24:
                break
    return n


def _cursor_rules_count(repo_root: Path) -> int:
    d = repo_root / ".cursor" / "rules"
    if not d.is_dir():
        return 0
    n = 0
    for p in d.iterdir():
        if p.is_file() and p.suffix.lower() in (".mdc", ".md"):
            n += 1
            if n >= 48:
                break
    return n


def _bounded_doc_md_paths(docs_root: Path) -> list[Path]:
    if not docs_root.is_dir():
        return []
    out: list[Path] = []
    for p in sorted(docs_root.rglob("*.md")):
        if len(out) >= _DOCS_MAX_MD:
            break
        try:
            rel = p.relative_to(docs_root)
        except ValueError:
            continue
        if len(rel.parts) > _DOCS_MAX_DEPTH:
            continue
        out.append(p)
    return out


def _doc_heading_hits(paths: list[Path]) -> int:
    """Count markdown lines that look like headings and contain hint words (bounded reads)."""
    total_hits = 0
    bytes_read = 0
    for p in paths:
        if bytes_read >= _TOTAL_READ_CAP:
            break
        chunk = _read_bounded(p, _READ_CHUNK)
        bytes_read += len(chunk.encode("utf-8", errors="replace"))
        for line in chunk.splitlines():
            s = line.strip()
            if not s.startswith("#"):
                continue
            low = s.lower()
            if any(h in low for h in _DOC_HEADING_HINTS):
                total_hits += 1
    return total_hits


def _collect_present_files(base: Path, rels: tuple[str, ...]) -> list[str]:
    out: list[str] = []
    for rel in rels:
        if (base / rel).is_file():
            out.append(rel)
    return sorted(out)


def fingerprint_compliance_inputs(repo_root: Path, product_root: Path) -> str:
    root = repo_root.resolve()
    pr = product_root.resolve()
    parts: list[str] = []

    for label, base in (("root", root), ("prod", pr)):
        for name in _GOVERNANCE_NAMES:
            p = base / name
            if p.is_file():
                parts.append(f"{label}:gov:{name}:{_mtime_ns(p)}:{_safe_size(p)}")
        for rel in _GITHUB_PATHS:
            p = base / rel
            if p.is_file():
                parts.append(f"{label}:gh:{rel}:{_mtime_ns(p)}:{_safe_size(p)}")

    parts.append(f"workflows:{_workflow_yml_count(root)}")
    parts.append(f"approval_json:{_approval_json_count(root)}")
    auton = root / "runs" / "autonomy" / "autonomy.json"
    parts.append(f"autonomy_json:{int(auton.is_file())}:{_mtime_ns(auton)}")
    parts.append(f"cursor_rules:{_cursor_rules_count(root)}")

    for label, base in (("prod", pr),):
        for fn in ("product.yaml", "doctrine.yaml"):
            p = base / fn
            if p.is_file():
                txt = _read_bounded(p, _READ_CHUNK * 2)
                mk = ",".join(_yaml_markers(txt))
                parts.append(f"{label}:{fn}:{_mtime_ns(p)}:{mk}")

    docs_root = root / "docs"
    if docs_root.is_dir():
        md_paths = _bounded_doc_md_paths(docs_root)
        hits = _doc_heading_hits(md_paths)
        parts.append(f"docs_heading_hits:{hits}")
        for p in md_paths[:8]:
            parts.append(f"doc:{p.as_posix()}:{_mtime_ns(p)}")

    raw = "|".join(sorted(parts)) if parts else "empty"
    return hashlib.sha256(raw.encode()).hexdigest()[:20]


def run_compliance_angle(repo_root: Path, product_root: Path) -> tuple[dict[str, Any], str]:
    fp = fingerprint_compliance_inputs(repo_root, product_root)
    root = repo_root.resolve()
    pr = product_root.resolve()

    gov_root = _collect_present_files(root, _GOVERNANCE_NAMES)
    gov_prod = _collect_present_files(pr, _GOVERNANCE_NAMES)
    gh_root = _collect_present_files(root, _GITHUB_PATHS)
    gh_prod = _collect_present_files(pr, _GITHUB_PATHS)

    wf = _workflow_yml_count(root)
    appr = _approval_json_count(root)
    auton_present = (root / "runs" / "autonomy" / "autonomy.json").is_file()
    cursor_n = _cursor_rules_count(root)

    prod_yaml = pr / "product.yaml"
    doc_yaml = pr / "doctrine.yaml"
    markers_prod = _yaml_markers(_read_bounded(prod_yaml, _READ_CHUNK * 2)) if prod_yaml.is_file() else []
    markers_doc = _yaml_markers(_read_bounded(doc_yaml, _READ_CHUNK * 2)) if doc_yaml.is_file() else []
    markers_union = sorted(set(markers_prod + markers_doc))[:16]

    docs_root = root / "docs"
    md_paths = _bounded_doc_md_paths(docs_root)
    heading_hits = _doc_heading_hits(md_paths)

    # Evidence groups (for status only): disjoint signals.
    groups = 0
    if gov_root or gov_prod or gh_root or gh_prod:
        groups += 1
    if wf > 0:
        groups += 1
    if appr > 0 or auton_present:
        groups += 1
    if markers_union:
        groups += 1
    if heading_hits > 0:
        groups += 1
    if cursor_n > 0:
        groups += 1

    if groups >= 2:
        angle_status = "active"
    else:
        angle_status = "partial"

    lines: list[str] = [
        "compliance: local repo signals only — not legal/regulatory certification",
        f"compliance: governance_named_files repo={len(gov_root)} product={len(gov_prod)}",
        f"compliance: github_review_paths_present={len(gh_root) + len(gh_prod)} workflow_yml~{wf}",
        f"compliance: gating_artifacts autonomy_json={'yes' if auton_present else 'no'} approval_records_json~{appr}",
        f"compliance: product_doctrine_yaml_markers={','.join(markers_union) if markers_union else 'none'}",
        f"compliance: docs_heading_hits~{heading_hits} (bounded markdown scan under docs/)",
        f"compliance: cursor_rules_files~{cursor_n} (.cursor/rules/)",
    ]
    lines = lines[:8]

    payload: dict[str, Any] = {
        "schema": "argus.audit_angle.compliance.v1",
        "angle_status": angle_status,
        "summary_lines": lines,
        "governance_named_files_repo": gov_root,
        "governance_named_files_product": gov_prod,
        "github_review_paths_repo": gh_root,
        "github_review_paths_product": gh_prod,
        "workflow_yml_count": wf,
        "approval_records_json_count": appr,
        "autonomy_json_present": auton_present,
        "yaml_marker_hits": markers_union,
        "docs_heading_hits": heading_hits,
        "docs_markdown_files_scanned": len(md_paths),
        "cursor_rules_file_count": cursor_n,
        "notes": [
            "bounded static inspection; keywords and headings are presence markers, not proof of process or compliance obligations",
        ],
    }
    return payload, fp
