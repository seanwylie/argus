"""
Emit ``runs/escalations/`` packets when Builder reconcile detects serious anomalies.

Conservative, deterministic triggers — no ML. Dedupe uses :func:`argus.escalation.dedupe.find_recent_duplicate_packet`.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from argus.escalation.builder_rule_ids import (
    RULE_BUILDER_ARGUS_CORE_BREACH,
    RULE_BUILDER_EXECUTION_OUTCOME_BLOCKED,
    RULE_BUILDER_EXECUTION_OUTCOME_BREACHED,
    RULE_BUILDER_NON_PRODUCT_ROOT_BREACH,
    RULE_BUILDER_SEMANTIC_SCOPE_BREACH,
    RULE_BUILDER_TRUST_DIRTY_TREE,
    RULE_BUILDER_TRUST_MISSING_NO_NEW_PRIVS,
    RULE_BUILDER_TRUST_UNSANDBOXED,
)
from argus.escalation.dedupe import find_recent_duplicate_packet
from argus.escalation.models import (
    PACKET_SCHEMA,
    EscalationPacket,
    RiskLevel,
    SourceType,
)
from argus.escalation.packet import default_options, latest_dir, new_packet_id, save_packet
from argus.escalation.rules import TriggerMatch, max_risk_for_matches

BUILDER_ESCALATION_EMIT_SCHEMA = "argus.builder.escalation_emit.v1"

_LAST_ESCALATION_EMPTY: dict[str, Any] = {
    "present": False,
    "severity": None,
    "short_reason": "",
    "packet_id": None,
    "path_repo": None,
    "operator_visible": False,
    "title": None,
}


def _path_scope_payload(scope_check: dict[str, Any] | None) -> dict[str, Any]:
    if not scope_check or not isinstance(scope_check, dict):
        return {}
    if scope_check.get("schema") == "argus.builder_scope_check.v2":
        ps = scope_check.get("path_scope")
        return ps if isinstance(ps, dict) else {}
    if scope_check.get("schema") == "argus.builder_scope_check.v1":
        return scope_check
    return {}


def evaluate_builder_escalation_triggers(
    *,
    scope_check: dict[str, Any] | None,
    execution_outcome: dict[str, Any] | None,
    invoke_data: dict[str, Any] | None,
) -> list[TriggerMatch]:
    """
    Return non-empty matches when Builder signals warrant operator attention.

    Does not fire on clean runs or unknown/benign outcomes.
    """
    out: list[TriggerMatch] = []
    sc = scope_check if isinstance(scope_check, dict) else None
    ps = _path_scope_payload(sc)

    if ps.get("argus_core_breach"):
        br = "; ".join((sc.get("breach_reasons") or [])[:6]) if sc else ""
        out.append(
            TriggerMatch(
                RULE_BUILDER_ARGUS_CORE_BREACH,
                f"path_scope: Argus core (argus/) modified — {br or 'see builder_scope_check.path_scope'}",
            )
        )
    if ps.get("non_product_root_breach"):
        br = "; ".join((sc.get("breach_reasons") or [])[:6]) if sc else ""
        out.append(
            TriggerMatch(
                RULE_BUILDER_NON_PRODUCT_ROOT_BREACH,
                f"path_scope: changes outside product tree — {br or 'see builder_scope_check'}",
            )
        )

    if sc and sc.get("semantic_scope_breach"):
        sem = sc.get("semantic_scope") if isinstance(sc.get("semantic_scope"), dict) else {}
        srs = sem.get("semantic_breach_reasons") or []
        out.append(
            TriggerMatch(
                RULE_BUILDER_SEMANTIC_SCOPE_BREACH,
                f"semantic scope: {'; '.join(str(x) for x in srs[:4]) or 'primary_target mismatch vs task'}",
            )
        )

    eo = execution_outcome if isinstance(execution_outcome, dict) else None
    if eo:
        oc = (eo.get("outcome") or "").strip().lower()
        if oc == "blocked":
            rs = "; ".join(str(x) for x in (eo.get("reasons") or [])[:6])
            out.append(
                TriggerMatch(
                    RULE_BUILDER_EXECUTION_OUTCOME_BLOCKED,
                    f"execution_outcome blocked — {rs or 'see execution_outcome'}",
                )
            )
        elif oc == "breached":
            rs = "; ".join(str(x) for x in (eo.get("reasons") or [])[:6])
            out.append(
                TriggerMatch(
                    RULE_BUILDER_EXECUTION_OUTCOME_BREACHED,
                    f"execution_outcome breached — {rs or 'see execution_outcome'}",
                )
            )

    inv = invoke_data if isinstance(invoke_data, dict) else None
    if inv and (inv.get("execution_backend") or "").strip().lower() == "agent" and inv.get("mode") == "execute":
        bc = inv.get("builder_containment") if isinstance(inv.get("builder_containment"), dict) else {}
        if bc.get("trust_degraded_unsandboxed") or bc.get("containment_fallback_used"):
            out.append(
                TriggerMatch(
                    RULE_BUILDER_TRUST_UNSANDBOXED,
                    "agent containment: unsandboxed or bubblewrap fallback (trust degraded)",
                )
            )
        if bc.get("trust_degraded_missing_no_new_privs"):
            out.append(
                TriggerMatch(
                    RULE_BUILDER_TRUST_MISSING_NO_NEW_PRIVS,
                    "agent containment: setpriv/no_new_privs not applied on Linux (trust degraded)",
                )
            )
        gbi = inv.get("git_branch_isolation") if isinstance(inv.get("git_branch_isolation"), dict) else {}
        if gbi.get("trust_degraded_dirty_tree"):
            out.append(
                TriggerMatch(
                    RULE_BUILDER_TRUST_DIRTY_TREE,
                    "git branch isolation: working tree was dirty before builder branch (trust degraded)",
                )
            )

    seen: set[str] = set()
    deduped: list[TriggerMatch] = []
    for m in out:
        if m.rule_id not in seen:
            seen.add(m.rule_id)
            deduped.append(m)
    return deduped


def _short_reason_from_packet(data: dict[str, Any]) -> str:
    why = str(data.get("why_stopped") or "").strip()
    if why:
        line = why.splitlines()[0].strip()
        if len(line) > 280:
            return line[:277] + "..."
        return line
    summ = str(data.get("summary") or "").strip()
    if summ:
        return summ[:280] + ("..." if len(summ) > 280 else "")
    t = str(data.get("title") or "").strip()
    return t[:280] + ("..." if len(t) > 280 else "") if t else ""


def _packet_sort_key(data: dict[str, Any], path: Path) -> tuple[float, float]:
    """Prefer created_at (newer first), then mtime."""
    ca = data.get("created_at")
    ts = 0.0
    if isinstance(ca, str) and ca.strip():
        try:
            raw = ca.strip()
            if raw.endswith("Z"):
                raw = raw[:-1] + "+00:00"
            dt = datetime.fromisoformat(raw)
            ts = dt.timestamp()
        except (TypeError, ValueError, OSError):
            ts = 0.0
    return (-ts, -path.stat().st_mtime_ns)


def summarize_latest_builder_escalation(repo_root: Path, product_id: str) -> dict[str, Any]:
    """
    Latest ``runs/escalations/latest`` packet from Builder (``metadata.builder_escalation``)
    for ``product_id``, if any.

    ``operator_visible`` is True when packet ``risk_level`` is **critical** or **high** (matches
    escalation inbox mirroring); medium/low are still reported here for CLI/status without inbox noise.
    """
    root = repo_root.resolve()
    pid = str(product_id or "").strip()
    lat = latest_dir(root)
    if not pid or not lat.is_dir():
        return dict(_LAST_ESCALATION_EMPTY)

    best: tuple[tuple[float, float], dict[str, Any], Path] | None = None
    for p in lat.glob("esc_*.json"):
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(raw, dict):
            continue
        if str(raw.get("product_id") or "").strip() != pid:
            continue
        md = raw.get("metadata") if isinstance(raw.get("metadata"), dict) else {}
        if not md.get("builder_escalation"):
            continue
        key = _packet_sort_key(raw, p)
        if best is None or key < best[0]:
            best = (key, raw, p)

    if best is None:
        return dict(_LAST_ESCALATION_EMPTY)

    _k, data, path = best
    rl = str(data.get("risk_level") or "").strip().lower()
    op_vis = rl in ("critical", "high")
    try:
        rel = str(path.relative_to(root)).replace("\\", "/")
    except ValueError:
        rel = str(path).replace("\\", "/")
    reason = _short_reason_from_packet(data)
    return {
        "present": True,
        "severity": rl or None,
        "short_reason": reason,
        "packet_id": str(data.get("packet_id") or path.stem),
        "path_repo": rel,
        "operator_visible": op_vis,
        "title": str(data.get("title") or "") or None,
    }


def list_operator_visible_builder_packets(repo_root: Path) -> list[dict[str, Any]]:
    """
    Newest-first rows for Builder packets with ``risk_level`` **critical** or **high** only
    (for ``escalation_inbox`` mirroring).
    """
    root = repo_root.resolve()
    lat = latest_dir(root)
    if not lat.is_dir():
        return []
    rows: list[tuple[tuple[float, float], dict[str, Any]]] = []
    for p in lat.glob("esc_*.json"):
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(raw, dict):
            continue
        md = raw.get("metadata") if isinstance(raw.get("metadata"), dict) else {}
        if not md.get("builder_escalation"):
            continue
        rl = str(raw.get("risk_level") or "").strip().lower()
        if rl not in ("critical", "high"):
            continue
        pid = str(raw.get("product_id") or "").strip()
        pkt_id = str(raw.get("packet_id") or p.stem)
        try:
            rel = str(p.relative_to(root)).replace("\\", "/")
        except ValueError:
            rel = str(p).replace("\\", "/")
        key = _packet_sort_key(raw, p)
        rows.append(
            (
                key,
                {
                    "product_id": pid,
                    "packet_id": pkt_id,
                    "risk_level": rl,
                    "title": str(raw.get("title") or ""),
                    "why_stopped": str(raw.get("why_stopped") or ""),
                    "summary": str(raw.get("summary") or ""),
                    "created_at": raw.get("created_at"),
                    "path_repo": rel,
                },
            )
        )
    rows.sort(key=lambda x: x[0])
    return [r[1] for r in rows]


def build_builder_escalation_packet(
    *,
    product_id: str,
    matches: list[TriggerMatch],
    reconcile_record: dict[str, Any],
    reconcile_path_repo: str,
    invoke_path_repo: str | None,
    branch_review: dict[str, Any] | None,
) -> EscalationPacket:
    """Construct :class:`EscalationPacket` for Builder triggers (``matches`` non-empty)."""
    pid = str(product_id or "").strip()
    packet_id = new_packet_id(pid)
    created = datetime.now(timezone.utc)
    risk_s = max_risk_for_matches(matches)
    try:
        risk = RiskLevel(risk_s)
    except ValueError:
        risk = RiskLevel.HIGH

    rule_ids = [m.rule_id for m in matches]
    why = "\n".join(f"- [{m.rule_id}] {m.detail}" for m in matches)

    brs = branch_review if isinstance(branch_review, dict) else {}
    title = f"Builder anomaly: {pid}"
    summary = (
        f"Builder reconcile fired {len(matches)} escalation rule(s) for product {pid!r}: "
        f"{', '.join(rule_ids)}. "
        f"Review merge readiness: {brs.get('review_status', 'unknown')}."
    )

    ctx: list[str] = [reconcile_path_repo]
    if invoke_path_repo:
        ctx.append(invoke_path_repo)
    ctx.append(f"products/{pid}/")
    ctx.append("docs/builder-execution-contract.md")

    notes = (
        "Automatic escalation from Builder reconcile (argus.builder.escalation_bridge). "
        "Commands under options are suggestions only. "
        "Inspect scope_check, execution_outcome, and invoke record before merging."
    )

    meta: dict[str, Any] = {
        "schema": PACKET_SCHEMA,
        "builder_escalation": True,
        "builder_review_status": brs.get("review_status"),
        "builder_review_reasons_sample": (brs.get("review_reasons") or [])[:12],
        "artifact_links": {
            "reconcile_latest": reconcile_path_repo,
            "invoke_latest": invoke_path_repo,
        },
        "scope_breach_reasons_sample": (reconcile_record.get("builder_scope_check") or {}).get(
            "breach_reasons", []
        )[:12]
        if isinstance(reconcile_record.get("builder_scope_check"), dict)
        else [],
    }

    return EscalationPacket(
        packet_id=packet_id,
        created_at=created,
        product_id=pid,
        source_type=SourceType.POLICY,
        source_id="builder_reconcile",
        stopped_stage="builder_reconcile",
        title=title,
        summary=summary,
        why_stopped=why,
        risk_level=risk,
        triggering_rules=rule_ids,
        options=default_options(pid),
        context_files=ctx,
        notes=notes,
        metadata=meta,
    )


def emit_builder_escalation_if_needed(
    repo_root: Path,
    product_id: str,
    reconcile_record: dict[str, Any],
    *,
    invoke_data: dict[str, Any] | None = None,
    dedupe_hours: float = 24.0,
) -> dict[str, Any]:
    """
    If triggers fire and not a recent duplicate, write an escalation packet under ``runs/escalations/``.

    Returns a small dict suitable for ``builder_escalation_emit`` on the reconcile record.
    """
    root = repo_root.resolve()
    pid = str(product_id or "").strip()
    sc = reconcile_record.get("builder_scope_check")
    eo = reconcile_record.get("execution_outcome")
    sip = reconcile_record.get("source_invoke_record_path")
    inv_path = sip.strip() if isinstance(sip, str) else None
    inv = invoke_data
    if inv is None:
        if isinstance(inv_path, str) and inv_path.strip():
            p = (root / inv_path).resolve() if not Path(inv_path).is_absolute() else Path(inv_path)
            if p.is_file():
                try:
                    raw = json.loads(p.read_text(encoding="utf-8"))
                    inv = raw if isinstance(raw, dict) else None
                except (OSError, json.JSONDecodeError):
                    inv = None

    matches = evaluate_builder_escalation_triggers(
        scope_check=sc if isinstance(sc, dict) else None,
        execution_outcome=eo if isinstance(eo, dict) else None,
        invoke_data=inv,
    )
    if not matches:
        return {
            "schema": BUILDER_ESCALATION_EMIT_SCHEMA,
            "emitted": False,
            "reason": "no_triggers",
            "triggering_rules": [],
        }

    rule_ids = [m.rule_id for m in matches]
    dup = find_recent_duplicate_packet(root, pid, rule_ids, hours=dedupe_hours)
    if dup:
        return {
            "schema": BUILDER_ESCALATION_EMIT_SCHEMA,
            "emitted": False,
            "reason": "dedupe_recent_packet",
            "triggering_rules": rule_ids,
            "duplicate_of": dup,
        }

    rel_reconcile = f"runs/builder/reconcile/{pid}/latest.json"
    rel_invoke: str | None = None
    if isinstance(inv_path, str) and inv_path.strip():
        try:
            rel_invoke = str(Path(inv_path).as_posix())
        except Exception:  # noqa: BLE001
            rel_invoke = inv_path

    brv = reconcile_record.get("builder_branch_review")
    pkt = build_builder_escalation_packet(
        product_id=pid,
        matches=matches,
        reconcile_record=reconcile_record,
        reconcile_path_repo=rel_reconcile,
        invoke_path_repo=rel_invoke,
        branch_review=brv if isinstance(brv, dict) else None,
    )
    out_path = save_packet(root, pkt)
    try:
        path_repo = str(out_path.relative_to(root))
    except ValueError:
        path_repo = str(out_path)
    return {
        "schema": BUILDER_ESCALATION_EMIT_SCHEMA,
        "emitted": True,
        "reason": None,
        "packet_id": pkt.packet_id,
        "path_repo": path_repo.replace("\\", "/"),
        "triggering_rules": rule_ids,
        "risk_level": pkt.risk_level.value,
        "duplicate_of": None,
    }
