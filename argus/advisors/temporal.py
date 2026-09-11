"""Temporal grounding for advisor prompts: artifact ages, freshness, recent signals."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from argus.core.serialize import dumps_json, to_jsonable
from argus.decision.persistence import load_latest_product_decisions
from argus.experiments.store import list_experiments
from argus.findings.persistence import load_latest_findings
from argus.products.inventory import build_inventory
from argus.signals.persistence import load_latest_bundle
from argus.temporal.persistence import load_latest_temporal_bundle
from argus.temporal.recency import (
    worst_freshness_bucket_from_signal_dicts,
    worst_freshness_status_from_signal_dicts,
)


def _parse_iso_utc(s: str | None) -> datetime | None:
    if not s or not str(s).strip():
        return None
    t = str(s).strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(t)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError:
        return None


def _age_days(now: datetime, ts: datetime | None) -> float | None:
    if ts is None:
        return None
    return max(0.0, (now - ts).total_seconds() / 86400.0)


@dataclass(frozen=True)
class SourceFreshness:
    """One evidence source with explicit freshness metadata."""

    key: str
    label: str
    observed_at_utc: str | None
    age_days: float | None
    stale_after_days: float
    status: str  # "current" | "stale" | "missing"
    detail: str


@dataclass
class TemporalGrounding:
    """Explicit temporal context for advisor consultation (not a claim of live truth)."""

    consultation_as_of_utc: str
    sources: list[SourceFreshness] = field(default_factory=list)
    recent_signals_excerpt: str = ""
    overall_freshness_risk: float = 0.0
    """0 = all sources current; 1 = worst case (all missing or very stale)."""
    missing_sources: list[str] = field(default_factory=list)
    current_vs_stale_note: str = ""
    collection_recency: dict[str, Any] | None = None
    """Optional summary from ``runs/temporal/latest/<product>.json`` (collection-level recency, not LLM output)."""

    def evidence_summary_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "consultation_as_of_utc": self.consultation_as_of_utc,
            "sources": [to_jsonable(s) for s in self.sources],
            "overall_freshness_risk": round(self.overall_freshness_risk, 4),
            "missing_sources": list(self.missing_sources),
            "recent_signals_excerpt": self.recent_signals_excerpt[:4000],
        }
        if self.collection_recency:
            out["collection_recency"] = dict(self.collection_recency)
        return out

    def one_line_summary(self) -> str:
        parts = [f"freshness_risk={self.overall_freshness_risk:.2f}"]
        if self.collection_recency:
            wb = self.collection_recency.get("worst_freshness_bucket")
            if wb:
                parts.append(f"collection_worst_bucket={wb}")
            ws = self.collection_recency.get("worst_freshness_status")
            if ws:
                parts.append(f"collection_worst_freshness_status={ws}")
        if self.missing_sources:
            parts.append(f"missing={','.join(self.missing_sources[:6])}")
        return "; ".join(parts)


# Default staleness windows (days). Signals age fastest; trends slower.
_STALE_SIGNALS_DAYS = 3.0
_STALE_FINDINGS_DAYS = 7.0
_STALE_DECISIONS_DAYS = 7.0
_STALE_TRENDS_DAYS = 14.0
_STALE_EXPERIMENTS_DAYS = 30.0


def _classify(age: float | None, *, threshold: float, has_data: bool) -> tuple[str, str]:
    if not has_data:
        return "missing", "No artifact for this source."
    if age is None:
        return "stale", "Timestamp missing or unparseable; treat as unknown age."
    if age <= threshold:
        return "current", f"Age {age:.1f}d is within {threshold:.0f}d window."
    return "stale", f"Age {age:.1f}d exceeds {threshold:.0f}d — interpret as potentially outdated."


def _risk_for_source(status: str, age: float | None, threshold: float) -> float:
    if status == "missing":
        return 1.0
    if status == "current":
        return 0.0
    # stale
    if age is None:
        return 0.75
    excess = max(0.0, age - threshold)
    return min(1.0, 0.35 + 0.65 * min(1.0, excess / max(threshold, 1.0)))


def build_temporal_grounding(repo_root: Path, product_id: str) -> TemporalGrounding:
    """Load timestamps and recent signals; compute freshness risk for prompts and consensus."""
    root = repo_root.resolve()
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()

    inv = build_inventory(root)
    if product_id not in inv.valid:
        raise ValueError(f"Unknown or invalid product: {product_id!r}")

    sources: list[SourceFreshness] = []
    missing: list[str] = []

    # --- signals ---
    sig_bundle = load_latest_bundle(root, product_id)
    sig_ts = _parse_iso_utc(sig_bundle.collected_at_utc if sig_bundle else None)
    sig_age = _age_days(now, sig_ts)
    sig_has = sig_bundle is not None and bool(sig_bundle.records)
    st_sig, det_sig = _classify(sig_age, threshold=_STALE_SIGNALS_DAYS, has_data=sig_has)
    if not sig_has:
        missing.append("signals")
    sources.append(
        SourceFreshness(
            key="signals",
            label="runs/signals/latest/{product}.json",
            observed_at_utc=sig_bundle.collected_at_utc if sig_bundle else None,
            age_days=sig_age,
            stale_after_days=_STALE_SIGNALS_DAYS,
            status=st_sig,
            detail=det_sig,
        )
    )

    recent_excerpt = ""
    if sig_bundle and sig_bundle.records:
        recs = sorted(sig_bundle.records, key=lambda r: r.observed_at, reverse=True)[:12]
        lines = []
        for r in recs:
            oa = r.observed_at.isoformat() if r.observed_at.tzinfo else r.observed_at.replace(tzinfo=timezone.utc).isoformat()
            try:
                payload_s = dumps_json(r.payload, indent=None)[:240]
            except TypeError:
                payload_s = str(r.payload)[:240]
            lines.append(
                f"- {oa} | {r.signal_type.value} | {r.source} | {payload_s}"
            )
        recent_excerpt = "\n".join(lines)
    else:
        recent_excerpt = "(No signal records — run `argus signals collect`.)"

    # --- findings ---
    fb = load_latest_findings(root, product_id)
    f_ts = _parse_iso_utc(fb.generated_at_utc if fb else None)
    f_age = _age_days(now, f_ts)
    f_has = fb is not None
    st_f, det_f = _classify(f_age, threshold=_STALE_FINDINGS_DAYS, has_data=f_has)
    if not f_has:
        missing.append("findings")
    sources.append(
        SourceFreshness(
            key="findings",
            label="runs/findings/latest/{product}.json",
            observed_at_utc=fb.generated_at_utc if fb else None,
            age_days=f_age,
            stale_after_days=_STALE_FINDINGS_DAYS,
            status=st_f,
            detail=det_f,
        )
    )

    # --- decisions ---
    raw = load_latest_product_decisions(root, product_id)
    d_ts_str = str(raw.get("generated_at_utc", "")) if isinstance(raw, dict) else ""
    d_ts = _parse_iso_utc(d_ts_str or None)
    d_age = _age_days(now, d_ts)
    d_has = raw is not None
    st_d, det_d = _classify(d_age, threshold=_STALE_DECISIONS_DAYS, has_data=d_has)
    if not d_has:
        missing.append("decisions")
    sources.append(
        SourceFreshness(
            key="decisions",
            label="runs/decisions/latest/{product}.json",
            observed_at_utc=d_ts_str or None,
            age_days=d_age,
            stale_after_days=_STALE_DECISIONS_DAYS,
            status=st_d,
            detail=det_d,
        )
    )

    # --- trends (file-level + per-product) ---
    trends_path = root / "runs" / "trends" / "latest.json"
    tr_has = trends_path.is_file()
    tr_file_ts: datetime | None = None
    if tr_has:
        try:
            data = json.loads(trends_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                tr_file_ts = _parse_iso_utc(str(data.get("generated_at_utc", "")))
        except (OSError, json.JSONDecodeError):
            tr_has = False
    tr_age = _age_days(now, tr_file_ts) if tr_file_ts else None
    st_tr, det_tr = _classify(tr_age, threshold=_STALE_TRENDS_DAYS, has_data=tr_has)
    if not tr_has:
        missing.append("trends")
    sources.append(
        SourceFreshness(
            key="trends",
            label="runs/trends/latest.json",
            observed_at_utc=tr_file_ts.isoformat() if tr_file_ts else None,
            age_days=tr_age,
            stale_after_days=_STALE_TRENDS_DAYS,
            status=st_tr,
            detail=det_tr,
        )
    )

    # --- experiments (max of created / last eval) ---
    exps = list_experiments(root, product_id=product_id)
    exp_ts: datetime | None = None
    for e in exps:
        for cand in (e.last_evaluation_at, e.created_at):
            t = _parse_iso_utc(cand)
            if t and (exp_ts is None or t > exp_ts):
                exp_ts = t
    ex_age = _age_days(now, exp_ts)
    ex_has = bool(exps)
    st_ex, det_ex = _classify(ex_age, threshold=_STALE_EXPERIMENTS_DAYS, has_data=ex_has)
    if not ex_has:
        missing.append("experiments")
    sources.append(
        SourceFreshness(
            key="experiments",
            label="runs/experiments/ (latest activity)",
            observed_at_utc=exp_ts.isoformat() if exp_ts else None,
            age_days=ex_age,
            stale_after_days=_STALE_EXPERIMENTS_DAYS,
            status=st_ex,
            detail=det_ex if ex_has else "No experiments on file.",
        )
    )

    risks: list[float] = []
    for s in sources:
        th = s.stale_after_days
        risks.append(_risk_for_source(s.status, s.age_days, th))
    overall = sum(risks) / max(1, len(risks))

    collection_recency: dict[str, Any] | None = None
    tb = load_latest_temporal_bundle(root, product_id)
    if isinstance(tb, dict):
        raw_sigs = tb.get("signals")
        if isinstance(raw_sigs, list):
            dict_rows = [x for x in raw_sigs if isinstance(x, dict)]
            scores: list[float] = []
            for row in dict_rows:
                fs = row.get("freshness_score")
                if isinstance(fs, (int, float)):
                    scores.append(float(fs))
            collection_recency = {
                "schema": str(tb.get("schema") or "argus.temporal_bundle.v1"),
                "collected_at_utc": tb.get("collected_at_utc"),
                "worst_freshness_bucket": worst_freshness_bucket_from_signal_dicts(dict_rows),
                "worst_freshness_status": worst_freshness_status_from_signal_dicts(dict_rows),
                "mean_freshness_score": (sum(scores) / len(scores)) if scores else None,
                "signal_row_count": len(dict_rows),
            }

    note_parts = [
        f"Consultation time (UTC): {now_iso}.",
        "CURRENT data: sources marked `current` reflect artifacts within their staleness windows.",
        "STALE or MISSING: do not invent live metrics; recommend verifying or refreshing artifacts.",
    ]
    if missing:
        note_parts.append(f"Missing sources (no usable artifact): {', '.join(missing)}.")

    return TemporalGrounding(
        consultation_as_of_utc=now_iso,
        sources=sources,
        recent_signals_excerpt=recent_excerpt,
        overall_freshness_risk=min(1.0, overall),
        missing_sources=sorted(set(missing)),
        current_vs_stale_note="\n".join(note_parts),
        collection_recency=collection_recency,
    )


def legacy_string_context_grounding(consultation_as_of_utc: str) -> TemporalGrounding:
    """When only a free-form context string is provided (no structured repo load)."""
    return TemporalGrounding(
        consultation_as_of_utc=consultation_as_of_utc,
        sources=[],
        recent_signals_excerpt="(Structured temporal grounding unavailable — context string override.)",
        overall_freshness_risk=0.4,
        missing_sources=["signals", "findings", "decisions", "trends", "experiments"],
        current_vs_stale_note=(
            "No structured artifact timestamps were loaded; treat evidence as ambiguous. "
            "Do not assume current operational state."
        ),
        collection_recency=None,
    )
