"""Built-in finding rules (signal → candidate mappings)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from argus.core.models.enums import FindingKind, LifecycleStage, SignalType
from argus.core.models.signal import SignalRecord
from argus.findings.candidates import FindingCandidate
from argus.findings.context import RuleContext
from argus.findings.rules.base import FindingRule
from argus.findings.rules.temporal import TemporalSignalsRule

_SECONDS_30D = 30 * 24 * 3600
_SECONDS_14D = 14 * 24 * 3600


def _manifest_declaration_count(signals: list[SignalRecord]) -> int:
    n = 0
    for s in signals:
        tags = {str(t).lower() for t in (s.tags or [])}
        if "manifest_declaration" in tags or str(s.source or "").strip().lower() == "manifest_declaration":
            n += 1
    return n


def _low_signal_wordings(
    *,
    n: int,
    nm: int,
    mf: int,
    product_type: str,
    metrics_n: int,
    empty_metrics_rows: int,
) -> tuple[str, str, str]:
    """Title, summary, recommendation for insufficient_signals — grounded, not product-quality judgment."""
    pt = f"Product type `{product_type}`: " if product_type else ""
    if mf > 0:
        title = "Sparse direct signal observations (manifest placeholders present)"
        summary = (
            f"{pt}{nm} non-manifest signal record(s) of {n} total ({mf} manifest_declaration "
            "reconciliation row(s)). This reflects pipeline coverage and manifest reconciliation, "
            "not a judgment of product quality."
        )
        recommendation = (
            "Align manifest paths with real adapters, trim unused manifest entries, or add local "
            "snapshot files under enabled signal types."
        )
    else:
        title = "Low pipeline signal coverage for assessment"
        summary = (
            f"{pt}Only {n} signal record(s) collected; metrics-type coverage may be thin "
            f"(metrics signal rows: {metrics_n}, metrics rows indicating empty/paths-only: "
            f"{empty_metrics_rows})."
        )
        recommendation = (
            "Enable more signal types in product.yaml or add local snapshot files where applicable."
        )
    return title, summary, recommendation


def _signals_of(ctx: RuleContext, st: SignalType) -> list[SignalRecord]:
    return [s for s in ctx.signals if s.signal_type == st]


def _metrics_json_payloads(ctx: RuleContext) -> list[tuple[SignalRecord, dict]]:
    """``metrics_file`` adapter JSON rows: (signal, parsed object data)."""
    out: list[tuple[SignalRecord, dict]] = []
    for s in _signals_of(ctx, SignalType.METRICS):
        if s.payload.get("format") != "json":
            continue
        data = s.payload.get("data")
        if isinstance(data, dict) and data:
            out.append((s, data))
    return out


def _is_bootstrap_metrics_data(data: dict) -> bool:
    """
    True when a metrics JSON blob is local seed / inventory / bootstrap, not operational KPIs.

    Products may ship honest ``metrics/*.json`` for Argus observability; those must not
    alone justify launch candidacy. Explicit opt-out: ``argus_metrics_bootstrap: true``.
    """
    if data.get("argus_metrics_bootstrap") is True:
        return True
    kind = data.get("kind")
    if kind in ("bootstrap_seed", "local_seed", "catalog_seed"):
        return True
    schema = str(data.get("schema") or "").lower()
    if "local_seed" in schema:
        return True
    if "catalog_snapshot" in schema:
        return True
    disc = data.get("disclaimer")
    if isinstance(disc, str) and "seed" in disc.lower():
        return True
    return False


def _validation_artifact_manual_complete(data: dict) -> bool:
    """Explicit human or checklist completion recorded in metrics JSON (substitutes for traffic)."""
    if data.get("argus_validation_evidence") is not True:
        return False
    st = str(data.get("validation_status") or "").lower()
    return st in ("complete", "passed", "signed_off", "done")


def _has_validation_artifact_file(ctx: RuleContext) -> bool:
    """Product-local validation artifact under metrics/ (filename or schema flag)."""
    for s in _signals_of(ctx, SignalType.METRICS):
        if s.payload.get("format") != "json":
            continue
        fn = str(s.payload.get("file") or "").lower()
        if "validation_evidence" in fn:
            return True
        data = s.payload.get("data")
        if isinstance(data, dict):
            sch = str(data.get("schema") or "").lower()
            if "validation_evidence" in sch:
                return True
    return False


def _has_numeric_analytics(ctx: RuleContext) -> bool:
    for s in _signals_of(ctx, SignalType.ANALYTICS):
        p = s.payload
        if p.get("check") == "analytics_snapshot":
            continue
        v = p.get("views")
        if v is not None:
            try:
                if float(v) > 0:
                    return True
            except (TypeError, ValueError):
                pass
        sn = p.get("snippet") if isinstance(p.get("snippet"), dict) else {}
        if isinstance(sn, dict):
            for key in ("views", "pageviews", "sessions"):
                if key in sn and sn[key] is not None:
                    try:
                        if float(sn[key]) > 0:
                            return True
                    except (TypeError, ValueError):
                        pass
    return False


def _has_execution_outcome(ctx: RuleContext) -> bool:
    for s in _signals_of(ctx, SignalType.EXECUTION):
        p = s.payload if isinstance(s.payload, dict) else {}
        if p.get("provenance") == "execution_outcome":
            return True
    return False


def _metrics_jsonl_samples(ctx: RuleContext) -> list[dict]:
    out: list[dict] = []
    for s in _signals_of(ctx, SignalType.METRICS):
        if s.payload.get("format") != "jsonl":
            continue
        for x in s.payload.get("samples") or []:
            if isinstance(x, dict):
                out.append(x)
    return out


def _has_non_seed_metrics_jsonl_exercise(ctx: RuleContext) -> bool:
    """JSONL line that is not bootstrap inventory and not a seed-generation marker only."""
    for d in _metrics_jsonl_samples(ctx):
        if _is_bootstrap_metrics_data(d):
            continue
        ev = str(d.get("event") or "")
        if ev == "catalog_seed_generated":
            continue
        if d.get("argus_metrics_bootstrap") is True:
            continue
        return True
    return False


def _has_content_evidence_metric(ctx: RuleContext) -> bool:
    """argus.content_evidence.v1 — published or multi-node depth (not one placeholder alone)."""
    for _, d in _metrics_json_payloads(ctx):
        if str(d.get("schema") or "") != "argus.content_evidence.v1":
            continue
        if int(d.get("published_slot_count") or d.get("published_nodes") or 0) >= 1:
            return True
        if int(d.get("node_count") or d.get("slots_present") or 0) >= 2:
            return True
    return False


def _collect_secondary_validation_axes(ctx: RuleContext) -> list[str]:
    axes: list[str] = []
    if _has_validation_artifact_file(ctx):
        axes.append("validation_artifact_file")
    if _has_numeric_analytics(ctx):
        axes.append("analytics_numeric")
    if _has_execution_outcome(ctx):
        axes.append("execution_outcome")
    if _has_non_seed_metrics_jsonl_exercise(ctx):
        axes.append("metrics_jsonl_exercise")
    if _has_content_evidence_metric(ctx):
        axes.append("content_evidence")
    return axes


def _manual_validation_complete(ctx: RuleContext) -> bool:
    for _, d in _metrics_json_payloads(ctx):
        if _validation_artifact_manual_complete(d):
            return True
    return False


def _validation_evidence_contract(ctx: RuleContext) -> tuple[bool, dict[str, object]]:
    """
    Minimum credible validation readiness: health + layout + evidence depth.

    Satisfied by either:
    - Manual path: ``argus_validation_evidence`` + ``validation_status`` in (complete, passed, …).
    - Evidence path: non-bootstrap operational KPI metrics plus at least one exercise axis
      (analytics, execution outcome, non-seed JSONL, content depth, or validation artifact file).

    Answers (explicit):
    - Bootstrap-only products never satisfy the contract.
    - One seeded placeholder node is not enough unless content_evidence or another axis fires.
    - Operational metrics alone are not enough — a second axis is required unless manual complete.
    """
    health_ok = any(
        s.payload.get("active") is True and s.payload.get("stale") is not True
        for s in _signals_of(ctx, SignalType.HEALTH)
    )
    fs_ok = not any(
        s.payload.get("check") == "path_exists" and s.payload.get("ok") is False
        for s in _signals_of(ctx, SignalType.FILESYSTEM)
    )
    pairs = _metrics_json_payloads(ctx)
    has_operational_metrics = any(not _is_bootstrap_metrics_data(d) for _, d in pairs)
    secondary = _collect_secondary_validation_axes(ctx)
    manual = _manual_validation_complete(ctx)

    path_exercise = has_operational_metrics and len(secondary) > 0
    validation_ready = bool(health_ok and fs_ok and (manual or path_exercise))

    detail: dict[str, object] = {
        "health_ok": health_ok,
        "fs_ok": fs_ok,
        "operational_metrics_present": has_operational_metrics,
        "secondary_validation_axes": secondary,
        "manual_validation_complete": manual,
        "validation_evidence_contract": validation_ready,
    }
    return validation_ready, detail


@dataclass(frozen=True)
class _EarlyStageReadiness:
    stage: LifecycleStage
    health_ok: bool
    fs_ok: bool
    has_structural_metrics: bool
    has_operational_metrics: bool
    validation_ready: bool
    validation_detail: dict[str, object]
    launch_eligible: bool
    structural_eligible: bool


def _early_stage_readiness(ctx: RuleContext) -> _EarlyStageReadiness:
    """Shared gates for launch, structural, and validation-readiness rules."""
    stage = ctx.product.lifecycle.stage
    if not isinstance(stage, LifecycleStage):
        stage = LifecycleStage(str(stage))

    health_ok = any(
        s.payload.get("active") is True and s.payload.get("stale") is not True
        for s in _signals_of(ctx, SignalType.HEALTH)
    )
    fs_ok = not any(
        s.payload.get("check") == "path_exists" and s.payload.get("ok") is False
        for s in _signals_of(ctx, SignalType.FILESYSTEM)
    )
    pairs = _metrics_json_payloads(ctx)
    has_structural_metrics = bool(pairs)
    has_operational_metrics = any(not _is_bootstrap_metrics_data(d) for _, d in pairs)

    validation_ready, validation_detail = _validation_evidence_contract(ctx)

    # Launch candidacy: declared validate stage + full validation evidence contract (not just YAML).
    launch_eligible = (
        health_ok
        and fs_ok
        and validation_ready
        and stage == LifecycleStage.VALIDATE
    )
    # Declared validate without evidence: ValidationEvidenceGapRule covers that case — do not
    # also emit structural_readiness (would duplicate "thin" semantics).
    structural_eligible = (
        health_ok
        and fs_ok
        and has_structural_metrics
        and not launch_eligible
        and not validation_ready
        and stage != LifecycleStage.VALIDATE
    )
    return _EarlyStageReadiness(
        stage=stage,
        health_ok=health_ok,
        fs_ok=fs_ok,
        has_structural_metrics=has_structural_metrics,
        has_operational_metrics=has_operational_metrics,
        validation_ready=validation_ready,
        validation_detail=validation_detail,
        launch_eligible=launch_eligible,
        structural_eligible=structural_eligible,
    )


class InactivityRule(FindingRule):
    """No recent activity or stale outputs (health + filesystem freshness)."""

    rule_id: ClassVar[str] = "inactivity"

    def evaluate(self, ctx: RuleContext) -> list[FindingCandidate]:
        contributing: list[str] = []
        hints: list[str] = []
        for s in _signals_of(ctx, SignalType.HEALTH):
            p = s.payload
            if p.get("active") is False:
                contributing.append(s.id)
                hints.append("health reports inactive")
            elif p.get("stale") is True:
                contributing.append(s.id)
                hints.append("health reports stale activity")
        for s in _signals_of(ctx, SignalType.FILESYSTEM):
            p = s.payload
            if p.get("check") == "directory_freshness" and p.get("stale") is True:
                contributing.append(s.id)
                hints.append("filesystem newest output is stale")
            if p.get("check") == "file_mtime" and p.get("stale") is True:
                contributing.append(s.id)
                hints.append("key file mtime is stale")
        if not contributing:
            return []
        return [
            FindingCandidate(
                rule_id=self.rule_id,
                issue_key="no_recent_activity",
                kind=FindingKind.INACTIVITY,
                title="Insufficient recent product activity",
                summary="; ".join(hints),
                recommendation=(
                    "Verify pipelines or schedules; confirm outputs land under expected paths."
                ),
                source_signal_ids=contributing,
                evidence={"hints": hints},
                confidence=0.72,
            )
        ]


class LowSignalRule(FindingRule):
    """Too few observations to judge viability."""

    rule_id: ClassVar[str] = "low_signal"

    def evaluate(self, ctx: RuleContext) -> list[FindingCandidate]:
        n = len(ctx.signals)
        mf = _manifest_declaration_count(ctx.signals)
        nm = max(0, n - mf)
        metrics = _signals_of(ctx, SignalType.METRICS)
        empty_metrics = sum(
            1
            for s in metrics
            if s.payload.get("note") == "no JSON/JSONL metrics files found under metrics/"
            or s.payload.get("check") == "metrics_files"
        )
        # When manifest rows inflate `n`, use non-manifest count for volume heuristics.
        eff = nm if mf > 0 else n
        if eff >= 6 and empty_metrics == 0:
            return []
        if eff < 4 or (len(metrics) <= 1 and empty_metrics >= 1):
            ptype = ""
            if ctx.product.type_info and ctx.product.type_info.type:
                ptype = str(ctx.product.type_info.type).strip()
            title, summary, recommendation = _low_signal_wordings(
                n=n,
                nm=nm,
                mf=mf,
                product_type=ptype,
                metrics_n=len(metrics),
                empty_metrics_rows=empty_metrics,
            )
            evidence: dict[str, object] = {
                "signal_count": n,
                "non_manifest_signal_count": nm,
                "manifest_declaration_count": mf,
                "metrics_signal_count": len(metrics),
                "finding_subkind": "insufficient_signals",
            }
            if ptype:
                evidence["product_type"] = ptype
            return [
                FindingCandidate(
                    rule_id=self.rule_id,
                    issue_key="insufficient_signals",
                    kind=FindingKind.RELIABILITY_PROBLEM,
                    title=title,
                    summary=summary,
                    recommendation=recommendation,
                    source_signal_ids=[s.id for s in ctx.signals[:12]],
                    evidence=evidence,
                    confidence=0.55,
                )
            ]
        return []


class CostRiskRule(FindingRule):
    """Spend exceeds declared cap from local cost snapshot."""

    rule_id: ClassVar[str] = "cost_risk"

    def evaluate(self, ctx: RuleContext) -> list[FindingCandidate]:
        cap = ctx.product.constraints.max_monthly_cost_usd
        if cap is None:
            return []
        out: list[FindingCandidate] = []
        for s in _signals_of(ctx, SignalType.COST):
            p = s.payload
            if not p.get("ok", True):
                continue
            m = p.get("monthly_usd")
            if m is None:
                continue
            try:
                val = float(m)
            except (TypeError, ValueError):
                continue
            if val > cap:
                out.append(
                    FindingCandidate(
                        rule_id=self.rule_id,
                        issue_key="over_budget",
                        kind=FindingKind.COST_RISK,
                        title="Monthly cost exceeds product cap",
                        summary=(
                            f"Reported monthly_usd {val} exceeds max_monthly_cost_usd {cap}."
                        ),
                        recommendation=(
                            "Review spend drivers; tighten resources or update the cap in product.yaml."
                        ),
                        source_signal_ids=[s.id],
                        evidence={
                            "monthly_usd": val,
                            "cap_usd": cap,
                            "file": p.get("file"),
                        },
                        confidence=0.88,
                    )
                )
        return out


class GrowthOpportunityRule(FindingRule):
    """Engagement up materially vs prior window (requires both values in snapshot)."""

    rule_id: ClassVar[str] = "growth_opportunity"

    def evaluate(self, ctx: RuleContext) -> list[FindingCandidate]:
        out: list[FindingCandidate] = []
        for s in _signals_of(ctx, SignalType.ANALYTICS):
            root = s.payload if isinstance(s.payload, dict) else {}
            snippet = root.get("snippet") if isinstance(root.get("snippet"), dict) else {}
            views = root.get("views")
            if views is None:
                views = snippet.get("views")
            prior = snippet.get("views_prior_7d") or snippet.get("views_prior")
            if views is None or prior is None:
                continue
            try:
                v, pv = float(views), float(prior)
            except (TypeError, ValueError):
                continue
            if pv <= 0:
                continue
            ratio = v / pv
            if ratio >= 1.25 and v >= 50:
                out.append(
                    FindingCandidate(
                        rule_id=self.rule_id,
                        issue_key="engagement_up",
                        kind=FindingKind.GROWTH_OPPORTUNITY,
                        title="Engagement trending up vs prior window",
                        summary=(
                            f"Views {v:.0f} vs prior {pv:.0f} (ratio {ratio:.2f})."
                        ),
                        recommendation=(
                            "Consider doubling down on the channel or content driving the lift."
                        ),
                        source_signal_ids=[s.id],
                        evidence={"views": v, "views_prior": pv, "ratio": ratio},
                        confidence=min(0.85, 0.5 + 0.1 * (ratio - 1.0)),
                    )
                )
        return out


class DeprecationCandidateRule(FindingRule):
    """Long inactivity / no traction — candidate for sunsetting."""

    rule_id: ClassVar[str] = "deprecation_candidate"

    def evaluate(self, ctx: RuleContext) -> list[FindingCandidate]:
        stage = ctx.product.lifecycle.stage
        inactive = False
        sig_ids: list[str] = []
        for s in _signals_of(ctx, SignalType.HEALTH):
            p = s.payload
            age = p.get("age_seconds")
            if p.get("active") is False:
                inactive = True
                sig_ids.append(s.id)
            elif isinstance(age, (int, float)) and age > _SECONDS_30D:
                inactive = True
                sig_ids.append(s.id)
        low_traction = False
        for s in _signals_of(ctx, SignalType.ANALYTICS):
            p = s.payload.get("snippet") if isinstance(s.payload.get("snippet"), dict) else s.payload
            if not isinstance(p, dict):
                continue
            v = p.get("views")
            if v is not None and float(v) < 25:
                low_traction = True
                sig_ids.append(s.id)
        if not inactive or not low_traction:
            return []
        if stage in (LifecycleStage.KILL, LifecycleStage.IDEA):
            return []
        return [
            FindingCandidate(
                rule_id=self.rule_id,
                issue_key="inactive_low_traction",
                kind=FindingKind.DEPRECATION_CANDIDATE,
                title="Product shows prolonged inactivity and low traction",
                summary=(
                    "Health reports inactivity or long idle period; analytics views are very low."
                ),
                recommendation=(
                    "Evaluate deprecation path: announce read-only, export data, schedule shutdown."
                ),
                source_signal_ids=list(dict.fromkeys(sig_ids)),
                evidence={"lifecycle_stage": stage.value},
                confidence=0.62,
            )
        ]


class LaunchCandidateRule(FindingRule):
    """Validate stage + full validation evidence contract (deeper than YAML label alone)."""

    rule_id: ClassVar[str] = "launch_candidate"

    def evaluate(self, ctx: RuleContext) -> list[FindingCandidate]:
        r = _early_stage_readiness(ctx)
        if r.stage not in (LifecycleStage.IDEA, LifecycleStage.BUILD, LifecycleStage.VALIDATE):
            return []
        if not r.launch_eligible:
            return []

        ids = [s.id for s in ctx.signals if s.signal_type in (SignalType.HEALTH, SignalType.FILESYSTEM, SignalType.METRICS)]
        ev = dict(r.validation_detail)
        ev["lifecycle_stage"] = r.stage.value
        return [
            FindingCandidate(
                rule_id=self.rule_id,
                issue_key="readiness_launch",
                kind=FindingKind.LAUNCH_CANDIDATE,
                title="Validate stage with evidence-backed readiness — consider next exposure gate",
                summary=(
                    f"Lifecycle stage {r.stage.value}; validation evidence contract satisfied "
                    "(non-bootstrap depth plus exercise axes or manual validation completion)."
                ),
                recommendation=(
                    "Run a focused validation gate (users, SLO), then widen traffic or channel."
                ),
                source_signal_ids=ids[:20],
                evidence=ev,
                confidence=0.58,
            )
        ]


class StructuralReadinessRule(FindingRule):
    """Early lifecycle: substrate is legible (health, layout, metrics files) but not launch-ready."""

    rule_id: ClassVar[str] = "structural_readiness"

    def evaluate(self, ctx: RuleContext) -> list[FindingCandidate]:
        r = _early_stage_readiness(ctx)
        if r.stage not in (LifecycleStage.IDEA, LifecycleStage.BUILD, LifecycleStage.VALIDATE):
            return []
        if not r.structural_eligible:
            return []

        ids = [s.id for s in ctx.signals if s.signal_type in (SignalType.HEALTH, SignalType.FILESYSTEM, SignalType.METRICS)]
        boot_only = r.has_structural_metrics and not r.has_operational_metrics
        return [
            FindingCandidate(
                rule_id=self.rule_id,
                issue_key="readiness_structural",
                kind=FindingKind.STRUCTURAL_READINESS,
                title="Product node is legible; validation evidence still thin",
                summary=(
                    f"Lifecycle stage {r.stage.value}; health active, layout ok, local metrics files "
                    "present. "
                    + (
                        "Metrics appear to be seed/bootstrap inventory only."
                        if boot_only
                        else (
                            "Launch-style readiness is reserved for validate stage with operational KPI metrics."
                            if r.stage != LifecycleStage.VALIDATE
                            else "Operational metrics not yet distinguished from seed inventory."
                        )
                    )
                ),
                recommendation=(
                    "Add validation signals (usage, SLOs, or explicit non-bootstrap KPI metrics) "
                    "before treating the product as a launch candidate."
                ),
                source_signal_ids=ids[:20],
                evidence={
                    "lifecycle_stage": r.stage.value,
                    "metrics_bootstrap_only": boot_only,
                    "operational_metrics_present": r.has_operational_metrics,
                    "validation_evidence_contract": False,
                },
                confidence=0.55,
            )
        ]


class ValidationReadinessRule(FindingRule):
    """Emitted when the validation evidence contract is satisfied (independent of declared lifecycle.stage)."""

    rule_id: ClassVar[str] = "validation_readiness"

    def evaluate(self, ctx: RuleContext) -> list[FindingCandidate]:
        r = _early_stage_readiness(ctx)
        if r.stage not in (LifecycleStage.IDEA, LifecycleStage.BUILD, LifecycleStage.VALIDATE):
            return []
        if not r.validation_ready:
            return []

        ids = [s.id for s in ctx.signals if s.signal_type in (SignalType.HEALTH, SignalType.FILESYSTEM, SignalType.METRICS)]
        detail = dict(r.validation_detail)
        detail["lifecycle_stage_declared"] = r.stage.value
        stage_hint = (
            "Declared stage already validate — evidence supports validation-oriented reasoning."
            if r.stage == LifecycleStage.VALIDATE
            else "Evidence depth supports validation-oriented reasoning; consider setting lifecycle.stage to validate when you adopt that posture in product.yaml."
        )
        return [
            FindingCandidate(
                rule_id=self.rule_id,
                issue_key="validation_evidence_ok",
                kind=FindingKind.VALIDATION_READINESS,
                title="Validation evidence contract satisfied",
                summary=stage_hint,
                recommendation=(
                    "Use validation-oriented checks and gates; keep adding operational signals as you widen scope."
                ),
                source_signal_ids=ids[:20],
                evidence=detail,
                confidence=0.6,
            )
        ]


class ValidationEvidenceGapRule(FindingRule):
    """lifecycle.stage is validate but signals do not meet the validation evidence contract."""

    rule_id: ClassVar[str] = "validation_evidence_gap"

    def evaluate(self, ctx: RuleContext) -> list[FindingCandidate]:
        r = _early_stage_readiness(ctx)
        if r.stage != LifecycleStage.VALIDATE:
            return []
        if r.validation_ready:
            return []

        ids = [s.id for s in ctx.signals if s.signal_type in (SignalType.HEALTH, SignalType.FILESYSTEM, SignalType.METRICS)]
        detail = dict(r.validation_detail)
        detail["lifecycle_stage_declared"] = r.stage.value
        return [
            FindingCandidate(
                rule_id=self.rule_id,
                issue_key="validation_declaration_without_evidence",
                kind=FindingKind.VALIDATION_EVIDENCE_GAP,
                title="Declared validate stage without validation evidence contract",
                summary=(
                    "product.yaml declares lifecycle.stage validate, but local signals do not yet meet "
                    "the minimum validation evidence contract (operational + exercise axis, or manual "
                    "validation artifact completion)."
                ),
                recommendation=(
                    "Add non-bootstrap KPI metrics plus an exercise signal (analytics, execution outcome, "
                    "non-seed activity JSONL, content_evidence metric, or validation_evidence.json), or "
                    "record argus_validation_evidence with validation_status complete."
                ),
                source_signal_ids=ids[:20],
                evidence=detail,
                confidence=0.62,
            )
        ]


class QualityGapRule(FindingRule):
    """Missing expected paths or empty required directories."""

    rule_id: ClassVar[str] = "quality_gap"

    def evaluate(self, ctx: RuleContext) -> list[FindingCandidate]:
        out: list[FindingCandidate] = []
        for s in _signals_of(ctx, SignalType.FILESYSTEM):
            p = s.payload
            if p.get("check") == "path_exists" and p.get("ok") is False:
                out.append(
                    FindingCandidate(
                        rule_id=self.rule_id,
                        issue_key=f"missing_path:{p.get('relative_path', 'unknown')}",
                        kind=FindingKind.QUALITY_ISSUE,
                        title="Expected path missing on disk",
                        summary=f"Declared path {p.get('relative_path')!r} is not present.",
                        recommendation="Restore directory layout or update metrics.local_paths in product.yaml.",
                        source_signal_ids=[s.id],
                        evidence=dict(p),
                        confidence=0.9,
                    )
                )
            if p.get("check") == "directory_empty" and p.get("file_count") == 0:
                out.append(
                    FindingCandidate(
                        rule_id=self.rule_id,
                        issue_key=f"empty_dir:{p.get('relative_path', 'unknown')}",
                        kind=FindingKind.QUALITY_ISSUE,
                        title="Metrics or activity directory is empty",
                        summary=f"Directory {p.get('relative_path')!r} has no files.",
                        recommendation="Generate outputs or adjust expectations for this product stage.",
                        source_signal_ids=[s.id],
                        evidence=dict(p),
                        confidence=0.75,
                    )
                )
        return out


def default_rules() -> list[FindingRule]:
    """Ordered list of built-in rules."""
    return [
        QualityGapRule(),
        CostRiskRule(),
        InactivityRule(),
        LowSignalRule(),
        GrowthOpportunityRule(),
        TemporalSignalsRule(),
        DeprecationCandidateRule(),
        LaunchCandidateRule(),
        StructuralReadinessRule(),
        ValidationReadinessRule(),
        ValidationEvidenceGapRule(),
    ]
