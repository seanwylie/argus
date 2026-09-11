"""Advisor runs: structured prompts + optional OpenAI-compatible LLM or deterministic stub."""

from __future__ import annotations

import hashlib
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from argus.advisors.context import ConsultationContext, gather_consultation_context
from argus.advisors.llm import (
    OpenAICompatProvider,
    extract_json_object,
    llm_config_from_env,
    parse_advisor_json,
)
from argus.advisors.models import Advisor, AdvisorResponse, AdvisorRunResult
from argus.advisors.prompts import build_chat_messages
from argus.advisors.registry import resolve_advisors
from argus.advisors.temporal import TemporalGrounding, legacy_string_context_grounding


def _stance(product_id: str, advisor_id: str) -> float:
    """Stable [0, 1] stance for consensus weighting."""
    raw = hashlib.sha256(f"{product_id}:{advisor_id}:argus.advisors.v1".encode()).hexdigest()
    return int(raw[:8], 16) / 0xFFFFFFFF


def _pick_line(stance: float, lines: tuple[str, ...]) -> str:
    if not lines:
        return ""
    idx = int(stance * len(lines)) % len(lines)
    return lines[idx]


def _compact_context(ctx: ConsultationContext) -> str:
    from argus.core.serialize import dumps_json

    tg_blob = ""
    if ctx.temporal is not None:
        tg_blob = dumps_json(ctx.temporal.evidence_summary_dict(), indent=None)
    parts = [
        dumps_json(ctx.product_summary, indent=None),
        tg_blob,
        ctx.findings_section,
        ctx.trends_section,
        ctx.decisions_section,
        ctx.experiments_section,
    ]
    s = "\n\n---\n\n".join(parts)
    return s[:14000]


def _freshness_label(risk: float) -> str:
    if risk > 0.55:
        return "high"
    if risk > 0.22:
        return "moderate"
    return "low"


def simulate_response(
    advisor: Advisor,
    product_id: str,
    context: str | ConsultationContext,
) -> AdvisorResponse:
    # ARGUS-STUB:intentional — default advisor path without LLM (docs/stub-inventory.md)
    """
    Produce a deterministic placeholder response (no external API).

    Shape matches LLM output: recommendation, rationale, risks, confidence, stance, temporal fields.
    """
    if isinstance(context, ConsultationContext):
        tg: TemporalGrounding | None = context.temporal
        compact = _compact_context(context)
    else:
        tg = legacy_string_context_grounding(
            datetime.now(timezone.utc).isoformat(),
        )
        compact = str(context)

    st = _stance(product_id, advisor.id)
    arch = advisor.archetype.value

    rec_finance = (
        "Prioritize burn visibility and cap discretionary spend this quarter.",
        "Reinvest if unit economics trend positive vs prior window.",
        "Freeze new scope until cost baseline is re-baselined.",
    )
    rec_investor = (
        "Seek a crisp milestone before additional portfolio surface area.",
        "Double down if traction metric inflected; else hold for signal.",
        "Treat as option value: small experiments, bounded downside.",
    )
    rec_technical = (
        "Reduce operational risk before scaling traffic or data volume.",
        "Address reliability gaps that block confident iteration.",
        "Invest in observability to shrink unknown-unknowns.",
    )
    rec_marketing = (
        "Tighten ICP narrative and measure one funnel metric weekly.",
        "Test one positioning variant against control messaging.",
        "Pause broad spend until conversion baseline stabilizes.",
    )
    rec_product = (
        "Ship the smallest slice that validates the next user outcome.",
        "Defer nice-to-haves; align roadmap to lifecycle stage.",
        "Validate retention before acquisition expansion.",
    )
    rec_creative = (
        "Differentiate on one memorable story, not feature density.",
        "Reduce cognitive load in primary user path.",
        "Balance brand risk with shipping cadence.",
    )

    mapping: dict[str, tuple[str, ...]] = {
        "finance": rec_finance,
        "investor": rec_investor,
        "technical": rec_technical,
        "marketing": rec_marketing,
        "product": rec_product,
        "creative": rec_creative,
    }
    recommendation = _pick_line(st, mapping.get(arch, ("Review tradeoffs against latest signals.",)))

    rationale = (
        f"Deterministic stub for {advisor.id} ({arch}): stance={st:.4f}. "
        f"Context digest len={len(compact)} chars."
    )

    risk_low = "Residual uncertainty is moderate; monitor weekly metrics."
    risk_mid = "Material disagreement possible across functions; align on one KPI."
    risk_high = "Elevated divergence risk between growth and stability levers."
    risk_bucket = (risk_low, risk_mid, risk_high)
    risk_assessment = _pick_line(st, risk_bucket)

    frisk = tg.overall_freshness_risk if tg is not None else 0.4
    conf_adj = -0.45 * frisk
    base_conf = 0.55 + conf_adj
    base_conf = max(0.12, min(0.92, base_conf))
    flabel = _freshness_label(frisk)

    return AdvisorResponse(
        advisor_id=advisor.id,
        archetype=advisor.archetype,
        recommendation=recommendation,
        rationale=rationale,
        risk_assessment=risk_assessment,
        risks=[risk_assessment],
        confidence=base_conf,
        temporal_assumptions=[
            "Deterministic stub: only repository artifacts and their timestamps define currency.",
            "Do not treat this output as live operational truth.",
        ],
        freshness_risk=flabel,
        confidence_adjustment=round(conf_adj, 4),
        metadata={
            "stance": round(st, 6),
            "simulated": True,
            "model": "argus.advisors.stub.v1",
            "temporal_evidence_used": tg.evidence_summary_dict() if tg is not None else {},
            "temporal_freshness_penalty": round(frisk, 4),
            "temporal_summary_line": tg.one_line_summary() if tg is not None else "ambiguous_context",
        },
    )


def _response_from_llm(
    advisor: Advisor,
    product_id: str,
    norm: dict[str, Any],
    *,
    model_name: str,
) -> AdvisorResponse:
    st_raw = norm.get("stance")
    try:
        st_f = float(st_raw) if st_raw is not None else _stance(product_id, advisor.id)
    except (TypeError, ValueError):
        st_f = _stance(product_id, advisor.id)
    st_f = max(0.0, min(1.0, st_f))
    conf_f = norm.get("confidence")
    try:
        conf_v = float(conf_f) if conf_f is not None else 0.65
    except (TypeError, ValueError):
        conf_v = 0.65
    conf_v = max(0.0, min(1.0, conf_v))
    risks_list = list(norm.get("risks") or [])
    if not risks_list:
        risks_list = ["(no explicit risks listed)"]
    risk_one = risks_list[0]
    ta = [str(x) for x in (norm.get("temporal_assumptions") or []) if str(x).strip()]
    fr_s = norm.get("freshness_risk")
    fr_s = str(fr_s).strip().lower() if fr_s is not None else None
    if fr_s not in ("low", "moderate", "high"):
        fr_s = None
    cadj_f = norm.get("confidence_adjustment")
    if cadj_f is not None:
        try:
            cadj_f = max(-1.0, min(0.0, float(cadj_f)))
        except (TypeError, ValueError):
            cadj_f = None
    return AdvisorResponse(
        advisor_id=advisor.id,
        archetype=advisor.archetype,
        recommendation=str(norm.get("recommendation") or "(empty)"),
        rationale=str(norm.get("rationale") or ""),
        risk_assessment=risk_one,
        risks=risks_list,
        confidence=conf_v,
        temporal_assumptions=ta,
        freshness_risk=fr_s,
        confidence_adjustment=cadj_f,
        metadata={
            "stance": round(st_f, 6),
            "simulated": False,
            "model": model_name,
        },
    )


def run_advisors(
    repo_root: Path,
    product_id: str,
    *,
    advisors: list[Advisor] | None = None,
    context_override: str | None = None,
    use_llm: bool | None = None,
    log_consultation: bool = True,
) -> AdvisorRunResult:
    """
    Run all advisors with structured context.

    ``use_llm``: ``None`` = use LLM when ``ARGUS_OPENAI_API_KEY`` is set; ``False`` = stub only;
    ``True`` = attempt LLM and fall back to stub per advisor on failure.
    """
    advisors = advisors if advisors is not None else resolve_advisors(repo_root, product_id)
    now = datetime.now(timezone.utc).isoformat()

    if context_override is not None:
        leg = legacy_string_context_grounding(now)
        stub_ctx = ConsultationContext(
            product_id=product_id,
            product_summary={"context_override": True},
            findings_section=context_override[:12000],
            temporal=leg,
        )
        compact = _compact_context(stub_ctx)
        responses = [simulate_response(a, product_id, stub_ctx) for a in advisors]
        return AdvisorRunResult(
            product_id=product_id,
            repo_root=str(repo_root.resolve()),
            generated_at_utc=now,
            context_summary=compact[:2000],
            responses=responses,
            consultation_log_dir=None,
            temporal_grounding=leg,
        )

    ctx_obj = gather_consultation_context(repo_root, product_id)
    compact = _compact_context(ctx_obj)

    cfg = llm_config_from_env()
    if use_llm is False:
        provider = None
    elif cfg is None:
        provider = None
    else:
        provider = OpenAICompatProvider(cfg)

    responses: list[AdvisorResponse] = []
    log_rows: list[dict[str, Any]] = []
    model_name = cfg.model if cfg else "stub"

    for advisor in advisors:
        if provider is None:
            resp = simulate_response(advisor, product_id, ctx_obj)
            log_rows.append(
                {
                    "advisor_id": advisor.id,
                    "mode": "stub",
                    "prompt_messages": None,
                    "raw_response": None,
                    "error": None,
                }
            )
            responses.append(resp)
            continue

        messages = build_chat_messages(advisor, ctx_obj)
        try:
            raw = provider.complete_json_chat(messages)
            data = extract_json_object(raw)
            norm = parse_advisor_json(data)
            resp = _response_from_llm(advisor, product_id, norm, model_name=model_name)
            log_rows.append(
                {
                    "advisor_id": advisor.id,
                    "mode": "llm",
                    "prompt_messages": messages,
                    "raw_response": raw[:50000],
                    "parsed": norm,
                    "error": None,
                }
            )
        except Exception as e:
            resp = simulate_response(advisor, product_id, ctx_obj)
            resp.metadata["llm_error"] = str(e)[:2000]
            resp.metadata["llm_traceback"] = traceback.format_exc()[:4000]
            log_rows.append(
                {
                    "advisor_id": advisor.id,
                    "mode": "stub_after_error",
                    "prompt_messages": messages,
                    "raw_response": None,
                    "error": str(e),
                }
            )
        responses.append(resp)

    log_path: Path | None = None
    if log_consultation:
        from argus.advisors.consult_log import write_consultation_artifacts
        from argus.core.serialize import to_jsonable

        run_payload = to_jsonable(
            AdvisorRunResult(
                product_id=product_id,
                repo_root=str(repo_root.resolve()),
                generated_at_utc=now,
                context_summary=compact[:4000],
                responses=responses,
                temporal_grounding=ctx_obj.temporal,
            )
        )
        tg_dump: dict[str, Any] = {}
        if ctx_obj.temporal is not None:
            tg_dump = ctx_obj.temporal.evidence_summary_dict()
        log_path = write_consultation_artifacts(
            repo_root,
            product_id,
            context_dump={
                "product_id": product_id,
                "product_summary": ctx_obj.product_summary,
                "findings_excerpt": ctx_obj.findings_section[:8000],
                "trends_excerpt": str(ctx_obj.trends_section)[:4000],
                "decisions_excerpt": ctx_obj.decisions_section[:4000],
                "experiments_excerpt": ctx_obj.experiments_section[:4000],
                "temporal_grounding": tg_dump,
            },
            per_advisor=log_rows,
            run_payload=run_payload,
        )

    return AdvisorRunResult(
        product_id=product_id,
        repo_root=str(repo_root.resolve()),
        generated_at_utc=now,
        context_summary=compact[:2000],
        responses=responses,
        consultation_log_dir=str(log_path) if log_path else None,
        temporal_grounding=ctx_obj.temporal,
    )


def build_context_summary(repo_root: Path, product_id: str) -> str:
    """Compact context string for prompts (local artifacts only)."""
    try:
        return _compact_context(gather_consultation_context(repo_root, product_id))
    except ValueError:
        parts: list[str] = [f"product_id={product_id}"]
        try:
            from argus.findings.persistence import load_latest_findings

            fb = load_latest_findings(repo_root, product_id)
            if fb:
                parts.append(f"findings_count={len(fb.findings)}")
        except (OSError, ValueError, TypeError):
            parts.append("findings=unavailable")
        return "; ".join(parts)
