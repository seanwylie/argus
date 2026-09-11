"""
Autonomous product shutdown: evaluate kill pressure, deprecate, archive, cleanup (stub).

Safety: default is dry-run; ``--apply`` requires ``--approve``.
"""

from __future__ import annotations

import shutil
import warnings
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from argus.autonomy.provider_cleanup import plan_provider_cleanup
from argus.core.models.enums import LifecycleStage
from argus.core.models.product import ProductNode
from argus.core.serialize import dumps_json, to_jsonable
from argus.lifecycle.kill import (
    KillRecommendation,
    KillScoreResult,
    compute_kill_score_for_product,
    compute_kill_scores_inventory,
)
from argus.products.inventory import build_inventory
from argus.products.loader import load_yaml_file
from argus.products.validate import validate_manifest
from argus.trends.analyze import analyze_product

DEFAULT_MIN_KILL_SCORE = 75
REPORT_SCHEMA = "argus.autonomy.shutdown_report.v1"

# Stable human-readable message for resource_cleanup_stub["notes"] (see docs/autonomy.md).
RESOURCE_CLEANUP_STUB_NOTES = (
    "No external resources torn down. Implement hooks (e.g. AWS, DNS, billing) here."
)


def _utc_ts_compact() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _resolve_product_root(repo: Path, node: ProductNode) -> Path:
    return (repo / node.product_root).resolve()


def _deprecation_stage(node: ProductNode) -> LifecycleStage:
    """Prefer ``decline`` when the lifecycle graph allows it; otherwise terminal ``kill``."""
    if node.lifecycle.can_transition_to(LifecycleStage.DECLINE):
        return LifecycleStage.DECLINE
    return LifecycleStage.KILL


def _write_product_yaml(repo: Path, node: ProductNode, data: dict[str, Any]) -> None:
    try:
        import yaml  # type: ignore[import-untyped]
    except ImportError as e:  # pragma: no cover
        raise ImportError("PyYAML is required to update product manifests.") from e
    cfg = (repo / node.config_path).resolve()
    text = yaml.safe_dump(
        data,
        sort_keys=False,
        default_flow_style=False,
        allow_unicode=True,
    )
    cfg.write_text(text, encoding="utf-8")


def _mark_deprecated(repo: Path, node: ProductNode) -> LifecycleStage:
    """Update ``product.yaml`` lifecycle to deprecated / terminal wind-down stage."""
    cfg = (repo / node.config_path).resolve()
    raw, err = load_yaml_file(cfg)
    if err or raw is None:
        raise ValueError(f"Cannot load manifest: {err}")
    target = _deprecation_stage(node)
    life = raw.get("lifecycle")
    if not isinstance(life, dict):
        raise ValueError("product.yaml missing lifecycle mapping")
    life = dict(life)
    life["stage"] = target.value
    life["next_gate"] = (
        f"Autonomy shutdown ({_utc_ts_compact()}): product marked deprecated; archive pending."
    )
    raw["lifecycle"] = life
    if "state" in raw:
        raw["state"] = target.value
    pr = _resolve_product_root(repo, node)
    res = validate_manifest(raw, repo_root=repo, product_root=pr, config_path=cfg)
    if res.errors:
        raise ValueError("Updated manifest failed validation: " + "; ".join(res.errors))
    _write_product_yaml(repo, node, raw)
    return target


def _archive_product_dir(repo: Path, node: ProductNode) -> Path:
    """Move ``products/<node>/`` to ``archive/products/<id>_<ts>/``."""
    src = _resolve_product_root(repo, node)
    if not src.is_dir():
        raise FileNotFoundError(f"Product root not found: {src}")
    dest_parent = (repo / "archive" / "products").resolve()
    dest_parent.mkdir(parents=True, exist_ok=True)
    dest = dest_parent / f"{node.id}_{_utc_ts_compact()}"
    if dest.exists():
        raise FileExistsError(f"Archive destination already exists: {dest}")
    shutil.move(str(src), str(dest))
    return dest


def resource_cleanup_stub(
    product_id: str,
    *,
    archive_path: Path | None = None,
) -> dict[str, Any]:
    # ARGUS-STUB:placeholder — external infra teardown hooks (docs/stub-inventory.md)
    """
    Placeholder for cloud / SaaS teardown hooks.

    Structured seam: :func:`plan_provider_cleanup`; human-readable notes stay stable for tests/docs.
    """
    out = plan_provider_cleanup(product_id, archive_path=archive_path)
    out["notes"] = RESOURCE_CLEANUP_STUB_NOTES
    return out


@dataclass
class ShutdownSignals:
    """Inputs used to decide and document shutdown (kill score, trends, cost, inactivity)."""

    kill_score: int
    kill_recommendation: str
    trend_flags: list[str]
    trend_summary: str
    trend_interpretation: str
    monthly_cost_usd: float
    estimated_revenue_usd: float
    inactivity_dimension: float
    inactivity_days: float
    no_usage_signal: bool
    orphan_spend_usd_monthly: float = 0.0
    orphan_resource_count: int = 0
    mapped_resource_cost_usd: float = 0.0


@dataclass
class ArchivePlan:
    """Structured archive + provider cleanup outline (dry-run default; apply still gated)."""

    product_id: str
    steps: list[dict[str, Any]]
    provider_cleanup_plan: dict[str, Any]
    rationale_notes: list[str]
    schema: str = "argus.archive_plan.v1"


def build_archive_plan(repo: Path, product_id: str, node: ProductNode) -> ArchivePlan:
    """Deterministic steps for deprecate → move tree → provider hooks; includes economics rationale."""
    root = repo.resolve()
    pr = _resolve_product_root(root, node)
    pc = plan_provider_cleanup(product_id, archive_path=None)
    steps: list[dict[str, Any]] = [
        {
            "id": "deprecate_manifest",
            "destructive": False,
            "detail": "Set lifecycle to decline/kill; update next_gate.",
        },
        {
            "id": "archive_tree",
            "destructive": True,
            "detail": f"Move {pr} to archive/products/<id>_<ts>/",
        },
        {
            "id": "provider_cleanup",
            "destructive": True,
            "detail": "AWS/DNS/billing teardown via capability-approved hooks",
        },
    ]
    rationale_notes: list[str] = []
    try:
        from argus.economics.resources_report import build_resource_report

        rep = build_resource_report(root)
        rationale_notes.append(
            f"Portfolio unmapped spend: ${rep.total_orphan_cost_usd:.2f}/mo "
            f"({len(rep.orphan_resources)} line item(s))."
        )
        mc = sum(r.monthly_cost_usd for r in rep.resources if r.product_id == product_id)
        rationale_notes.append(f"Registry spend mapped to this product: ${mc:.2f}/mo.")
    except Exception:
        rationale_notes.append("Economics resource report unavailable for shutdown rationale.")

    return ArchivePlan(
        product_id=product_id,
        steps=steps,
        provider_cleanup_plan=pc,
        rationale_notes=rationale_notes,
    )


def gather_shutdown_signals(repo: Path, product_id: str, ks: KillScoreResult) -> ShutdownSignals:
    tr = analyze_product(repo, product_id)
    notes = ks.notes or {}
    orphan_spend = 0.0
    orphan_count = 0
    mapped_cost = 0.0
    try:
        from argus.economics.resources_report import build_resource_report

        rep = build_resource_report(repo)
        orphan_spend = float(rep.total_orphan_cost_usd)
        orphan_count = len(rep.orphan_resources)
        mapped_cost = sum(
            r.monthly_cost_usd for r in rep.resources if r.product_id == product_id
        )
    except Exception as e:
        warnings.warn(
            f"shutdown signals: economics resource linkage omitted ({type(e).__name__}: {e})",
            UserWarning,
            stacklevel=1,
        )
    return ShutdownSignals(
        kill_score=ks.kill_score,
        kill_recommendation=ks.recommendation.value,
        trend_flags=list(tr.trend_flags),
        trend_summary=tr.summary,
        trend_interpretation=tr.recommended_interpretation,
        monthly_cost_usd=float(notes.get("monthly_cost_usd") or 0.0),
        estimated_revenue_usd=float(notes.get("estimated_revenue_usd") or 0.0),
        inactivity_dimension=float(ks.dimensions.get("inactivity", 0.0)),
        inactivity_days=float(notes.get("inactivity_days") or 0.0),
        no_usage_signal=bool(notes.get("no_usage_signal")),
        orphan_spend_usd_monthly=orphan_spend,
        orphan_resource_count=orphan_count,
        mapped_resource_cost_usd=mapped_cost,
    )


def is_shutdown_eligible(
    ks: KillScoreResult | ShutdownSignals,
    *,
    min_kill_score: int,
) -> tuple[bool, str]:
    """Eligible when kill score meets threshold (default: KILL band)."""
    score = ks.kill_score if isinstance(ks, KillScoreResult) else ks.kill_score
    if score < min_kill_score:
        return False, f"kill_score {score} < min_kill_score {min_kill_score}"
    rec = (
        ks.recommendation
        if isinstance(ks, KillScoreResult)
        else KillRecommendation(str(ks.kill_recommendation))
    )
    if rec == KillRecommendation.CONTINUE:
        return False, "recommendation is continue (unexpected at high score)"
    return True, "kill_score and recommendation support wind-down"


@dataclass
class ShutdownStepResult:
    name: str
    ok: bool
    detail: str = ""
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class ProductShutdownReport:
    """Per-product shutdown plan or execution outcome."""

    product_id: str
    eligible: bool
    eligibility_reason: str
    dry_run: bool
    apply_requested: bool
    approved: bool
    signals: ShutdownSignals
    steps: list[ShutdownStepResult] = field(default_factory=list)
    deprecated_stage: str | None = None
    archive_path: str | None = None
    resource_cleanup: dict[str, Any] = field(default_factory=dict)
    archive_plan: dict[str, Any] | None = None
    error: str | None = None

    def to_jsonable(self) -> dict[str, Any]:
        return {
            "schema": REPORT_SCHEMA,
            "product_id": self.product_id,
            "eligible": self.eligible,
            "eligibility_reason": self.eligibility_reason,
            "dry_run": self.dry_run,
            "apply_requested": self.apply_requested,
            "approved": self.approved,
            "signals": to_jsonable(self.signals),
            "steps": [to_jsonable(s) for s in self.steps],
            "deprecated_stage": self.deprecated_stage,
            "archive_path": self.archive_path,
            "resource_cleanup": dict(self.resource_cleanup),
            "archive_plan": self.archive_plan,
            "error": self.error,
        }


def plan_product_shutdown(
    repo: Path,
    product_id: str,
    *,
    min_kill_score: int = DEFAULT_MIN_KILL_SCORE,
    dry_run: bool = True,
    apply_requested: bool = False,
    approved: bool = False,
    products_dir: Path | None = None,
) -> ProductShutdownReport:
    """
    Evaluate one product and optionally deprecate + archive + cleanup stub.

    When ``apply_requested`` is True, ``approved`` must also be True or no mutations occur.
    """
    root = repo.resolve()
    inv = build_inventory(root, products_dir=products_dir)
    if product_id not in inv.valid:
        return ProductShutdownReport(
            product_id=product_id,
            eligible=False,
            eligibility_reason="product not in valid inventory",
            dry_run=dry_run,
            apply_requested=apply_requested,
            approved=approved,
            signals=ShutdownSignals(
                kill_score=0,
                kill_recommendation="",
                trend_flags=[],
                trend_summary="",
                trend_interpretation="",
                monthly_cost_usd=0.0,
                estimated_revenue_usd=0.0,
                inactivity_dimension=0.0,
                inactivity_days=0.0,
                no_usage_signal=False,
                orphan_spend_usd_monthly=0.0,
                orphan_resource_count=0,
                mapped_resource_cost_usd=0.0,
            ),
            error="unknown_or_invalid_product",
        )

    node = inv.valid[product_id].node
    ks = compute_kill_score_for_product(root, product_id, node)
    sig = gather_shutdown_signals(root, product_id, ks)
    ok_elig, reason = is_shutdown_eligible(ks, min_kill_score=min_kill_score)

    report = ProductShutdownReport(
        product_id=product_id,
        eligible=ok_elig,
        eligibility_reason=reason,
        dry_run=dry_run,
        apply_requested=apply_requested,
        approved=approved,
        signals=sig,
    )
    ap = build_archive_plan(root, product_id, node)
    report.archive_plan = to_jsonable(ap)

    if not ok_elig:
        return report

    can_apply = apply_requested and approved and not dry_run
    if apply_requested and not approved:
        report.error = "apply_requires_approve"
        report.steps.append(
            ShutdownStepResult(
                name="gate",
                ok=False,
                detail="--apply requires --approve (acknowledge destructive archive).",
            )
        )
        return report

    if dry_run or not apply_requested:
        report.steps.extend(
            [
                ShutdownStepResult(
                    name="deprecate_manifest",
                    ok=True,
                    detail="Would set lifecycle to decline or kill (per graph), update next_gate.",
                    data={"would_deprecate": True},
                ),
                ShutdownStepResult(
                    name="archive_tree",
                    ok=True,
                    detail=f"Would move {_resolve_product_root(root, node)} → archive/products/<id>_<ts>/",
                    data={"would_archive": True},
                ),
                ShutdownStepResult(
                    name="resource_cleanup",
                    ok=True,
                    detail="Provider seam is stubbed until capability-approved hooks are wired.",
                    data=resource_cleanup_stub(product_id, archive_path=None),
                ),
                ShutdownStepResult(
                    name="archive_plan",
                    ok=True,
                    detail="Structured plan (deprecate → archive → provider cleanup); see archive_plan in report.",
                    data={"archive_plan": report.archive_plan},
                ),
            ]
        )
        return report

    if not can_apply:
        return report

    from argus.autonomy.quotas import check_shutdown_apply_allowed, record_shutdown_applied

    ok_q, qmsg = check_shutdown_apply_allowed(root)
    if not ok_q:
        report.error = qmsg
        report.steps.append(ShutdownStepResult(name="shutdown_quota", ok=False, detail=qmsg))
        return report

    # --- apply path ---
    try:
        stage = _mark_deprecated(root, node)
        report.deprecated_stage = stage.value
        report.steps.append(
            ShutdownStepResult(
                name="deprecate_manifest",
                ok=True,
                detail=f"Set lifecycle.stage to {stage.value}",
                data={"stage": stage.value},
            )
        )
        # Reload node path after yaml write (paths unchanged)
        arch = _archive_product_dir(root, node)
        report.archive_path = str(arch.relative_to(root))
        report.steps.append(
            ShutdownStepResult(
                name="archive_tree",
                ok=True,
                detail=f"Moved to {report.archive_path}",
                data={"archive_path": report.archive_path},
            )
        )
        rc = resource_cleanup_stub(product_id, archive_path=arch)
        report.resource_cleanup = rc
        report.steps.append(
            ShutdownStepResult(
                name="resource_cleanup",
                ok=True,
                detail=rc.get("notes", ""),
                data=rc,
            )
        )
        record_shutdown_applied(root)
    except (OSError, ValueError) as e:
        report.error = str(e)
        report.steps.append(
            ShutdownStepResult(name="shutdown", ok=False, detail=str(e)),
        )
    return report


@dataclass
class BatchShutdownReport:
    generated_at_utc: str
    min_kill_score: int
    reports: list[ProductShutdownReport]

    def to_jsonable(self) -> dict[str, Any]:
        return {
            "schema": "argus.autonomy.shutdown_batch.v1",
            "generated_at_utc": self.generated_at_utc,
            "min_kill_score": self.min_kill_score,
            "reports": [r.to_jsonable() for r in self.reports],
        }


def list_kill_candidate_ids(
    repo: Path,
    *,
    min_kill_score: int = DEFAULT_MIN_KILL_SCORE,
    products_dir: Path | None = None,
) -> list[str]:
    """Product ids whose kill score meets the threshold."""
    rows = compute_kill_scores_inventory(repo, products_dir=products_dir)
    return [r.product_id for r in rows if r.kill_score >= min_kill_score]


def run_shutdown_batch(
    repo: Path,
    *,
    product_id: str | None,
    all_candidates: bool,
    min_kill_score: int = DEFAULT_MIN_KILL_SCORE,
    dry_run: bool = True,
    apply_requested: bool = False,
    approved: bool = False,
    products_dir: Path | None = None,
) -> BatchShutdownReport:
    """Run shutdown plan (or execute) for one product or all kill-satisfying ids."""
    root = repo.resolve()
    now = datetime.now(timezone.utc).isoformat()
    if product_id:
        ids = [product_id]
    elif all_candidates:
        ids = list_kill_candidate_ids(root, min_kill_score=min_kill_score, products_dir=products_dir)
    else:
        raise ValueError("Specify --product ID or --all-candidates")

    reports = [
        plan_product_shutdown(
            root,
            pid,
            min_kill_score=min_kill_score,
            dry_run=dry_run,
            apply_requested=apply_requested,
            approved=approved,
            products_dir=products_dir,
        )
        for pid in ids
    ]
    return BatchShutdownReport(generated_at_utc=now, min_kill_score=min_kill_score, reports=reports)


def write_shutdown_report_artifact(repo: Path, batch: BatchShutdownReport) -> Path:
    out_dir = repo.resolve() / "runs" / "autonomy"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"shutdown_{_utc_ts_compact()}.json"
    path.write_text(dumps_json(batch.to_jsonable()), encoding="utf-8")
    latest = out_dir / "shutdown_latest.json"
    latest.write_text(dumps_json(batch.to_jsonable()), encoding="utf-8")
    return path


def run_autonomy_shutdown_command(args: Any) -> int:
    """CLI: ``argus autonomy shutdown``."""
    import sys

    from argus.cli.repo import repo_root

    if args.autonomy_command != "shutdown":
        print("Unknown autonomy subcommand.", file=sys.stderr)
        return 2

    root = repo_root()
    pd = getattr(args, "products_dir", None)
    products_dir = Path(pd).resolve() if pd else None

    pid = getattr(args, "shutdown_product_id", None)
    all_c = bool(getattr(args, "all_candidates", False))
    if pid and all_c:
        print("Use only one of --product or --all-candidates.", file=sys.stderr)
        return 2
    if not pid and not all_c:
        print("Specify --product ID or --all-candidates.", file=sys.stderr)
        return 2

    apply_requested = bool(getattr(args, "apply", False))
    approved = bool(getattr(args, "approve", False))
    dry_run = not apply_requested

    if apply_requested and not approved:
        print(
            "Refusing to apply: pass --approve to acknowledge deprecation + archive outside products/.",
            file=sys.stderr,
        )
        return 2

    try:
        batch = run_shutdown_batch(
            root,
            product_id=getattr(args, "shutdown_product_id", None),
            all_candidates=bool(getattr(args, "all_candidates", False)),
            min_kill_score=int(getattr(args, "min_kill_score", DEFAULT_MIN_KILL_SCORE)),
            dry_run=dry_run,
            apply_requested=apply_requested,
            approved=approved,
            products_dir=products_dir,
        )
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 2

    path = write_shutdown_report_artifact(root, batch)

    if args.json:
        print(dumps_json(batch.to_jsonable()))
    else:
        print(f"Autonomy shutdown ({batch.generated_at_utc})  min_kill_score={batch.min_kill_score}")
        print(f"Report written: {path.relative_to(root)}\n")
        for r in batch.reports:
            status = "eligible" if r.eligible else "not eligible"
            print(f"=== {r.product_id}  [{status}]  {r.eligibility_reason}")
            s = r.signals
            print(
                f"  kill_score={s.kill_score}  recommendation={s.kill_recommendation}  "
                f"cost=${s.monthly_cost_usd:.2f}/mo  rev~=${s.estimated_revenue_usd:.2f}"
            )
            print(
                f"  inactivity: risk={s.inactivity_dimension:.2f}  days={s.inactivity_days:.1f}  "
                f"no_usage={s.no_usage_signal}"
            )
            print(
                f"  economics: orphan_spend=${s.orphan_spend_usd_monthly:.2f}/mo  "
                f"orphan_items={s.orphan_resource_count}  mapped_to_product=${s.mapped_resource_cost_usd:.2f}/mo"
            )
            print(f"  trends: {', '.join(s.trend_flags) or '—'}")
            if r.error:
                print(f"  error: {r.error}")
            for st in r.steps:
                mark = "ok" if st.ok else "fail"
                print(f"  [{mark}] {st.name}: {st.detail}")
            if r.archive_path:
                print(f"  archive: {r.archive_path}")
        if dry_run:
            print("\n(dry-run: no files modified; omit --apply or use without --approve)", file=sys.stderr)
        elif apply_requested:
            print("\n(apply: manifests and archive updated where eligible)", file=sys.stderr)

    err = any(x.error for x in batch.reports)
    return 1 if err else 0
