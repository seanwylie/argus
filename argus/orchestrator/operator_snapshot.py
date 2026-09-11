"""
Per-product operator snapshot — one consolidated view of Argus judgment for a product.

Writes ``runs/orchestration/operator_snapshot/<product_id>.json`` and a Markdown companion
when orchestration state is persisted (see :func:`write_operator_snapshot_artifacts`).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from argus.core.serialize import dumps_json
from argus.decision.persistence import latest_product_path as decisions_latest_path
from argus.mission.provenance import (
    build_mission_context_for_product,
    mission_context_markdown_lines,
)

OPERATOR_SNAPSHOT_SCHEMA = "argus.operator_snapshot.v1"


def operator_snapshot_json_path(repo_root: Path, product_id: str) -> Path:
    return Path(repo_root).resolve() / "runs" / "orchestration" / "operator_snapshot" / f"{product_id}.json"


def operator_snapshot_md_path(repo_root: Path, product_id: str) -> Path:
    return Path(repo_root).resolve() / "runs" / "orchestration" / "operator_snapshot" / f"{product_id}.md"


def _read_top_decision_title(repo_root: Path, product_id: str) -> str | None:
    p = decisions_latest_path(repo_root, product_id)
    if not p.is_file():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    cands = data.get("candidates") or []
    if not isinstance(cands, list) or not cands:
        return None
    top = cands[0]
    if not isinstance(top, dict):
        return None
    t = top.get("title")
    return str(t).strip() if t is not None and str(t).strip() else None


def _ensure_readiness(
    repo_root: Path,
    product_id: str,
    state: dict[str, Any],
) -> dict[str, Any]:
    r = state.get("readiness")
    if isinstance(r, dict) and r.get("schema"):
        return r
    from argus.orchestrator.readiness import build_readiness_section
    from argus.products.loader import load_yaml_file

    import_state = None
    raw, _ = load_yaml_file(repo_root / "products" / product_id / "product.yaml")
    if isinstance(raw, dict):
        ext = raw.get("raw_extensions")
        if isinstance(ext, dict):
            import_state = ext.get("import_state")
            if not isinstance(import_state, dict):
                import_state = None

    ef = state.get("eligibility_facts") if isinstance(state.get("eligibility_facts"), dict) else {}
    tier = ef.get("import_readiness_tier")
    if not isinstance(tier, str):
        tier = None

    return build_readiness_section(
        product_id=product_id,
        root=repo_root,
        import_state=import_state,
        import_readiness_tier=tier,
        import_health=state.get("import_health") if isinstance(state.get("import_health"), dict) else {},
        artifacts=state.get("artifacts") if isinstance(state.get("artifacts"), dict) else {},
        eligibility_facts=ef,
        waiting_inputs=state.get("waiting_inputs") if isinstance(state.get("waiting_inputs"), list) else [],
        orchestration_status=str(state.get("orchestration_status") or ""),
        next_action=str(state.get("next_action") or "none"),
    )


def build_artifact_freshness_summary(state: dict[str, Any]) -> dict[str, Any]:
    """Bounded summary of artifact phases/staleness plus key eligibility flags."""
    arts = state.get("artifacts") if isinstance(state.get("artifacts"), dict) else {}
    ef = state.get("eligibility_facts") if isinstance(state.get("eligibility_facts"), dict) else {}
    out: dict[str, Any] = {"bundles": {}, "eligibility_flags": {}}

    _keys = (
        "signals",
        "temporal",
        "audit",
        "execution",
        "refinement",
        "implementation_plan",
        "product_spec",
    )
    for key in _keys:
        sub = arts.get(key)
        if isinstance(sub, dict):
            out["bundles"][key] = {
                k: sub.get(k)
                for k in (
                    "phase",
                    "staleness",
                    "collected_at_utc",
                    "generated_at_utc",
                    "worst_freshness_status",
                    "path",
                    "record_count",
                )
                if k in sub
            }
        else:
            out["bundles"][key] = None

    for k in (
        "signals_collection_time_stale",
        "signals_refresh_needed",
        "temporal_worst_freshness_status",
        "temporal_freshness_stale",
        "audit_bundle_time_stale",
        "audit_product_gap_incomplete",
        "audit_security_stub",
    ):
        if k in ef:
            out["eligibility_flags"][k] = ef.get(k)

    return out


def build_waiting_blocking(state: dict[str, Any]) -> dict[str, Any]:
    wi = state.get("waiting_inputs") if isinstance(state.get("waiting_inputs"), list) else []
    bl = state.get("blockers") if isinstance(state.get("blockers"), list) else []
    codes = state.get("orchestration_status_reason_codes")
    if not isinstance(codes, list):
        codes = []
    return {
        "orchestration_status": state.get("orchestration_status"),
        "orchestration_status_reason": state.get("orchestration_status_reason"),
        "orchestration_status_reason_codes": codes,
        "overall_status": state.get("overall_status"),
        "waiting_inputs": wi,
        "blockers": bl,
    }


def build_decision_summary(
    repo_root: Path,
    product_id: str,
    readiness: dict[str, Any],
) -> dict[str, Any]:
    metrics = readiness.get("metrics") if isinstance(readiness.get("metrics"), dict) else {}
    return {
        "confidence_gate": readiness.get("confidence_gate"),
        "top_decision_confidence": metrics.get("top_decision_confidence"),
        "advisor_conflict": metrics.get("advisor_conflict"),
        "confidence_threshold": metrics.get("confidence_threshold"),
        "top_decision_title": _read_top_decision_title(repo_root, product_id),
    }


def build_operator_recommendation(state: dict[str, Any], readiness: dict[str, Any]) -> str:
    hint = str(readiness.get("readiness_policy_hint") or "").strip()
    na = str(state.get("next_action") or "none").strip()
    gate = str(readiness.get("confidence_gate") or "caution").strip()
    orch = str(state.get("orchestration_status") or "").strip()
    wi = state.get("waiting_inputs") if isinstance(state.get("waiting_inputs"), list) else []
    if wi:
        kinds: list[str] = []
        for w in wi:
            if isinstance(w, dict) and w.get("kind"):
                kinds.append(str(w["kind"]))
        uniq = sorted(set(kinds))[:8]
        wait_s = ", ".join(uniq) if uniq else "non-empty waiting_inputs"
        return (
            f"Confidence gate: {gate}. Policy hint: {hint or '—'}. "
            f"Orchestration status: {orch or '—'}. "
            f"Waiting on: {wait_s}. "
            f"Suggested next action: {na}."
        )
    if na.lower() not in ("none", ""):
        return (
            f"Confidence gate: {gate}. Policy hint: {hint or '—'}. "
            f"Orchestration status: {orch or '—'}. "
            f"Run `{na}` when appropriate."
        )
    return (
        f"Confidence gate: {gate}. Policy hint: {hint or '—'}. "
        f"Orchestration status: {orch or '—'}. "
        f"No single next action (next_action={na})."
    )


def build_operator_snapshot(
    repo_root: Path,
    orchestration_state: dict[str, Any],
    *,
    product_id: str | None = None,
) -> dict[str, Any]:
    """
    Build the operator snapshot payload from a full ``argus.orchestration_state.v1`` dict.

    If ``readiness`` is missing (legacy callers), it is recomputed via
    :func:`argus.orchestrator.readiness.build_readiness_section` when possible.
    """
    pid = product_id or str(orchestration_state.get("product_id") or "").strip()
    if not pid:
        raise ValueError("operator snapshot requires product_id")

    readiness = _ensure_readiness(repo_root, pid, orchestration_state)
    debt = readiness.get("understanding_debt")
    if debt is None:
        debt = 0.0

    na_pol = orchestration_state.get("next_action_policy")
    if not isinstance(na_pol, dict):
        na_pol = {}

    snap = {
        "schema": OPERATOR_SNAPSHOT_SCHEMA,
        "product_id": pid,
        "mission_context": build_mission_context_for_product(repo_root, pid),
        "snapshot_generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_evaluated_at_utc": orchestration_state.get("evaluated_at_utc"),
        "import_health": orchestration_state.get("import_health") if isinstance(orchestration_state.get("import_health"), dict) else {},
        "readiness": readiness,
        "understanding_debt": debt,
        "readiness_reason": orchestration_state.get("readiness_reason"),
        "next_action": orchestration_state.get("next_action"),
        "next_action_policy": na_pol,
        "orchestration_status": orchestration_state.get("orchestration_status"),
        "orchestration_status_reason": orchestration_state.get("orchestration_status_reason"),
        "decision_summary": build_decision_summary(repo_root, pid, readiness),
        "artifact_freshness_summary": build_artifact_freshness_summary(orchestration_state),
        "waiting_and_blocking": build_waiting_blocking(orchestration_state),
        "operator_recommendation": build_operator_recommendation(orchestration_state, readiness),
    }
    return snap


def render_operator_snapshot_markdown(payload: dict[str, Any]) -> str:
    """Human-readable companion for the JSON snapshot (deterministic sections)."""
    pid = payload.get("product_id", "")
    lines = [
        f"# Operator snapshot — `{pid}`",
        "",
        f"**Schema:** `{payload.get('schema')}`",
        "",
    ]
    mc = payload.get("mission_context") if isinstance(payload.get("mission_context"), dict) else {}
    lines.extend(mission_context_markdown_lines(mc))
    lines.extend(
        [
            f"- **Generated (UTC):** {payload.get('snapshot_generated_at_utc')}",
            f"- **Source orchestration evaluated_at_utc:** {payload.get('source_evaluated_at_utc')}",
            "",
            "## Summary",
            "",
            str(payload.get("operator_recommendation") or "").strip(),
            "",
            "## Readiness",
            "",
        ]
    )
    rd = payload.get("readiness") if isinstance(payload.get("readiness"), dict) else {}
    lines.extend(
        [
            f"- **Tier:** `{rd.get('readiness_tier')}`",
            f"- **Understanding debt:** `{rd.get('understanding_debt')}`",
            f"- **Confidence gate:** `{rd.get('confidence_gate')}`",
            f"- **Policy hint (string):** `{rd.get('readiness_policy_hint')}`",
            "",
        ]
    )
    rc = rd.get("readiness_reason_codes")
    if isinstance(rc, list) and rc:
        lines.append("**Reason codes:**")
        lines.append("")
        for c in rc:
            lines.append(f"- `{c}`")
        lines.append("")

    ds = payload.get("decision_summary") if isinstance(payload.get("decision_summary"), dict) else {}
    lines.extend(
        [
            "## Decision (top rank)",
            "",
            f"- **Title:** {ds.get('top_decision_title') or '—'}",
            f"- **Confidence:** {ds.get('top_decision_confidence')!s}",
            f"- **Advisor conflict:** {ds.get('advisor_conflict')!s}",
            "",
            "## Next action",
            "",
            f"- **next_action:** `{payload.get('next_action')}`",
            "",
        ]
    )
    nap = payload.get("next_action_policy") if isinstance(payload.get("next_action_policy"), dict) else {}
    if nap:
        lines.append("**next_action_policy (subset):**")
        lines.append("")
        for k in ("rule_applied", "ordering_basis", "tie_break", "action_family"):
            if k in nap:
                lines.append(f"- **{k}:** `{nap.get(k)}`")
        lines.append("")

    lines.extend(
        [
            "## Import health",
            "",
            "```json",
            dumps_json(payload.get("import_health") or {}),
            "```",
            "",
            "## Artifact freshness (summary)",
            "",
            "```json",
            dumps_json(payload.get("artifact_freshness_summary") or {}),
            "```",
            "",
            "## Waiting / blocking",
            "",
            "```json",
            dumps_json(payload.get("waiting_and_blocking") or {}),
            "```",
            "",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def write_operator_snapshot_artifacts(
    repo_root: Path,
    product_id: str,
    orchestration_state: dict[str, Any],
) -> tuple[Path, Path]:
    """
    Write JSON + Markdown operator snapshots under ``runs/orchestration/operator_snapshot/``.

    Call with the same payload written to ``runs/orchestration/latest/<product_id>.json``.
    """
    root = repo_root.resolve()
    payload = build_operator_snapshot(root, orchestration_state, product_id=product_id)
    out_dir = root / "runs" / "orchestration" / "operator_snapshot"
    out_dir.mkdir(parents=True, exist_ok=True)
    jpath = out_dir / f"{product_id}.json"
    mpath = out_dir / f"{product_id}.md"
    jpath.write_text(dumps_json(payload) + "\n", encoding="utf-8")
    mpath.write_text(render_operator_snapshot_markdown(payload), encoding="utf-8")
    return jpath, mpath
