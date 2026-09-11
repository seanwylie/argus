"""
Canonical work order contract: handoff from Argus core (steward) to the worker lane (executor).

The worker consumes approved artifacts only; it does not re-prioritize work.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Final

from argus.core.serialize import dumps_json, to_jsonable

WORK_ORDER_SCHEMA: Final = "argus.work_order.v1"

_REQUEST_TYPE_SAFE = re.compile(r"[^a-zA-Z0-9._-]+")


def work_orders_dir(repo_root: Path) -> Path:
    return Path(repo_root).resolve() / "runs" / "worker" / "work_orders"


def work_orders_latest_dir(repo_root: Path) -> Path:
    return work_orders_dir(repo_root) / "latest"


def _sanitize_request_type_for_filename(request_type: str) -> str:
    s = str(request_type).strip().lower()
    if not s:
        return "unknown"
    out = _REQUEST_TYPE_SAFE.sub("_", s).strip("_")
    return (out[:96] if len(out) > 96 else out) or "unknown"


def sanitize_request_type_for_filename(request_type: str) -> str:
    """Public alias for latest-path naming (``product_id__<sanitized>.json``)."""
    return _sanitize_request_type_for_filename(request_type)


def _iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def new_work_order_id(*, selected_at_utc: str, product_id: str, request_type: str) -> str:
    """Deterministic id from selection time + product + request (stable for tests)."""
    base = f"{selected_at_utc}|{product_id}|{request_type}".encode("utf-8")
    digest = hashlib.sha256(base).hexdigest()[:12]
    m = re.match(
        r"^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})",
        str(selected_at_utc).strip(),
    )
    if m:
        ts = f"{m.group(1)}{m.group(2)}{m.group(3)}{m.group(4)}{m.group(5)}{m.group(6)}"
    else:
        ts = "unknown"
    return f"wo_{ts}_{digest}"


def create_work_order(
    *,
    product_id: str,
    request_type: str,
    selected_by: str,
    rationale: str,
    source_artifact_paths: list[str],
    acceptance_criteria: list[str],
    implementation_seed: dict[str, Any] | None = None,
    priority: str = "normal",
    autonomy_mode: str = "supervised",
    approval_required: bool = True,
    status: str | None = None,
    mission_context: dict[str, Any] | None = None,
    risk_notes: list[str] | None = None,
    recommended_worker_mode: str = "apply_patch",
    work_order_id: str | None = None,
    selected_at_utc: str | None = None,
) -> dict[str, Any]:
    """
    Build a normalized ``argus.work_order.v1`` payload (does not write disk).

    *status:* if omitted, ``pending_approval`` when ``approval_required`` else ``approved``.
    """
    pid = str(product_id).strip()
    rt = str(request_type).strip()
    if not pid:
        raise ValueError("product_id is required")
    if not rt:
        raise ValueError("request_type is required")

    sat = selected_at_utc or _iso_now()
    woid = (work_order_id or "").strip() or new_work_order_id(
        selected_at_utc=sat, product_id=pid, request_type=rt
    )

    st = status
    if st is None:
        st = "pending_approval" if approval_required else "approved"

    payload: dict[str, Any] = {
        "schema": WORK_ORDER_SCHEMA,
        "work_order_id": woid,
        "product_id": pid,
        "request_type": rt,
        "selected_at_utc": sat,
        "selected_by": str(selected_by).strip() or "argus.core",
        "priority": str(priority).strip() or "normal",
        "autonomy_mode": str(autonomy_mode).strip() or "supervised",
        "approval_required": bool(approval_required),
        "status": str(st).strip(),
        "rationale": str(rationale).strip(),
        "source_artifact_paths": [str(p) for p in source_artifact_paths],
        "acceptance_criteria": [str(x) for x in acceptance_criteria],
        "implementation_seed": dict(implementation_seed) if implementation_seed else {},
        "mission_context": mission_context if isinstance(mission_context, dict) else None,
        "risk_notes": [str(x) for x in (risk_notes or [])],
        "recommended_worker_mode": str(recommended_worker_mode).strip() or "apply_patch",
    }
    return payload


def render_work_order_markdown(payload: dict[str, Any]) -> str:
    """Human-readable summary for operators and worker intake."""
    lines = [
        "# Work order",
        "",
        f"- **Schema:** `{payload.get('schema')}`",
        f"- **Work order id:** `{payload.get('work_order_id')}`",
        f"- **Product:** `{payload.get('product_id')}`",
        f"- **Request type:** `{payload.get('request_type')}`",
        f"- **Status:** `{payload.get('status')}`",
        f"- **Priority:** `{payload.get('priority')}`",
        f"- **Selected (UTC):** {payload.get('selected_at_utc')}",
        f"- **Selected by:** `{payload.get('selected_by')}`",
        f"- **Autonomy mode:** `{payload.get('autonomy_mode')}`",
        f"- **Approval required:** {payload.get('approval_required')}",
        f"- **Recommended worker mode:** `{payload.get('recommended_worker_mode')}`",
        "",
        "## Rationale",
        "",
        str(payload.get("rationale") or "—"),
        "",
        "## Source artifacts",
        "",
    ]
    for p in payload.get("source_artifact_paths") or []:
        lines.append(f"- `{p}`")
    if not (payload.get("source_artifact_paths") or []):
        lines.append("—")
    lines.extend(["", "## Acceptance criteria", ""])
    for i, c in enumerate(payload.get("acceptance_criteria") or [], 1):
        lines.append(f"{i}. {c}")
    if not (payload.get("acceptance_criteria") or []):
        lines.append("—")
    lines.extend(["", "## Implementation seed", "", "```json", ""])
    seed = payload.get("implementation_seed") or {}
    lines.append(json.dumps(seed, indent=2, sort_keys=True))
    lines.extend(["", "```", ""])
    mc = payload.get("mission_context")
    if mc:
        lines.extend(["## Mission context", "", "```json", json.dumps(mc, indent=2, sort_keys=True), "```", ""])
    rn = payload.get("risk_notes") or []
    if rn:
        lines.extend(["## Risk notes", ""])
        for n in rn:
            lines.append(f"- {n}")
        lines.append("")
    lines.append(
        "---\n\n*Argus core is the steward: the worker lane must not re-prioritize or substitute goals.*\n"
    )
    return "\n".join(lines)


def write_work_order_artifacts(
    repo_root: Path,
    payload: dict[str, Any],
    *,
    write_latest: bool = True,
) -> tuple[Path, Path, Path | None, Path | None]:
    """
    Persist JSON + Markdown:

    - ``runs/worker/work_orders/<work_order_id>.json`` (+ .md)
    - ``runs/worker/work_orders/latest/<product_id>__<request_type>.json`` (+ .md) when ``write_latest``
    """
    root = Path(repo_root).resolve()
    if str(payload.get("schema") or "") != WORK_ORDER_SCHEMA:
        raise ValueError(f"payload.schema must be {WORK_ORDER_SCHEMA!r}")
    woid = str(payload.get("work_order_id") or "").strip()
    pid = str(payload.get("product_id") or "").strip()
    rt = str(payload.get("request_type") or "").strip()
    if not woid or not pid or not rt:
        raise ValueError("payload missing work_order_id, product_id, or request_type")

    base = work_orders_dir(root)
    base.mkdir(parents=True, exist_ok=True)

    pl = dict(payload)
    body_json = dumps_json(to_jsonable(pl)) + "\n"
    md = render_work_order_markdown(pl)

    stamped_json = base / f"{woid}.json"
    stamped_md = base / f"{woid}.md"
    stamped_json.write_text(body_json, encoding="utf-8")
    stamped_md.write_text(md, encoding="utf-8")

    latest_json: Path | None = None
    latest_md: Path | None = None
    if write_latest:
        ld = work_orders_latest_dir(root)
        ld.mkdir(parents=True, exist_ok=True)
        safe_rt = _sanitize_request_type_for_filename(rt)
        latest_json = ld / f"{pid}__{safe_rt}.json"
        latest_md = ld / f"{pid}__{safe_rt}.md"
        latest_json.write_text(body_json, encoding="utf-8")
        latest_md.write_text(md, encoding="utf-8")

    return stamped_json, stamped_md, latest_json, latest_md


def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return raw if isinstance(raw, dict) else None


def load_latest_work_orders(
    repo_root: Path,
    *,
    product_id: str | None = None,
) -> list[dict[str, Any]]:
    """
    Read all ``latest/<product_id>__<request_type>.json`` files that match :data:`WORK_ORDER_SCHEMA`.
    Sorted by ``(product_id, request_type, work_order_id)``.

    Each record is merged with ``runs/worker/work_orders/actions/`` so ``status`` is **effective**
    (see :func:`argus.worker.work_order_actions.overlay_work_order_view`).
    """
    from argus.worker.work_order_actions import overlay_work_order_view

    root = Path(repo_root).resolve()
    ld = work_orders_latest_dir(root)
    if not ld.is_dir():
        return []

    out: list[dict[str, Any]] = []
    for path in sorted(ld.glob("*.json")):
        raw = _load_json(path)
        if not raw or str(raw.get("schema") or "") != WORK_ORDER_SCHEMA:
            continue
        if product_id is not None and str(raw.get("product_id") or "").strip() != str(product_id).strip():
            continue
        out.append(overlay_work_order_view(root, raw))

    def sort_key(d: dict[str, Any]) -> tuple[str, str, str]:
        return (
            str(d.get("product_id") or ""),
            str(d.get("request_type") or ""),
            str(d.get("work_order_id") or ""),
        )

    return sorted(out, key=sort_key)


def find_work_order(repo_root: Path, work_order_id: str) -> dict[str, Any] | None:
    """
    Load ``runs/worker/work_orders/<work_order_id>.json`` if present and schema matches.

    Merges action history from ``runs/worker/work_orders/actions/`` so ``status`` is **effective**;
    ``status_stamped`` holds the on-disk issuance snapshot.
    """
    from argus.worker.work_order_actions import overlay_work_order_view

    root = Path(repo_root).resolve()
    woid = str(work_order_id).strip()
    if not woid:
        return None
    raw = _load_json(work_orders_dir(root) / f"{woid}.json")
    if not raw or str(raw.get("schema") or "") != WORK_ORDER_SCHEMA:
        return None
    return overlay_work_order_view(root, raw)


def latest_work_order_path_for(repo_root: Path, *, product_id: str, request_type: str) -> Path:
    """Path to ``latest/<product_id>__<sanitized_request_type>.json`` (may not exist)."""
    root = Path(repo_root).resolve()
    pid = str(product_id).strip()
    safe_rt = _sanitize_request_type_for_filename(request_type)
    return work_orders_latest_dir(root) / f"{pid}__{safe_rt}.json"


def load_latest_work_order_for(
    repo_root: Path,
    *,
    product_id: str,
    request_type: str,
) -> dict[str, Any] | None:
    """
    Load the latest stamped work order for ``product_id`` + ``request_type`` via the symlink-style
    ``latest/*.json`` file (same filename rules as :func:`write_work_order_artifacts`).

    Merges action history so ``status`` is effective (see :func:`find_work_order`).
    """
    from argus.worker.work_order_actions import overlay_work_order_view

    root = Path(repo_root).resolve()
    path = latest_work_order_path_for(root, product_id=product_id, request_type=request_type)
    raw = _load_json(path)
    if not raw or str(raw.get("schema") or "") != WORK_ORDER_SCHEMA:
        return None
    return overlay_work_order_view(root, raw)


__all__ = [
    "WORK_ORDER_SCHEMA",
    "create_work_order",
    "find_work_order",
    "latest_work_order_path_for",
    "load_latest_work_order_for",
    "load_latest_work_orders",
    "new_work_order_id",
    "render_work_order_markdown",
    "sanitize_request_type_for_filename",
    "work_orders_dir",
    "work_orders_latest_dir",
    "write_work_order_artifacts",
]
