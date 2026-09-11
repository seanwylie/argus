"""
Structured builder work orders derived from signal contract (and related operational gaps).

Deterministic artifacts only — no execution, no orchestration hooks.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from argus.core.serialize import dumps_json
from argus.disk_budget import require_disk_headroom_for_write
from argus.observability.signal_contract import (
    SIGNAL_CONTRACT_EVALUATION_SCHEMA,
    evaluate_signal_contract,
)

BUILDER_WORK_ORDER_SCHEMA = "argus.builder_work_order.v1"
BUILDER_WORK_ORDERS_BUNDLE_SCHEMA = "argus.builder_work_orders_bundle.v1"

_STATUS_PROPOSED = "proposed"
_BACKEND_CURSOR = "cursor"
_BACKEND_FILESYSTEM = "filesystem"


def _stable_id(*parts: str) -> str:
    h = hashlib.sha256("\0".join(parts).encode("utf-8")).hexdigest()[:14]
    return f"wo_{h}"


def _slug(s: str) -> str:
    x = re.sub(r"[^a-zA-Z0-9._-]+", "_", s.strip())[:80]
    return x or "x"


def work_orders_output_dir(repo_root: Path, product_id: str) -> Path:
    return Path(repo_root).resolve() / "runs" / "builder" / "work_orders" / _slug(product_id)


def _default_backend_for_kind(kind: str) -> str:
    if kind in ("golden_signal_gap", "golden_signal_stale"):
        return _BACKEND_CURSOR
    if kind == "mission_signal_gap":
        # First pass: deterministic notes/checklists under runs/builder (no codegen)
        return _BACKEND_FILESYSTEM
    return _BACKEND_FILESYSTEM


def work_order_from_candidate(
    *,
    product_id: str,
    repo_root: Path,
    candidate: dict[str, Any],
    signal_contract_eval: dict[str, Any],
) -> dict[str, Any] | None:
    """Map one ``builder_task_candidates`` row to a work order, or None if unsupported."""
    kind = str(candidate.get("kind") or "")
    sid = str(candidate.get("signal_id") or "")
    pri = str(candidate.get("priority") or "medium").strip().lower()
    note = str(candidate.get("note") or "")

    if kind not in ("golden_signal_gap", "golden_signal_stale", "mission_signal_gap"):
        return None

    wtype = kind
    title = f"Signal contract: {sid} ({kind})"
    if kind == "golden_signal_gap":
        objective = (
            f"Restore required operability coverage for golden signal `{sid}` on product `{product_id}` "
            f"by collecting or wiring real observations into `runs/signals/latest/{product_id}.json`."
        )
    elif kind == "golden_signal_stale":
        objective = (
            f"Refresh stale operability coverage for golden signal `{sid}` on `{product_id}` "
            f"(re-collect signals so canonical freshness is current)."
        )
    else:
        objective = (
            f"Add recommended mission optimization signal `{sid}` for `{product_id}` "
            f"(analytics/metrics alignment with declared mission). "
            f"Start with a filesystem checklist; wire adapters when ready."
        )

    suggested = _default_backend_for_kind(kind)
    root = repo_root.resolve()

    constraints = [
        "Do not change escalation triggers, strategy, or orchestration policy in this pass.",
        "Preserve existing behavior unless explicitly widening observability.",
        f"Validate with `argus portfolio signal-contract {product_id}` after changes.",
    ]
    acceptance = [
        f"`argus portfolio signal-contract {product_id}` reports this slot satisfied or no longer stale/missing.",
        "No regressions in `uv run pytest tests/test_signal_contract.py` (signal contract tests).",
    ]
    if suggested == _BACKEND_FILESYSTEM:
        constraints.append(
            "Filesystem mode: only add or update markdown/checklists under `runs/builder/` or `docs/` as noted."
        )
        acceptance.append("Durable note or checklist file exists where specified.")

    evidence = {
        "source": "signal_contract_evaluation",
        "signal_contract_schema": signal_contract_eval.get("schema"),
        "candidate": dict(candidate),
        "product_type_bucket": signal_contract_eval.get("product_type_bucket"),
        "mission_id": signal_contract_eval.get("mission_id"),
    }

    wid = _stable_id(product_id, wtype, sid, pri)

    return {
        "schema": BUILDER_WORK_ORDER_SCHEMA,
        "work_order_id": wid,
        "product_id": product_id,
        "repo_path": str(root),
        "work_type": wtype,
        "priority": "high" if pri == "high" else "medium",
        "title": title,
        "objective": objective,
        "evidence": evidence,
        "constraints": constraints,
        "acceptance_criteria": acceptance,
        "suggested_backend": suggested,
        "status": _STATUS_PROPOSED,
        "notes": note,
        "recommended_paths": _recommended_paths_for_kind(product_id, wtype),
    }


def _recommended_paths_for_kind(product_id: str, wtype: str) -> list[str]:
    base = [
        f"products/{product_id}/product.yaml",
        f"runs/signals/latest/{product_id}.json",
        f"runs/temporal/latest/{product_id}.json",
        "argus/observability/signal_contract.py",
    ]
    if wtype == "mission_signal_gap":
        base.insert(
            0,
            f"runs/builder/work_orders/{_slug(product_id)}/implementation_checklist.md",
        )
    return base


def build_work_orders_from_candidates(
    *,
    product_id: str,
    repo_root: Path,
    signal_contract_eval: dict[str, Any],
    backend_filter: str | None = None,
) -> list[dict[str, Any]]:
    """Convert ``builder_task_candidates`` into work orders (narrow mapping)."""
    cands = signal_contract_eval.get("builder_task_candidates") or []
    if not isinstance(cands, list):
        return []
    out: list[dict[str, Any]] = []
    for c in cands:
        if not isinstance(c, dict):
            continue
        wo = work_order_from_candidate(
            product_id=product_id,
            repo_root=repo_root,
            candidate=c,
            signal_contract_eval=signal_contract_eval,
        )
        if wo is None:
            continue
        if backend_filter in (_BACKEND_CURSOR, _BACKEND_FILESYSTEM):
            if str(wo.get("suggested_backend") or "") != backend_filter:
                continue
        out.append(wo)
    return sorted(out, key=lambda x: (x.get("priority") != "high", str(x.get("work_order_id"))))


def build_work_orders_bundle_from_signal_contract(
    repo_root: Path,
    product_id: str,
    *,
    backend_filter: str | None = None,
) -> dict[str, Any]:
    """
    Evaluate signal contract and emit a bundle of work orders (schema ``argus.builder_work_orders_bundle.v1``).
    """
    root = repo_root.resolve()
    pid = str(product_id or "").strip()
    sc = evaluate_signal_contract(root, pid)
    if str(sc.get("schema") or "") != SIGNAL_CONTRACT_EVALUATION_SCHEMA:
        sc = dict(sc)
        sc.setdefault("schema", SIGNAL_CONTRACT_EVALUATION_SCHEMA)
    orders = build_work_orders_from_candidates(
        product_id=pid,
        repo_root=root,
        signal_contract_eval=sc,
        backend_filter=backend_filter,
    )
    ts = datetime.now(timezone.utc).isoformat()
    for wo in orders:
        wo["created_at_utc"] = ts
    return {
        "schema": BUILDER_WORK_ORDERS_BUNDLE_SCHEMA,
        "generated_at_utc": ts,
        "product_id": pid,
        "source_evaluation_schema": sc.get("schema"),
        "signal_contract_evaluated_at_utc": sc.get("evaluated_at_utc"),
        "work_orders": orders,
    }


def render_cursor_implementation_brief(work_order: dict[str, Any]) -> str:
    """
    Deterministic Cursor-oriented brief (markdown). Does not invoke Cursor.
    """
    wo_id = work_order.get("work_order_id", "")
    pid = work_order.get("product_id", "")
    repo = work_order.get("repo_path", "")
    title = work_order.get("title", "")
    objective = work_order.get("objective", "")
    backend = work_order.get("suggested_backend", "cursor")
    constraints = work_order.get("constraints") or []
    acceptance = work_order.get("acceptance_criteria") or []
    paths = work_order.get("recommended_paths") or []
    evidence = work_order.get("evidence") if isinstance(work_order.get("evidence"), dict) else {}

    lines = [
        "# Argus builder — implementation brief",
        "",
        f"**Work order:** `{wo_id}`",
        f"**Product:** `{pid}`",
        f"**Repository root:** `{repo}`",
        f"**Suggested backend:** `{backend}`",
        f"**Title:** {title}",
        "",
        "## Problem summary",
        "",
        objective,
        "",
        "## Evidence (inspectable)",
        "",
        "```",
        dumps_json(evidence) if evidence else "{}",
        "```",
        "",
        "## Required outcome",
        "",
        "Address the signal contract gap described above so Argus can observe this product honestly "
        "(golden = operability, mission = optimization).",
        "",
        "## Constraints",
        "",
    ]
    for c in constraints:
        lines.append(f"- {c}")
    lines.extend(["", "## Acceptance criteria", ""])
    for a in acceptance:
        lines.append(f"- {a}")
    lines.extend(["", "## Recommended files / areas", ""])
    for p in paths:
        lines.append(f"- `{p}`")
    lines.extend(
        [
            "",
            "## Execution instructions",
            "",
            "- Use this repository as the workspace root.",
            "- **Preserve behavior:** do not refactor unrelated code; keep changes minimal and reviewable.",
            f"- **Validate:** run `argus portfolio signal-contract {pid}` after edits.",
            "- Do not enable autonomous portfolio execution or change orchestration triggers as part of this brief.",
            "",
        ]
    )
    if backend == _BACKEND_FILESYSTEM:
        lines.extend(
            [
                "### Filesystem mode",
                "",
                "Prefer adding a short checklist or note under `runs/builder/work_orders/<product_id>/` "
                "rather than editing application code, unless wiring real signal adapters.",
                "",
            ]
        )
    else:
        lines.extend(
            [
                "### Cursor mode",
                "",
                "Use codebase-aware editing to wire or refresh observability; follow Argus patterns in "
                "`argus/signals/` and product `product.yaml` signal declarations.",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def write_work_order_artifacts(
    repo_root: Path,
    bundle: dict[str, Any],
    *,
    run_id: str | None = None,
) -> tuple[Path, Path, Path]:
    """
    Write ``latest.json``, stamped copy, and ``latest.md`` summary under
    ``runs/builder/work_orders/<product_id>/``.

    Returns ``(latest_json, latest_md, stamped_json)``.
    """
    root = repo_root.resolve()
    pid = str(bundle.get("product_id") or "").strip()
    d = work_orders_output_dir(root, pid)
    require_disk_headroom_for_write(d, op="builder work orders")
    d.mkdir(parents=True, exist_ok=True)
    rid = run_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    latest_json = d / "latest.json"
    latest_md = d / "latest.md"
    stamped = d / f"{rid}.json"
    latest_json.write_text(dumps_json(bundle) + "\n", encoding="utf-8")
    stamped.write_text(dumps_json(bundle) + "\n", encoding="utf-8")
    latest_md.write_text(_render_bundle_markdown(bundle), encoding="utf-8")

    from argus.runs_retention import maybe_prune_work_orders_after_write

    maybe_prune_work_orders_after_write(root, pid)

    return latest_json, latest_md, stamped


def _render_bundle_markdown(bundle: dict[str, Any]) -> str:
    pid = bundle.get("product_id", "")
    lines = [
        f"# Builder work orders — `{pid}`",
        "",
        f"**Schema:** `{bundle.get('schema')}`",
        f"**Generated (UTC):** {bundle.get('generated_at_utc')}",
        "",
        "## Work orders",
        "",
    ]
    for wo in bundle.get("work_orders") or []:
        if not isinstance(wo, dict):
            continue
        lines.append(f"### `{wo.get('work_order_id')}` — {wo.get('title')}")
        lines.append("")
        lines.append(f"- **Type:** `{wo.get('work_type')}` · **Priority:** `{wo.get('priority')}` · **Backend:** `{wo.get('suggested_backend')}`")
        lines.append(f"- **Objective:** {wo.get('objective')}")
        lines.append("")
    lines.append("Full JSON: `latest.json`.")
    lines.append("")
    return "\n".join(lines)


def load_latest_work_order_bundle(repo_root: Path, product_id: str) -> dict[str, Any] | None:
    p = work_orders_output_dir(repo_root, product_id) / "latest.json"
    if not p.is_file():
        return None
    try:
        import json

        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return raw if isinstance(raw, dict) else None


__all__ = [
    "BUILDER_WORK_ORDER_SCHEMA",
    "BUILDER_WORK_ORDERS_BUNDLE_SCHEMA",
    "build_work_orders_bundle_from_signal_contract",
    "load_latest_work_order_bundle",
    "render_cursor_implementation_brief",
    "work_orders_output_dir",
    "write_work_order_artifacts",
]
