"""
Read-only forensic replay of the last portfolio operator cycle from stored artifacts.

Does not run :mod:`argus.portfolio.cycle` or other evaluators — reconstructs from JSON on disk only.
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from argus.core.serialize import dumps_json
from argus.orchestrator.operator_snapshot import operator_snapshot_json_path
from argus.portfolio.delta_report import PORTFOLIO_DELTA_REPORT_SCHEMA
from argus.portfolio.intervention import PORTFOLIO_INTERVENTION_SCHEMA
from argus.portfolio.operator_queue import OPERATOR_QUEUE_SCHEMA, operator_queue_output_dir
from argus.portfolio.progression import PORTFOLIO_PROGRESSION_SCHEMA
from argus.portfolio.quiescence import PORTFOLIO_QUIESCENCE_SCHEMA

PORTFOLIO_REPLAY_SCHEMA = "argus.portfolio_replay.v1"
PORTFOLIO_CYCLE_SCHEMA = "argus.portfolio_cycle.v1"


def portfolio_replay_dir(repo_root: Path) -> Path:
    return Path(repo_root).resolve() / "runs" / "portfolio" / "replay"


def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _rel(root: Path, path: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except ValueError:
        return str(path)


def replay_portfolio(repo_root: Path) -> dict[str, Any]:
    root = repo_root.resolve()
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    generated_at = datetime.now(timezone.utc).isoformat()

    cycle_path = root / "runs" / "portfolio" / "cycle" / "latest.json"
    cycle = _load_json(cycle_path)

    q_path = operator_queue_output_dir(root) / "latest.json"
    prog_path = root / "runs" / "portfolio" / "progression" / "latest.json"
    quies_path = root / "runs" / "portfolio" / "quiescence" / "latest.json"
    delta_path = root / "runs" / "portfolio" / "delta_report" / "latest.json"
    inv_path = root / "runs" / "portfolio" / "intervention" / "latest.json"

    queue = _load_json(q_path)
    progression = _load_json(prog_path)
    quiescence = _load_json(quies_path)
    delta_report = _load_json(delta_path)
    intervention = _load_json(inv_path)

    input_artifacts: list[dict[str, Any]] = []
    output_artifacts: list[dict[str, Any]] = []

    def _add(path: Path, role: str, bucket: list[dict[str, Any]], **extra: Any) -> None:
        if not path.is_file():
            return
        row = {"path": _rel(root, path), "role": role, "present": True}
        row.update(extra)
        bucket.append(row)

    # Upstream inputs the portfolio layer typically consults (when present).
    _add(q_path, "operator_queue_latest", input_artifacts)
    if queue and queue.get("schema") == OPERATOR_QUEUE_SCHEMA:
        for e in (queue.get("entries") or [])[:24]:
            if not isinstance(e, dict):
                continue
            pid = str(e.get("product_id") or "").strip()
            if not pid:
                continue
            sp = operator_snapshot_json_path(root, pid)
            _add(sp, "operator_snapshot_per_product", input_artifacts, product_id=pid)

    # Outputs written by portfolio subcommands / cycle.
    _add(cycle_path, "portfolio_cycle_latest", output_artifacts)
    _add(prog_path, "portfolio_progression_latest", output_artifacts)
    _add(quies_path, "portfolio_quiescence_latest", output_artifacts)
    _add(delta_path, "portfolio_delta_report_latest", output_artifacts)
    _add(inv_path, "portfolio_intervention_latest", output_artifacts)

    cycle_summary = None
    stages_replay = None
    overall_ok = None
    failure_stages: list[str] = []

    if cycle and cycle.get("schema") == PORTFOLIO_CYCLE_SCHEMA:
        cycle_summary = {
            "run_id": cycle.get("run_id"),
            "generated_at_utc": cycle.get("generated_at_utc"),
            "ok": cycle.get("ok"),
            "overall_operator_recommendation": (cycle.get("summary") or {}).get("overall_operator_recommendation"),
            "overall_rationale_codes": (cycle.get("summary") or {}).get("overall_rationale_codes"),
        }
        stages_replay = cycle.get("stages")
        overall_ok = cycle.get("ok")
        st = cycle.get("stages") or {}
        for name, row in st.items():
            if isinstance(row, dict) and row.get("status") == "error":
                failure_stages.append(str(name))

    reason_codes: list[str] = []
    if isinstance(quiescence, dict) and quiescence.get("schema") == PORTFOLIO_QUIESCENCE_SCHEMA:
        for c in quiescence.get("quiescence_reason_codes") or []:
            if str(c).strip():
                reason_codes.append(str(c))
    if isinstance(delta_report, dict) and delta_report.get("schema") == PORTFOLIO_DELTA_REPORT_SCHEMA:
        for c in delta_report.get("delta_reason_codes") or []:
            if str(c).strip():
                reason_codes.append(str(c))
    reason_codes = sorted(set(reason_codes))

    summaries = {
        "operator_queue": _excerpt_queue(queue),
        "portfolio_progression": _excerpt_progression(progression),
        "quiescence": _excerpt_quiescence(quiescence),
        "delta_report": _excerpt_delta(delta_report),
        "intervention": _excerpt_intervention(intervention),
        "cycle": cycle_summary,
    }

    completeness = "none"
    if cycle:
        completeness = "full"
    elif queue or progression or quiescence or delta_report or intervention:
        completeness = "partial"
    else:
        completeness = "minimal"

    why_parts: list[str] = []
    if cycle:
        if overall_ok:
            why_parts.append("Latest portfolio cycle artifact recorded all stages as successful.")
        else:
            why_parts.append(
                "Latest portfolio cycle completed with at least one stage error — see `failure_stages` and cycle JSON."
            )
        if failure_stages:
            why_parts.append(f"Failed stages: {', '.join(failure_stages)}.")
    elif queue or progression:
        why_parts.append(
            "No stamped cycle bundle found; replay is assembled from latest operator queue / progression / quiescence / delta / intervention only."
        )
    else:
        why_parts.append("No portfolio artifacts present under runs/portfolio/.")

    if isinstance(quiescence, dict):
        why_parts.append(f"Quiescence recommendation was {quiescence.get('recommendation')!r} when last written.")

    return {
        "schema": PORTFOLIO_REPLAY_SCHEMA,
        "run_id": run_id,
        "generated_at_utc": generated_at,
        "read_only": True,
        "completeness": completeness,
        "inputs": {"artifacts": input_artifacts},
        "outputs": {"artifacts": output_artifacts},
        "cycle_replay_summary": cycle_summary,
        "stage_status_replay": stages_replay,
        "failure_stages": failure_stages,
        "summaries": summaries,
        "reason_codes_surfaced": reason_codes,
        "why_this_state_likely_occurred": " ".join(why_parts),
    }


def _excerpt_queue(q: dict[str, Any] | None) -> dict[str, Any] | None:
    if not q or q.get("schema") != OPERATOR_QUEUE_SCHEMA:
        return None
    return {
        "schema": q.get("schema"),
        "generated_at_utc": q.get("generated_at_utc"),
        "entry_count": len(q.get("entries") or []),
        "top_product_ids": [
            str(e.get("product_id"))
            for e in (q.get("entries") or [])[:8]
            if isinstance(e, dict) and e.get("product_id")
        ],
    }


def _excerpt_progression(p: dict[str, Any] | None) -> dict[str, Any] | None:
    if not p or p.get("schema") != PORTFOLIO_PROGRESSION_SCHEMA:
        return None
    return {
        "schema": p.get("schema"),
        "run_id": p.get("run_id"),
        "dry_run": p.get("dry_run"),
        "summary_counts": p.get("summary_counts"),
    }


def _excerpt_quiescence(q: dict[str, Any] | None) -> dict[str, Any] | None:
    if not q or q.get("schema") != PORTFOLIO_QUIESCENCE_SCHEMA:
        return None
    return {
        "schema": q.get("schema"),
        "recommendation": q.get("recommendation"),
        "portfolio_quiescent": q.get("portfolio_quiescent"),
        "products_with_material_change_count": len(q.get("products_with_material_change") or []),
    }


def _excerpt_delta(d: dict[str, Any] | None) -> dict[str, Any] | None:
    if not d or d.get("schema") != PORTFOLIO_DELTA_REPORT_SCHEMA:
        return None
    return {
        "schema": d.get("schema"),
        "run_id": d.get("run_id"),
        "recommended_next_portfolio_action": d.get("recommended_next_portfolio_action"),
    }


def _excerpt_intervention(i: dict[str, Any] | None) -> dict[str, Any] | None:
    if not i or i.get("schema") != PORTFOLIO_INTERVENTION_SCHEMA:
        return None
    return {
        "schema": i.get("schema"),
        "run_id": i.get("run_id"),
        "flagged_count": len(i.get("flagged_products") or []),
    }


def render_portfolio_replay_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Portfolio replay (read-only)",
        "",
        f"**Schema:** `{payload.get('schema')}`",
        f"**Completeness:** `{payload.get('completeness')}`",
        f"**Generated (UTC):** {payload.get('generated_at_utc')}",
        "",
        "## Input artifacts",
        "",
    ]
    for a in payload.get("inputs", {}).get("artifacts") or []:
        extra = f" ({a.get('product_id')})" if a.get("product_id") else ""
        lines.append(f"- `{a.get('path')}` — {a.get('role')}{extra}")
    if not (payload.get("inputs") or {}).get("artifacts"):
        lines.append("—")
    lines.extend(["", "## Output artifacts", ""])
    for a in payload.get("outputs", {}).get("artifacts") or []:
        lines.append(f"- `{a.get('path')}` — {a.get('role')}")
    if not (payload.get("outputs") or {}).get("artifacts"):
        lines.append("—")

    fs = payload.get("failure_stages") or []
    if fs:
        lines.extend(["", "## Stage failures (from cycle)", "", ", ".join(f"`{x}`" for x in fs), ""])

    rc = payload.get("reason_codes_surfaced") or []
    lines.extend(["", "## Reason codes surfaced", "", ", ".join(f"`{c}`" for c in rc) or "—", ""])

    lines.extend(
        [
            "## Why this state likely occurred",
            "",
            str(payload.get("why_this_state_likely_occurred") or "—"),
            "",
            "## Summaries",
            "",
            "```json",
            json.dumps(payload.get("summaries") or {}, indent=2, sort_keys=True)[:16000],
            "```",
            "",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def write_portfolio_replay_artifacts(
    repo_root: Path,
    payload: dict[str, Any],
    *,
    run_id: str | None = None,
) -> tuple[Path, Path, Path, Path]:
    root = repo_root.resolve()
    rid = run_id or str(payload.get("run_id") or "")
    if not rid:
        rid = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    pl = dict(payload)
    pl["run_id"] = rid
    d = portfolio_replay_dir(root)
    d.mkdir(parents=True, exist_ok=True)
    stamped_json = d / f"{rid}.json"
    stamped_md = d / f"{rid}.md"
    latest_json = d / "latest.json"
    latest_md = d / "latest.md"
    stamped_json.write_text(dumps_json(pl) + "\n", encoding="utf-8")
    stamped_md.write_text(render_portfolio_replay_markdown(pl), encoding="utf-8")
    shutil.copyfile(stamped_json, latest_json)
    shutil.copyfile(stamped_md, latest_md)
    return stamped_json, stamped_md, latest_json, latest_md


def run_portfolio_replay(
    repo_root: Path,
    *,
    write_artifacts: bool = True,
) -> dict[str, Any]:
    payload = replay_portfolio(repo_root)
    if write_artifacts:
        write_portfolio_replay_artifacts(repo_root, payload, run_id=payload.get("run_id"))
    return payload
