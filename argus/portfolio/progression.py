"""
Bounded multi-product orchestration progression — one ``advance_orchestration`` step per product.

Reuses :func:`argus.orchestrator.advancement.advance_orchestration` (same rules as
``argus orchestration advance``); does not define a parallel execution model.
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from argus.core.serialize import dumps_json
from argus.orchestrator.advancement import advance_orchestration, orchestration_advancement_payload
from argus.orchestrator.eligibility import evaluate_product_orchestration
from argus.orchestrator.state_models import (
    ACTION_STATUS_BLOCKED,
    ACTION_STATUS_EXECUTED,
    ACTION_STATUS_FAILED,
    ACTION_STATUS_QUEUED,
    ACTION_STATUS_QUEUED_UNHANDLED,
    ACTION_STATUS_SKIPPED,
    ORCH_STATUS_BLOCKED_WAITING_APPROVAL,
    ORCH_STATUS_BLOCKED_WAITING_INPUT,
)
from argus.portfolio.operator_queue import (
    OPERATOR_QUEUE_SCHEMA,
    build_operator_queue_payload,
    operator_queue_output_dir,
)
from argus.portfolio.strategy_influence import load_latest_strategic_posture

PORTFOLIO_PROGRESSION_SCHEMA = "argus.portfolio_progression.v1"

DEFAULT_PROGRESS_LIMIT = 5

_SUMMARY_KEYS = (
    "advanced",
    "blocked_waiting",
    "blocked_approval",
    "no_action",
    "failed",
    "skipped",
)


def _normalize_summary_counts(raw: dict[str, int]) -> dict[str, int]:
    out = {k: int(raw.get(k, 0)) for k in _SUMMARY_KEYS}
    for k, v in raw.items():
        if k not in out:
            out[k] = int(v)
    return out


def portfolio_progression_dir(repo_root: Path) -> Path:
    return Path(repo_root).resolve() / "runs" / "portfolio" / "progression"


def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def load_operator_queue_or_rebuild(
    repo_root: Path,
    *,
    products_dir: Path | None = None,
) -> tuple[dict[str, Any], str]:
    """
    Return ``(queue_payload, source)`` where source is ``latest`` or ``rebuilt``.
    """
    p = operator_queue_output_dir(repo_root) / "latest.json"
    raw = _load_json(p)
    if raw is not None and raw.get("schema") == OPERATOR_QUEUE_SCHEMA:
        return raw, "latest"
    return build_operator_queue_payload(repo_root, products_dir=products_dir), "rebuilt"


def _import_failed(state: dict[str, Any]) -> bool:
    ih = state.get("import_health") if isinstance(state.get("import_health"), dict) else {}
    if str(ih.get("gating_tier") or "") == "failed":
        return True
    fps = ih.get("first_pass_status")
    if isinstance(fps, str) and fps.strip().lower() == "failed":
        return True
    return False


def _waiting_blocked_headline(state: dict[str, Any]) -> bool:
    orch = str(state.get("orchestration_status") or "")
    return orch in (ORCH_STATUS_BLOCKED_WAITING_INPUT, ORCH_STATUS_BLOCKED_WAITING_APPROVAL)


def _map_outcome(
    *,
    state_before: dict[str, Any],
    payload: dict[str, Any],
    dry_run: bool,
    execute: bool,
) -> tuple[str, str]:
    """Return ``(outcome, short_reason)`` using vocabulary from the portfolio progression contract."""
    ast = str(payload.get("action_status") or "")
    snap_orch = str(payload.get("snapshot_orchestration_status") or state_before.get("orchestration_status") or "")
    tr = str(payload.get("transition_reason") or "")

    if dry_run:
        if ast == ACTION_STATUS_QUEUED:
            sa = payload.get("selected_action")
            return "no_action", f"dry_run: would queue action {sa!r} (no writes)"
        if ast == ACTION_STATUS_BLOCKED:
            if snap_orch == ORCH_STATUS_BLOCKED_WAITING_APPROVAL:
                return "blocked_approval", f"dry_run: blocked_waiting_approval — {tr}"
            return "blocked_waiting", f"dry_run: blocked_waiting_input — {tr}"
        if ast == ACTION_STATUS_SKIPPED:
            return "no_action", f"dry_run: skipped — {tr}"
        return "no_action", f"dry_run: action_status={ast!r}"

    if ast == ACTION_STATUS_EXECUTED:
        return "advanced", tr or "executed"
    if ast == ACTION_STATUS_FAILED:
        err = str(payload.get("execution_error") or "")
        return "failed", err or "execution failed"
    if ast == ACTION_STATUS_QUEUED_UNHANDLED:
        return "no_action", tr or "queued_unhandled"
    if ast == ACTION_STATUS_QUEUED:
        if execute:
            return "no_action", tr or "queued without execution (unexpected)"
        return "advanced", tr or "advancement queued (execute=false)"
    if ast == ACTION_STATUS_BLOCKED:
        if snap_orch == ORCH_STATUS_BLOCKED_WAITING_APPROVAL:
            return "blocked_approval", tr
        return "blocked_waiting", tr
    if ast == ACTION_STATUS_SKIPPED:
        return "no_action", tr or "no eligible actions"
    return "no_action", f"unmapped action_status={ast!r}"


def run_portfolio_progression(
    repo_root: Path,
    *,
    limit: int = DEFAULT_PROGRESS_LIMIT,
    products_dir: Path | None = None,
    dry_run: bool = False,
    execute: bool = True,
    skip_import_failed: bool = False,
    skip_waiting: bool = False,
    write_artifacts: bool = True,
) -> dict[str, Any]:
    """
    For each of the top ``limit`` queue entries (by priority), run at most one
    :func:`advance_orchestration` (or preview via :func:`orchestration_advancement_payload` when
    ``dry_run``).

    Guardrails: ``skip_import_failed``, ``skip_waiting`` (blocked-waiting headline only).
    """
    root = repo_root.resolve()
    if limit < 0:
        raise ValueError("limit must be >= 0")
    queue_payload, queue_source = load_operator_queue_or_rebuild(root, products_dir=products_dir)
    entries = list(queue_payload.get("entries") or [])
    entries.sort(key=lambda e: (int(e.get("queue_rank") or 9999), str(e.get("product_id") or "")))
    slice_rows = entries[:limit] if limit else []

    run_started = datetime.now(timezone.utc).isoformat()
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    strat_posture, _strat_raw = load_latest_strategic_posture(root)

    product_rows: list[dict[str, Any]] = []
    summary: dict[str, int] = {}

    for qe in slice_rows:
        if not isinstance(qe, dict):
            continue
        pid = str(qe.get("product_id") or "").strip()
        if not pid:
            continue

        state_before = evaluate_product_orchestration(root, pid)
        next_before = state_before.get("next_action")

        if skip_import_failed and _import_failed(state_before):
            product_rows.append(
                {
                    "product_id": pid,
                    "queue_rank": qe.get("queue_rank"),
                    "priority_score": qe.get("priority_score"),
                    "outcome": "skipped",
                    "reason": "skip_import_failed: first_pass failed or gating_tier=failed",
                    "action_attempted": None,
                    "next_action_before": next_before,
                    "next_action_after": next_before,
                    "advancement": None,
                    "skipped_reason": "import_failed",
                }
            )
            summary["skipped"] = summary.get("skipped", 0) + 1
            continue

        if skip_waiting and _waiting_blocked_headline(state_before):
            product_rows.append(
                {
                    "product_id": pid,
                    "queue_rank": qe.get("queue_rank"),
                    "priority_score": qe.get("priority_score"),
                    "outcome": "skipped",
                    "reason": "skip_waiting: orchestration in blocked_waiting_input/approval",
                    "action_attempted": None,
                    "next_action_before": next_before,
                    "next_action_after": next_before,
                    "advancement": None,
                    "skipped_reason": "waiting_blocked",
                }
            )
            summary["skipped"] = summary.get("skipped", 0) + 1
            continue

        ts = datetime.now(timezone.utc).isoformat()
        if dry_run:
            payload = orchestration_advancement_payload(state_before, selected_at_utc=ts)
            outcome, reason = _map_outcome(
                state_before=state_before, payload=payload, dry_run=True, execute=execute
            )
            next_after = next_before
            adv_subset = {
                "action_status": payload.get("action_status"),
                "selected_action": payload.get("selected_action"),
                "transition_reason": payload.get("transition_reason"),
            }
        else:
            _path, payload = advance_orchestration(
                root, pid, refresh_state=True, execute=execute
            )
            state_after = evaluate_product_orchestration(root, pid)
            next_after = state_after.get("next_action")
            outcome, reason = _map_outcome(
                state_before=state_before, payload=payload, dry_run=False, execute=execute
            )
            adv_subset = {
                "action_status": payload.get("action_status"),
                "selected_action": payload.get("selected_action"),
                "transition_reason": payload.get("transition_reason"),
                "execution_error": payload.get("execution_error"),
            }

        summary[outcome] = summary.get(outcome, 0) + 1  # sparse keys allowed (e.g. only touched outcomes)

        product_rows.append(
            {
                "product_id": pid,
                "queue_rank": qe.get("queue_rank"),
                "priority_score": qe.get("priority_score"),
                "outcome": outcome,
                "reason": reason,
                "action_attempted": payload.get("selected_action"),
                "next_action_before": next_before,
                "next_action_after": next_after,
                "advancement": adv_subset,
                "skipped_reason": None,
            }
        )

    out: dict[str, Any] = {
        "schema": PORTFOLIO_PROGRESSION_SCHEMA,
        "run_id": run_id,
        "generated_at_utc": run_started,
        "queue_source": queue_source,
        "operator_queue_generated_at_utc": queue_payload.get("generated_at_utc"),
        "limit": limit,
        "dry_run": dry_run,
        "execute": execute,
        "skip_import_failed": skip_import_failed,
        "skip_waiting": skip_waiting,
        "queue_slice": slice_rows,
        "products": product_rows,
        "summary_counts": _normalize_summary_counts(summary),
        "portfolio_strategy_context": {
            "strategic_posture_loaded": strat_posture,
            "note": (
                "Progression consumes operator queue order (which may include bounded strategy nudges) "
                "without changing orchestration eligibility."
                if strat_posture
                else "No portfolio strategy artifact loaded — queue uses base scoring only."
            ),
        },
    }
    if write_artifacts:
        write_portfolio_progression_artifacts(root, out, run_id=run_id)
    return out


def render_portfolio_progression_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Portfolio progression run",
        "",
        f"**Schema:** `{payload.get('schema')}`",
        f"**Run id:** `{payload.get('run_id')}`",
        f"**Generated (UTC):** {payload.get('generated_at_utc')}",
        f"**Queue source:** {payload.get('queue_source')}",
        f"**dry_run:** {payload.get('dry_run')} · **execute:** {payload.get('execute')}",
        "",
        "## Summary counts",
        "",
    ]
    for k, v in sorted((payload.get("summary_counts") or {}).items()):
        lines.append(f"- **{k}:** {v}")
    lines.extend(
        [
            "",
            "## Products",
            "",
            "| Product | Outcome | Action | next_action before → after | Reason |",
            "|---------|---------|--------|---------------------------|--------|",
        ]
    )
    for p in payload.get("products") or []:
        if not isinstance(p, dict):
            continue
        na_b = p.get("next_action_before")
        na_a = p.get("next_action_after")
        act = p.get("action_attempted")
        reason_cell = str(p.get("reason") or "").replace("|", "\\|")
        lines.append(
            f"| `{p.get('product_id')}` | `{p.get('outcome')}` | `{act}` | `{na_b}` → `{na_a}` | {reason_cell} |"
        )
    lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def write_portfolio_progression_artifacts(
    repo_root: Path,
    payload: dict[str, Any],
    *,
    run_id: str | None = None,
) -> tuple[Path, Path, Path, Path]:
    """
    Write ``<timestamp>.json/.md`` plus ``latest.json`` / ``latest.md`` copies (plain files).
    """
    root = repo_root.resolve()
    rid = run_id or str(payload.get("run_id") or "")
    if not rid:
        rid = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    d = portfolio_progression_dir(root)
    d.mkdir(parents=True, exist_ok=True)
    stamped_json = d / f"{rid}.json"
    stamped_md = d / f"{rid}.md"
    latest_json = d / "latest.json"
    latest_md = d / "latest.md"
    stamped_json.write_text(dumps_json(payload) + "\n", encoding="utf-8")
    stamped_md.write_text(render_portfolio_progression_markdown(payload), encoding="utf-8")
    shutil.copyfile(stamped_json, latest_json)
    shutil.copyfile(stamped_md, latest_md)
    return stamped_json, stamped_md, latest_json, latest_md
