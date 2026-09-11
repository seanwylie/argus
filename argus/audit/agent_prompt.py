"""Render prompts for Cursor (or other agents) to produce structured audit_cursor_scan JSON."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from argus.audit.agent_lens import CURSOR_SCAN_ANGLE_IDS, LENS_BY_ANGLE
from argus.audit.bundle import ANGLE_IDS, load_audit_bundle
from argus.audit.cursor_scan import (
    CURSOR_SCAN_BATCH_SCHEMA,
    CURSOR_SCAN_SCHEMA,
    cursor_scan_contract_prompt_block,
    cursor_scan_single_object_contract_prompt_block,
)
from argus.audit.scanner import resolve_product_paths
from argus.products.inventory import build_inventory

# Bounded, angle-specific repo scan hints (complement :data:`LENS_BY_ANGLE`).
SCAN_FOCUS_BY_ANGLE: dict[str, str] = {
    "product_gap": (
        "- Open `product.yaml`, doctrine paths, and declared `metrics` / `actions` targets; compare to files that exist.\n"
        "- Sample capability rows: cite path + status; flag drift vs deterministic table without re-listing every row."
    ),
    "cost": (
        "- Read `config/economics` and any registry YAML tied to this product id; compare declared `cost.monthly_usd`.\n"
        "- List orphan resource ids or mapping gaps with exact paths—no invented cloud bills."
    ),
    "quality": (
        "- Inspect CI under `.github/workflows`, and `pyproject.toml` / `package.json` scripts for test/lint targets.\n"
        "- Note presence/absence of tooling; do not score subjective code quality."
    ),
    "security": (
        "- Scan lockfiles/manifests, secret-like patterns in tracked files, auth/env usage in app entrypoints.\n"
        "- Cite each concern with file path; avoid claiming exploits or pentest results."
    ),
    "compliance": (
        "- Search workflow YAML and product constraints for retention, PII, approval hooks; match to code comments/config.\n"
        "- Treat gaps as risks only when doctrine or yaml implies an obligation."
    ),
    "reliability": (
        "- Find runbooks, health/readiness checks, retry/backoff in scripts and services; cite file:region or path.\n"
        "- Static only—no uptime claims."
    ),
    "performance": (
        "- Look for declared budgets, obvious N+1 or unbounded loops in hot scripts, large sync I/O.\n"
        "- No fabricated timings; use limitations when unknown."
    ),
    "store_business": (
        "- Trace pricing/store fields in yaml vs any local commerce or experiment files under the product.\n"
        "- Flag inconsistencies with path-level evidence."
    ),
    "ux": (
        "- Check `audit.ux.scope_roots`, docs mentioning flows/a11y, and plausible UI routes/components under declared paths.\n"
        "- Report coverage signals only—not visual design critique."
    ),
}


def _norm_angle_ids(requested: list[str] | None) -> list[str]:
    if not requested:
        return list(ANGLE_IDS)
    out: list[str] = []
    for a in requested:
        s = str(a).strip().lower().replace("-", "_")
        if s in CURSOR_SCAN_ANGLE_IDS and s not in out:
            out.append(s)
    return out if out else list(ANGLE_IDS)


def normalize_angle_id(angle_id: str) -> str:
    """Normalize user/CLI angle id; raises ``ValueError`` if not a known audit angle."""
    s = str(angle_id).strip().lower().replace("-", "_")
    if s not in ANGLE_IDS:
        raise ValueError(f"Unknown angle_id: {angle_id!r}; expected one of {list(ANGLE_IDS)}")
    return s


def _compact_deterministic_excerpt(angle: dict[str, Any], *, max_lines: int = 6) -> str:
    dlines = angle.get("deterministic_summary_lines")
    if isinstance(dlines, list) and dlines:
        lines = [str(x) for x in dlines[:max_lines]]
    else:
        sl = angle.get("summary_lines") or []
        lines = [str(x) for x in sl[:max_lines]] if isinstance(sl, list) else []
    return "\n".join(f"  - {x}" for x in lines) if lines else "  (no summary_lines)"


def _cap_list(key: str, val: Any, *, cap: int) -> Any:
    if not isinstance(val, list):
        return val
    if len(val) <= cap:
        return val
    rest = len(val) - cap
    return list(val[:cap]) + [f"... ({rest} more omitted)"]


def _bounded_angle_evidence_snapshot(
    block: dict[str, Any] | None,
    aid: str,
    *,
    max_json_chars: int = 7200,
) -> str:
    """JSON snapshot of deterministic angle payload for prompt grounding (bounded)."""
    if not isinstance(block, dict):
        return "(No angle block in bundle — run `argus audit run --product-id <id>` first.)"
    d: dict[str, Any] = {}
    for k, v in block.items():
        if k in ("cursor_scan",):
            continue
        d[k] = v
    # Trim large list fields common across angles
    for lk, cap in (
        ("capabilities", 40),
        ("scanned_paths", 48),
        ("summary_lines", 18),
        ("deterministic_summary_lines", 18),
    ):
        if lk in d:
            d[lk] = _cap_list(lk, d[lk], cap=cap)
    try:
        s = json.dumps(d, indent=2, default=str, ensure_ascii=False)
    except (TypeError, ValueError):
        s = json.dumps({"_error": "could not serialize angle snapshot"}, indent=2)
    if len(s) > max_json_chars:
        s = s[:max_json_chars] + "\n... [truncated for prompt size]"
    return s


def build_cursor_scan_prompt_for_angle(repo_root: Path, product_id: str, angle_id: str) -> str:
    """
    Cursor codebase scan prompt for **one** angle: deterministic snapshot + angle lens + single-object JSON contract.

    Output instructs the agent to return one root object matching ``argus.audit_cursor_scan.v1`` (not the batch wrapper).
    """
    aid = normalize_angle_id(angle_id)
    root = repo_root.resolve()
    inv = build_inventory(root)
    if product_id not in inv.valid:
        raise ValueError(f"Unknown product: {product_id!r}")
    record = inv.valid[product_id]
    product_root, node = resolve_product_paths(root, record)
    pr = product_root.resolve()

    bundle = load_audit_bundle(root, product_id)
    bundle_angles = (bundle or {}).get("angles") or {}
    block = bundle_angles.get(aid) if isinstance(bundle_angles, dict) else None
    snapshot = _bounded_angle_evidence_snapshot(block if isinstance(block, dict) else None, aid)
    excerpt = _compact_deterministic_excerpt(block, max_lines=10) if isinstance(block, dict) else "  (run `argus audit run` first)"

    lens = LENS_BY_ANGLE.get(aid, "Review this angle against repo evidence.")
    scan_focus = SCAN_FOCUS_BY_ANGLE.get(aid, "- Trace declarations to files under product_root and repo_root.\n- Cite paths in evidence.")

    example_inner = {
        "schema": CURSOR_SCAN_SCHEMA,
        "angle_id": aid,
        "summary_lines": [f"{aid}: observation tied to path/to/file (repo-relative)"],
        "findings": [
            {
                "title": "Example finding",
                "detail": "Observed in file X; inference Y is labeled as hypothesis.",
                "severity": "info",
                "evidence_refs": ["relative/path/to/file"],
            }
        ],
        "risks": [{"statement": "Example risk", "evidence_refs": ["relative/path/to/file"]}],
        "enhancements": [
            {
                "title": "Example incremental fix",
                "detail": "Small change supported by evidence_refs; not a redesign.",
                "severity": "info",
                "evidence_refs": ["relative/path/to/file"],
            }
        ],
        "confidence": 0.75,
        "repo_evidence_refs": ["relative/path"],
        "limitations": ["static inspection only; no production metrics"],
        "provenance": "cursor_codebase_scan",
    }

    rel_product = pr.relative_to(root)
    lines: list[str] = [
        f"# Argus Cursor audit — single angle `{aid}`",
        "",
        "You are performing a **focused** Argus Cursor codebase scan for **this angle only** (interpretation layer).",
        "Inspect the **actual repository** under `repo_root` and the product tree under `product_root`.",
        "Use repository files and paths only; do not invent production telemetry, live metrics, or undocumented APIs.",
        "",
        "## Identity and scope",
        f"- **product_id**: `{product_id}`",
        f"- **angle_id**: `{aid}` (analyze **only** this angle in this session)",
        f"- **product_name**: {node.name or product_id}",
        f"- **product_root** (relative to repo): `{rel_product}`",
        f"- **repo_root**: `{root}`",
        "",
        "## Merge and provenance (read before writing JSON)",
        f"- Argus stores your output at **`bundle.angles.{aid}.cursor_scan`** in `runs/audit/{product_id}/bundle.json`.",
        "- Deterministic runner output on the same angle object is **preserved**; your payload is merged as the Cursor layer.",
        "- **`sources.cursor_scan`** will reflect your ingest; do not claim deterministic work as Cursor output.",
        "- Set **`provenance`** to exactly `cursor_codebase_scan` on the JSON object.",
        "- If you disagree with the deterministic baseline below, say so in `limitations` / `detail` and **cite repo paths** that support your view.",
        "",
        "## Deterministic baseline — bounded JSON snapshot (ground truth from last `argus audit run`)",
        "Do not contradict structured facts without file-level evidence. Label inference as such in `detail` or `limitations`.",
        "",
        "```json",
        snapshot,
        "```",
        "",
        "## Deterministic summary lines (quick read)",
        excerpt,
        "",
        "## Angle lens — what this angle means",
        lens,
        "",
        "## Repo scan focus — bounded tasks for this angle",
        scan_focus,
        "",
        "## Inspection rules",
        "- **Evidence vs inference**: state what you saw in a file (path + excerpt-level description); hypotheses go in `detail` / `limitations`.",
        "- **No broad redesigns**: `enhancements` must be incremental and tied to `evidence_refs`; avoid greenfield architecture.",
        "- **Concrete items**: every finding, risk, and enhancement should point at repo evidence where possible.",
        "- **Structured JSON only**: your entire reply must be a single JSON object — no markdown, no prose outside JSON.",
        "",
        "### Mandatory JSON contract (root object)",
        cursor_scan_single_object_contract_prompt_block(),
        "",
        "---",
        "Required: **one** JSON object with top-level `schema` = "
        f'`"{CURSOR_SCAN_SCHEMA}"` and `angle_id` = `"{aid}"`.',
        "",
        json.dumps(example_inner, indent=2),
    ]
    return "\n".join(lines)


def build_cursor_scan_prompt(
    repo_root: Path,
    product_id: str,
    *,
    angle_ids: list[str] | None = None,
) -> str:
    """
    Cursor codebase scan prompt: grounded in current deterministic audit (if present) + lens text.

    Response must be JSON matching ``argus.audit_cursor_scan_batch.v1`` (see template at end).
    """
    root = repo_root.resolve()
    inv = build_inventory(root)
    if product_id not in inv.valid:
        raise ValueError(f"Unknown product: {product_id!r}")
    record = inv.valid[product_id]
    product_root, node = resolve_product_paths(root, record)
    pr = product_root.resolve()

    angles = _norm_angle_ids(angle_ids)
    bundle = load_audit_bundle(root, product_id)
    bundle_angles = (bundle or {}).get("angles") or {}

    lines: list[str] = [
        "You are performing an Argus **Cursor codebase scan** for a product audit (interpretation layer).",
        "Use repository files and paths only; do not invent production telemetry or live metrics.",
        "Return **one JSON object** (no markdown fences). Output MUST match the contract below exactly — Argus ingests with strict validation.",
        "",
        "### Mandatory JSON contract (per angle object)",
        cursor_scan_contract_prompt_block(),
        "",
        f"product_id: {product_id}",
        f"product_name: {node.name or product_id}",
        f"product_root (relative to repo): {pr.relative_to(root)}",
        f"repo_root: {root}",
        "",
        "### Deterministic audit baseline (from last `argus audit run`; cite paths that contradict or extend this)",
        "",
    ]

    for aid in angles:
        block = bundle_angles.get(aid)
        excerpt = _compact_deterministic_excerpt(block) if isinstance(block, dict) else "  (run `argus audit run` first for baseline)"
        lines.append(f"#### Angle `{aid}` — static summary_lines")
        lines.append(excerpt)
        lines.append("")

    lines.append("### Lenses — what to analyze in-repo for each angle")
    lines.append("")
    for aid in angles:
        desc = LENS_BY_ANGLE.get(aid, "Review this angle against repo evidence.")
        lines.append(f"## `{aid}`")
        lines.append(desc)
        lines.append("")

    example_inner = {
        "schema": CURSOR_SCAN_SCHEMA,
        "angle_id": angles[0] if angles else "product_gap",
        "summary_lines": [f"{angles[0] if angles else 'product_gap'}: observation (see path/to/file)"],
        "findings": [
            {
                "title": "Example finding",
                "detail": "",
                "severity": "info",
                "evidence_refs": ["relative/path/to/file"],
            }
        ],
        "risks": [{"statement": "Example risk", "evidence_refs": []}],
        "enhancements": [
            {
                "title": "Example enhancement",
                "detail": "",
                "severity": "info",
                "evidence_refs": [],
            }
        ],
        "confidence": 0.75,
        "repo_evidence_refs": ["relative/path"],
        "limitations": ["static inspection only; no production metrics"],
        "provenance": "cursor_codebase_scan",
    }
    template = {"schema": CURSOR_SCAN_BATCH_SCHEMA, "angles": {a: {**example_inner, "angle_id": a} for a in angles}}

    lines.extend(
        [
            "---",
            "Required top-level JSON (fill `angles` with one object per angle; each must satisfy the contract):",
            json.dumps(template, indent=2),
            "",
            f"Required keys under `angles`: {angles}",
            f'Top-level schema must be "{CURSOR_SCAN_BATCH_SCHEMA}".',
        ]
    )
    return "\n".join(lines)


def build_agent_batch_prompt(
    repo_root: Path,
    product_id: str,
    *,
    angle_ids: list[str] | None = None,
) -> str:
    """Alias for :func:`build_cursor_scan_prompt` (backward compatible name)."""
    return build_cursor_scan_prompt(repo_root, product_id, angle_ids=angle_ids)
