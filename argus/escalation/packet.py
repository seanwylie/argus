"""Build escalation packets, persist under ``runs/escalations/``, load by id."""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from argus.core.models.decision import DecisionCandidate
from argus.core.models.finding import Finding
from argus.core.models.product import ProductNode
from argus.core.serialize import dumps_json, to_jsonable
from argus.decision.engine import generate_decisions
from argus.decision.intents import DecisionIntent
from argus.escalation.models import (
    PACKET_SCHEMA,
    EscalationOption,
    EscalationPacket,
    RiskLevel,
    SourceType,
)
from argus.escalation.orchestration_bridge import (
    matches_from_orchestration_triggers,
    merge_trigger_matches,
)
from argus.escalation.rules import (
    RULE_CONFIDENCE_TOO_LOW,
    RULE_COST_OVER_CEILING,
    RULE_KILL_OR_DEPRECATE,
    RULE_LIFECYCLE_CONTRADICTION,
    RULE_MISSING_BUSINESS_DATA,
    RULE_UNSAFE_CONTRACT,
    TriggerMatch,
    evaluate_triggers,
    max_risk_for_matches,
)
from argus.escalation.task_state import write_escalation_task_state
from argus.lifecycle.model import LifecycleAssessment


def escalations_dir(repo_root: Path) -> Path:
    return repo_root.resolve() / "runs" / "escalations"


def generations_dir(repo_root: Path) -> Path:
    return escalations_dir(repo_root) / "generations"


def latest_dir(repo_root: Path) -> Path:
    return escalations_dir(repo_root) / "latest"


def _safe_packet_suffix(product_id: str) -> str:
    return re.sub(r"[^a-zA-Z0-9._-]+", "_", product_id).strip("_") or "product"


def new_packet_id(product_id: str) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"esc_{ts}_{_safe_packet_suffix(product_id)}"


def default_options(product_id: str) -> list[EscalationOption]:
    """Standard handoff options (commands are documentation-only)."""
    pid = product_id
    return [
        EscalationOption(
            action="proceed_anyway",
            description="Approve continuing despite risk; record decision outside Argus if required.",
            command=f"# After approval: argus decisions generate {pid}",
            risk="high",
            requires_human_confirmation=True,
        ),
        EscalationOption(
            action="skip_this_product",
            description="Exclude this product from automated portfolio steps until reviewed.",
            command=f"# Skip: do not run execution hooks for {pid}; revisit after triage.",
            risk="low",
            requires_human_confirmation=False,
        ),
        EscalationOption(
            action="lower_threshold_and_retry",
            description="Adjust product.yaml constraints or policy, then re-run decisions.",
            command=f"# Edit products/<dir>/product.yaml, then: argus decisions generate {pid}",
            risk="medium",
            requires_human_confirmation=True,
        ),
        EscalationOption(
            action="gather_more_data",
            description="Collect signals/findings until confidence and cost posture are clear.",
            command=f"argus signals collect {pid} && argus findings generate {pid} --fresh-signals",
            risk="low",
            requires_human_confirmation=False,
        ),
        EscalationOption(
            action="abandon_action",
            description="Do not execute destructive recommendations; park or reverse course via runbook.",
            command=f"# No Argus auto-exec; archive intent for {pid} in your tracker.",
            risk="medium",
            requires_human_confirmation=True,
        ),
    ]


def _parse_created_at_value(raw: Any) -> datetime | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def _count_prior_escalations_in_window(
    repo_root: Path,
    product_id: str,
    *,
    days: int = 30,
) -> int:
    """Packets already on disk for this product in the rolling window (excludes not-yet-written)."""
    lat = latest_dir(repo_root)
    if not lat.is_dir():
        return 0
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    n = 0
    for path in lat.glob("esc_*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if data.get("product_id") != product_id:
            continue
        dt = _parse_created_at_value(data.get("created_at"))
        if dt is None:
            continue
        if dt >= cutoff:
            n += 1
    return n


def _recurrence_risk_score(prior_count: int) -> float:
    return min(1.0, prior_count / 4.0)


def _risk_score_value(assessment: LifecycleAssessment, risk_level: RiskLevel) -> float:
    kill = float(assessment.kill)
    rl_map = {
        RiskLevel.LOW: 0.2,
        RiskLevel.MEDIUM: 0.45,
        RiskLevel.HIGH: 0.72,
        RiskLevel.CRITICAL: 0.92,
    }
    rl = rl_map.get(risk_level, 0.5)
    return max(0.0, min(1.0, 0.55 * kill + 0.45 * rl))


def _confidence_pair(candidates: list[DecisionCandidate]) -> tuple[float | None, float | None]:
    if not candidates:
        return None, None
    top = candidates[0]
    conf = top.confidence
    if conf is None:
        return None, 0.88
    c = max(0.0, min(1.0, float(conf)))
    return c, max(0.0, min(1.0, 1.0 - c))


def _uncertainty_factors(matches: list[TriggerMatch]) -> list[str]:
    want = {
        RULE_MISSING_BUSINESS_DATA,
        RULE_CONFIDENCE_TOO_LOW,
        RULE_LIFECYCLE_CONTRADICTION,
    }
    out: list[str] = []
    for m in matches:
        if m.rule_id in want:
            out.append(f"[{m.rule_id}] {m.detail[:220]}")
    return out[:8]


def _blocking_factors(matches: list[TriggerMatch]) -> list[str]:
    want = {
        RULE_COST_OVER_CEILING,
        RULE_KILL_OR_DEPRECATE,
        RULE_UNSAFE_CONTRACT,
        RULE_CONFIDENCE_TOO_LOW,
    }
    out: list[str] = []
    for m in matches:
        if m.rule_id in want:
            out.append(f"[{m.rule_id}] {m.detail[:220]}")
    return out[:8]


def _exploratory_fields(
    candidates: list[DecisionCandidate],
    matches: list[TriggerMatch],
) -> tuple[str | None, str | None]:
    considered: str | None = None
    for c in candidates[:6]:
        intent = (c.metadata or {}).get("intent")
        if intent in (
            DecisionIntent.LAUNCH_EXPERIMENT.value,
            DecisionIntent.IMPROVE_PRODUCT.value,
        ):
            considered = (
                f"Candidate `{c.id}` intent={intent!r} — {c.summary[:200]}"
            )
            break

    rule_ids = {m.rule_id for m in matches}
    rejected: str | None = None
    if considered:
        if RULE_CONFIDENCE_TOO_LOW in rule_ids:
            rejected = (
                "Leap or improvement path appears in ranked decisions, but automation confidence "
                "or policy blocked unsupervised execution — human gate applies."
            )
        elif {RULE_KILL_OR_DEPRECATE, RULE_LIFECYCLE_CONTRADICTION} & rule_ids:
            rejected = (
                "Experiment or growth intent is present, but lifecycle/kill posture conflicts — "
                "resolve posture before taking a leap-of-faith bet."
            )
    elif RULE_CONFIDENCE_TOO_LOW in rule_ids:
        rejected = (
            "Top-ranked high-stakes or destructive path is below the automation confidence floor; "
            "Argus will not proceed without explicit human approval."
        )
    return considered, rejected


def _escalation_pressure(u: float | None, r: float | None, rec: float | None) -> float | None:
    vals = [x for x in (u, r, rec) if x is not None]
    if not vals:
        return None
    return max(0.0, min(1.0, sum(vals) / len(vals)))


def _decision_confidence_enrichment(
    repo_root: Path | None,
    product_id: str,
    assessment: LifecycleAssessment,
    candidates: list[DecisionCandidate],
    matches: list[TriggerMatch],
    risk_level: RiskLevel,
) -> dict[str, Any]:
    """
    Prefer :mod:`argus.decision_assessment` when repo artifacts are available; fall back to
    lightweight heuristics from ranked candidates + triggers.
    """
    prior = 0
    if repo_root is not None:
        prior = _count_prior_escalations_in_window(repo_root.resolve(), product_id)
    tu_trig = _uncertainty_factors(matches)
    tb = _blocking_factors(matches)
    ex_c, ex_r = _exploratory_fields(candidates, matches)

    if repo_root is not None:
        try:
            from argus.decision_assessment.evaluate import evaluate_decision_context

            ev = evaluate_decision_context(repo_root, product_id)
            tu = list(
                dict.fromkeys(
                    [f.label for f in ev.uncertainty_factors[:8]] + list(tu_trig)
                )
            )[:12]
            ex_c2 = ev.exploratory_action_reason if ev.exploratory_action_recommended else ex_c
            return {
                "confidence_score": ev.confidence_score,
                "uncertainty_score": ev.uncertainty_score,
                "risk_score": ev.risk_score,
                "recurrence_risk_score": ev.recurrence_risk_score,
                "escalation_pressure": ev.escalation_pressure,
                "top_uncertainty_factors": tu,
                "top_blocking_factors": tb,
                "exploratory_action_considered": ex_c2,
                "exploratory_action_rejected_reason": ex_r,
                "prior_escalations_in_window": prior,
            }
        except (OSError, ValueError):
            pass

    conf, unc = _confidence_pair(candidates)
    risk_s = _risk_score_value(assessment, risk_level)
    rec_r: float | None = _recurrence_risk_score(prior) if repo_root is not None else None
    pressure = _escalation_pressure(unc, risk_s, rec_r)
    return {
        "confidence_score": conf,
        "uncertainty_score": unc,
        "risk_score": risk_s,
        "recurrence_risk_score": rec_r,
        "escalation_pressure": pressure,
        "top_uncertainty_factors": tu_trig,
        "top_blocking_factors": tb,
        "exploratory_action_considered": ex_c,
        "exploratory_action_rejected_reason": ex_r,
        "prior_escalations_in_window": prior,
    }


def _infer_source_type(matches: list[TriggerMatch]) -> tuple[SourceType, str]:
    ids = {m.rule_id for m in matches}
    if len(ids) <= 1 and ids:
        only = next(iter(ids))
        if only == RULE_KILL_OR_DEPRECATE:
            return SourceType.LIFECYCLE, "lifecycle_assessment"
        if only == RULE_COST_OVER_CEILING:
            return SourceType.FINDING, "cost_signals"
        if only == RULE_MISSING_BUSINESS_DATA:
            return SourceType.FINDING, "findings_bundle"
    if not ids:
        return SourceType.POLICY, "policy"
    return SourceType.COMPOSITE, "multiple_triggers"


def build_packet(
    *,
    product: ProductNode,
    findings: list[Finding],
    assessment: LifecycleAssessment,
    candidates: list[DecisionCandidate],
    matches: list[TriggerMatch],
    repo_root: Path | None = None,
    orchestration_snapshot: dict[str, Any] | None = None,
) -> EscalationPacket:
    """Construct a packet from evaluated triggers (caller ensures matches non-empty)."""
    pid = product.id
    packet_id = new_packet_id(pid)
    created = datetime.now(timezone.utc)
    risk_s = max_risk_for_matches(matches)
    try:
        risk = RiskLevel(risk_s)
    except ValueError:
        risk = RiskLevel.HIGH

    rule_ids = [m.rule_id for m in matches]
    why = "\n".join(f"- [{m.rule_id}] {m.detail}" for m in matches)
    src_t, src_id = _infer_source_type(matches)

    enrich = _decision_confidence_enrichment(
        repo_root,
        pid,
        assessment,
        candidates,
        matches,
        risk,
    )
    prior_n = int(enrich.pop("prior_escalations_in_window", 0))

    title = f"Escalation required: {pid}"
    summary = (
        f"Argus policy halted before any automated action. "
        f"{len(matches)} trigger(s) fired: {', '.join(rule_ids)}."
    )
    cs = enrich.get("confidence_score")
    us = enrich.get("uncertainty_score")
    if cs is not None:
        summary += f" Top decision confidence: {cs:.2f}."
    elif us is not None:
        summary += " Top decision confidence: unknown (treat as high uncertainty)."
    if prior_n > 0:
        summary += f" Prior escalations for this product (last 30d, on disk): {prior_n}."

    ctx: list[str] = [
        f"products/{pid}/product.yaml",
        f"runs/findings/latest/{pid}.json",
        f"runs/decisions/latest/{pid}.json",
    ]

    notes = (
        "This packet is self-contained for humans and downstream systems. "
        "Commands listed under options are suggestions only — Argus does not execute them here. "
        f"Schema: {PACKET_SCHEMA}. "
        "Scores (confidence, uncertainty, pressure) are heuristic summaries from local artifacts — not live SLIs."
    )

    meta = {
        "schema": PACKET_SCHEMA,
        "lifecycle_stage": assessment.stage.value,
        "kill_candidate": assessment.kill_candidate,
        "top_intent": (candidates[0].metadata or {}).get("intent") if candidates else None,
        "prior_escalations_in_window_30d": prior_n,
        "decision_confidence_enrichment": True,
    }
    if orchestration_snapshot is not None:
        et = orchestration_snapshot.get("escalation_triggers")
        n_tr = len(et) if isinstance(et, list) else 0
        meta["orchestration_escalation_eligible"] = bool(
            orchestration_snapshot.get("escalation_eligible")
        )
        meta["orchestration_escalation_trigger_count"] = n_tr
        meta["orchestration_status"] = orchestration_snapshot.get("orchestration_status")

    return EscalationPacket(
        packet_id=packet_id,
        created_at=created,
        product_id=pid,
        source_type=src_t,
        source_id=src_id,
        stopped_stage="execution_gate",
        title=title,
        summary=summary,
        why_stopped=why,
        risk_level=risk,
        triggering_rules=rule_ids,
        options=default_options(pid),
        context_files=ctx,
        notes=notes,
        metadata=meta,
        confidence_score=enrich.get("confidence_score"),
        uncertainty_score=enrich.get("uncertainty_score"),
        risk_score=enrich.get("risk_score"),
        recurrence_risk_score=enrich.get("recurrence_risk_score"),
        escalation_pressure=enrich.get("escalation_pressure"),
        top_uncertainty_factors=list(enrich.get("top_uncertainty_factors") or []),
        top_blocking_factors=list(enrich.get("top_blocking_factors") or []),
        exploratory_action_considered=enrich.get("exploratory_action_considered"),
        exploratory_action_rejected_reason=enrich.get("exploratory_action_rejected_reason"),
    )


def packet_to_json_dict(packet: EscalationPacket) -> dict[str, Any]:
    """JSON-serializable dict (ISO datetimes, enum values)."""
    d = to_jsonable(packet)
    assert isinstance(d, dict)
    d["schema"] = PACKET_SCHEMA
    return d


def save_packet(repo_root: Path, packet: EscalationPacket) -> Path:
    """Write ``generations/<packet_id>.json`` and mirror to ``latest/<packet_id>.json``."""
    root = repo_root.resolve()
    gen = generations_dir(root)
    lat = latest_dir(root)
    gen.mkdir(parents=True, exist_ok=True)
    lat.mkdir(parents=True, exist_ok=True)
    name = f"{packet.packet_id}.json"
    payload = packet_to_json_dict(packet)
    text = dumps_json(payload)
    p_gen = gen / name
    p_lat = lat / name
    p_gen.write_text(text, encoding="utf-8")
    p_lat.write_text(text, encoding="utf-8")
    return p_lat


def _opt_float_val(raw: Any) -> float | None:
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _opt_str_val(raw: Any) -> str | None:
    if raw is None:
        return None
    s = str(raw).strip()
    return s or None


def packet_from_dict(d: dict[str, Any]) -> EscalationPacket:
    """Rebuild :class:`EscalationPacket` from JSON (e.g. for ``show``)."""
    opts_raw = d.get("options") or []
    options = [
        EscalationOption(
            action=str(x["action"]),
            description=str(x["description"]),
            command=str(x["command"]),
            risk=str(x["risk"]),
            requires_human_confirmation=bool(x["requires_human_confirmation"]),
        )
        for x in opts_raw
        if isinstance(x, dict)
    ]
    created = d.get("created_at")
    if isinstance(created, str):
        created_dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
    else:
        created_dt = datetime.now(timezone.utc)
    tu = d.get("top_uncertainty_factors")
    if not isinstance(tu, list):
        tu = []
    tb = d.get("top_blocking_factors")
    if not isinstance(tb, list):
        tb = []
    return EscalationPacket(
        packet_id=str(d["packet_id"]),
        created_at=created_dt,
        product_id=str(d["product_id"]),
        source_type=SourceType(str(d["source_type"])),
        source_id=str(d["source_id"]),
        stopped_stage=str(d["stopped_stage"]),
        title=str(d["title"]),
        summary=str(d["summary"]),
        why_stopped=str(d["why_stopped"]),
        risk_level=RiskLevel(str(d["risk_level"])),
        triggering_rules=list(d.get("triggering_rules") or []),
        options=options,
        context_files=list(d.get("context_files") or []),
        notes=str(d.get("notes") or ""),
        metadata=dict(d.get("metadata") or {}),
        confidence_score=_opt_float_val(d.get("confidence_score")),
        uncertainty_score=_opt_float_val(d.get("uncertainty_score")),
        risk_score=_opt_float_val(d.get("risk_score")),
        recurrence_risk_score=_opt_float_val(d.get("recurrence_risk_score")),
        escalation_pressure=_opt_float_val(d.get("escalation_pressure")),
        top_uncertainty_factors=[str(x) for x in tu if str(x).strip()],
        top_blocking_factors=[str(x) for x in tb if str(x).strip()],
        exploratory_action_considered=_opt_str_val(d.get("exploratory_action_considered")),
        exploratory_action_rejected_reason=_opt_str_val(d.get("exploratory_action_rejected_reason")),
    )


def load_packet(repo_root: Path, packet_id: str) -> dict[str, Any] | None:
    """Load JSON by packet id (with or without ``.json`` suffix)."""
    root = repo_root.resolve()
    stem = packet_id[:-5] if packet_id.endswith(".json") else packet_id
    for base in (latest_dir(root), generations_dir(root)):
        p = base / f"{stem}.json"
        if p.is_file():
            return json.loads(p.read_text(encoding="utf-8"))
    return None


def list_packets(repo_root: Path, *, limit: int = 200) -> list[dict[str, Any]]:
    """Newest-first lightweight index entries (packet_id, product_id, created_at, risk_level)."""
    root = repo_root.resolve()
    lat = latest_dir(root)
    if not lat.is_dir():
        return []
    rows: list[dict[str, Any]] = []
    for p in sorted(lat.glob("esc_*.json"), key=lambda x: x.stat().st_mtime_ns, reverse=True)[
        :limit
    ]:
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        rows.append(
            {
                "packet_id": data.get("packet_id", p.stem),
                "product_id": data.get("product_id"),
                "created_at": data.get("created_at"),
                "risk_level": data.get("risk_level"),
                "title": data.get("title"),
            }
        )
    return rows


def generate_packet_for_product(
    repo_root: Path,
    product: ProductNode,
    findings: list[Finding],
) -> tuple[EscalationPacket | None, list[TriggerMatch]]:
    """
    Run decision engine + trigger rules, merge orchestration-derived escalation triggers,
    and persist :func:`write_escalation_task_state`.

    Returns ``(packet, matches)`` if any trigger fired (policy or orchestration), else ``(None, [])``.
    """
    from argus.orchestrator.eligibility import evaluate_product_orchestration

    assessment, candidates = generate_decisions(product, findings, repo_root=repo_root)
    core_matches = evaluate_triggers(product, findings, assessment, candidates, repo_root=repo_root)
    orch_payload = evaluate_product_orchestration(repo_root, product.id)
    raw_triggers = orch_payload.get("escalation_triggers")
    orch_list = [x for x in raw_triggers if isinstance(x, dict)] if isinstance(raw_triggers, list) else []
    orch_matches = matches_from_orchestration_triggers(orch_list)
    matches = merge_trigger_matches(core_matches, orch_matches)

    pkt: EscalationPacket | None = None
    if matches:
        pkt = build_packet(
            product=product,
            findings=findings,
            assessment=assessment,
            candidates=candidates,
            matches=matches,
            repo_root=repo_root,
            orchestration_snapshot=orch_payload,
        )

    write_escalation_task_state(
        repo_root,
        product.id,
        orch_payload,
        matches,
        packet_written=pkt is not None,
    )
    if pkt is None:
        return None, []
    return pkt, matches
