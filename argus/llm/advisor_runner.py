"""Multi-perspective advisor council using optional LLM (advisory only; never executes actions)."""

from __future__ import annotations

import json
import logging
import statistics
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from argus.advisors.context import gather_consultation_context
from argus.advisors.llm import extract_json_object
from argus.core.serialize import dumps_json
from argus.llm.client import (
    LLMCompletionStatus,
    complete,
    is_llm_enabled,
    llm_client_from_env,
)

logger = logging.getLogger(__name__)

_ADVISOR_KEYS = ("finance", "growth", "product", "technical", "creative")
_PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"


@dataclass
class AdvisorPerspectiveOutput:
    """One LLM advisor lane (interpretation — not ground truth)."""

    advisor_id: str
    critique: str
    risks: list[str] = field(default_factory=list)
    suggested_improvements: list[str] = field(default_factory=list)
    score: float | None = None
    """Optional 0–1; None when unavailable."""
    raw_status: str = ""


@dataclass
class AdvisorCouncilResult:
    """Combined council output for inspection and decision-context hints."""

    product_id: str
    per_advisor: dict[str, AdvisorPerspectiveOutput] = field(default_factory=dict)
    aggregated_sentiment: str = "unknown"
    """Short label, e.g. aligned | mixed | conflict."""
    agreement_signal: float = 0.0
    """0–1 higher when scores align (derived; advisory)."""
    advisory: bool = True
    status: str = LLMCompletionStatus.DISABLED.value


def _compact_context_block(repo_root: Path, product_id: str) -> str:
    ctx = gather_consultation_context(repo_root, product_id)
    blob = {
        "product": ctx.product_summary,
        "findings_excerpt": (ctx.findings_section or "")[:6000],
        "decisions_excerpt": (ctx.decisions_section or "")[:4000],
        "experiments_excerpt": (ctx.experiments_section or "")[:3000],
    }
    if ctx.temporal is not None:
        blob["temporal"] = ctx.temporal.evidence_summary_dict()
    return dumps_json(blob, indent=None)


def _load_advisor_template(advisor_id: str) -> str:
    p = _PROMPTS_DIR / f"advisor_{advisor_id}.txt"
    return p.read_text(encoding="utf-8")


def _parse_advisor_json(text: str, advisor_id: str) -> AdvisorPerspectiveOutput:
    data = extract_json_object(text)
    crit = str(data.get("critique", "")).strip()
    risks_raw = data.get("risks")
    risks: list[str] = []
    if isinstance(risks_raw, list):
        risks = [str(x).strip() for x in risks_raw if str(x).strip()]
    sug_raw = data.get("suggested_improvements")
    sug: list[str] = []
    if isinstance(sug_raw, list):
        sug = [str(x).strip() for x in sug_raw if str(x).strip()]
    sc = data.get("score")
    score: float | None = None
    try:
        if sc is not None:
            score = max(0.0, min(1.0, float(sc)))
    except (TypeError, ValueError):
        score = None
    return AdvisorPerspectiveOutput(
        advisor_id=advisor_id,
        critique=crit or "(empty)",
        risks=risks[:12],
        suggested_improvements=sug[:12],
        score=score,
        raw_status=LLMCompletionStatus.OK.value,
    )


def _run_one_advisor(
    repo_root: Path,
    product_id: str,
    advisor_id: str,
    context_block: str,
    focus: str | None,
) -> AdvisorPerspectiveOutput:
    template = _load_advisor_template(advisor_id)
    focus_block = f"\nFocus / question:\n{focus}\n" if focus else ""
    prompt = template.replace("{{CONTEXT_BLOCK}}", context_block + focus_block)
    result = complete(prompt)
    if result.status != LLMCompletionStatus.OK or not result.text:
        return AdvisorPerspectiveOutput(
            advisor_id=advisor_id,
            critique="(llm unavailable)",
            raw_status=result.status.value,
        )
    try:
        return _parse_advisor_json(result.text, advisor_id)
    except (ValueError, RuntimeError) as e:
        logger.warning("Advisor %s JSON parse failed: %s", advisor_id, e)
        return AdvisorPerspectiveOutput(
            advisor_id=advisor_id,
            critique="(parse error)",
            raw_status="parse_error",
        )


def _aggregate(per: dict[str, AdvisorPerspectiveOutput]) -> tuple[str, float]:
    scores = [o.score for o in per.values() if o.score is not None]
    if len(scores) < 2:
        return ("mixed", 0.45)
    spread = statistics.pstdev(scores) if len(scores) > 1 else 0.0
    agreement = max(0.0, min(1.0, 1.0 - min(1.0, spread * 3.0)))
    mean_s = statistics.mean(scores)
    if spread <= 0.12 and mean_s >= 0.55:
        sentiment = "aligned"
    elif spread >= 0.22 or mean_s < 0.35:
        sentiment = "conflict"
    else:
        sentiment = "mixed"
    return (sentiment, agreement)


def run_advisor_council(
    repo_root: Path,
    product_id: str,
    *,
    focus: str | None = None,
) -> AdvisorCouncilResult:
    """
    Run five advisory perspectives. Does not change autonomy, approvals, or execution.

    When LLM is disabled or misconfigured, returns a stub result with ``status`` set accordingly.
    """
    root = repo_root.resolve()
    if not is_llm_enabled() or llm_client_from_env() is None:
        return AdvisorCouncilResult(
            product_id=product_id,
            status=LLMCompletionStatus.DISABLED.value
            if not is_llm_enabled()
            else LLMCompletionStatus.NO_API_KEY.value,
        )

    context_block = _compact_context_block(root, product_id)
    per: dict[str, AdvisorPerspectiveOutput] = {}
    for aid in _ADVISOR_KEYS:
        per[aid] = _run_one_advisor(root, product_id, aid, context_block, focus)

    sentiment, agreement = _aggregate(per)
    return AdvisorCouncilResult(
        product_id=product_id,
        per_advisor=per,
        aggregated_sentiment=sentiment,
        agreement_signal=round(agreement, 4),
        advisory=True,
        status=LLMCompletionStatus.OK.value,
    )


def council_result_to_jsonable(res: AdvisorCouncilResult) -> dict[str, Any]:
    out: dict[str, Any] = {
        "schema": "argus.llm_advisor_council.v1",
        "product_id": res.product_id,
        "advisory": res.advisory,
        "status": res.status,
        "aggregated_sentiment": res.aggregated_sentiment,
        "agreement_signal": res.agreement_signal,
        "per_advisor": {},
    }
    for aid, o in res.per_advisor.items():
        out["per_advisor"][aid] = {
            "critique": o.critique,
            "risks": o.risks,
            "suggested_improvements": o.suggested_improvements,
            "score": o.score,
            "raw_status": o.raw_status,
        }
    return out


def persist_council_result(repo_root: Path, res: AdvisorCouncilResult) -> Path:
    """Write under ``runs/advisors/`` for decision assessment to consume (no secrets)."""
    root = repo_root.resolve()
    out_dir = root / "runs" / "advisors"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"llm_council_{res.product_id}.latest.json"
    path.write_text(dumps_json(council_result_to_jsonable(res)) + "\n", encoding="utf-8")
    return path


def load_council_metrics(
    repo_root: Path,
    product_id: str,
) -> tuple[float | None, bool, str]:
    """
    Alignment score (0–1), conflict flag, short summary for :class:`DecisionContextAssessment`.

    Reads persisted council file when present; does not call the network.
    """
    p = repo_root.resolve() / "runs" / "advisors" / f"llm_council_{product_id}.latest.json"
    if not p.is_file():
        return None, False, ""
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None, False, ""
    if not isinstance(raw, dict):
        return None, False, ""
    agreement = raw.get("agreement_signal")
    try:
        align = float(agreement) if agreement is not None else None
    except (TypeError, ValueError):
        align = None
    if align is not None:
        align = max(0.0, min(1.0, align))
    sentiment = str(raw.get("aggregated_sentiment", "")).lower()
    conflict = sentiment == "conflict"
    summary = f"llm_council sentiment={sentiment} agreement={raw.get('agreement_signal')}"
    return align, conflict, summary
