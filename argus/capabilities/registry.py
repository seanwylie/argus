"""Declared capabilities and deterministic gap inference."""

from __future__ import annotations

from pathlib import Path

from argus.capabilities.models import Capability, MissingCapability
from argus.signals.snapshots.registry import snapshot_type_catalog


def current_capabilities() -> list[Capability]:
    """
    Curated registry of behaviors shipped in this Argus tree.

    ``last_updated`` is informational (scaffold); bump when capabilities change materially.
    """
    u = "2026-04-12"
    return [
        Capability(
            id="cap.ingestion.signals.builtin",
            name="Built-in signal adapters",
            description="Filesystem, metrics files, cost files, analytics files, heartbeat.",
            category="ingestion",
            maturity="basic",
            coverage="Local files, declared signal types on product.yaml",
            known_gaps=("No remote SaaS pull without snapshots or future connectors.",),
            last_updated=u,
        ),
        Capability(
            id="cap.ingestion.snapshots.business",
            name="Business snapshot ingestion",
            description="Normalize JSON/CSV business metrics from metrics/snapshots/.",
            category="ingestion",
            maturity="basic",
            coverage="PostHog, GA, AWS cost, Stripe revenue, mobile/app metrics filenames",
            known_gaps=("Adapter set is file-convention based; odd filenames may not match.",),
            last_updated=u,
        ),
        Capability(
            id="cap.analysis.findings",
            name="Findings generation",
            description="Rule engine over signals → structured findings.",
            category="analysis",
            maturity="basic",
            coverage="Cost, activity, quality, growth, deprecation heuristics",
            known_gaps=("Domain-specific rules require explicit rules per product class.",),
            last_updated=u,
        ),
        Capability(
            id="cap.analysis.decisions",
            name="Decision candidates + lifecycle scoring",
            description="Lifecycle assessment and ranked candidates from findings.",
            category="analysis",
            maturity="basic",
            coverage="Per-product decisions; portfolio ranking",
            known_gaps=("No automatic execution of shell actions.",),
            last_updated=u,
        ),
        Capability(
            id="cap.analysis.portfolio",
            name="Portfolio refresh",
            description="End-to-end validate → signals → findings → decisions → report.",
            category="analysis",
            maturity="basic",
            coverage="Single-repo batch pipeline",
            known_gaps=("No cross-repo aggregation.",),
            last_updated=u,
        ),
        Capability(
            id="cap.analysis.history_trends",
            name="History snapshots and trends",
            description="Snapshot diffs and drift/trend rules over time.",
            category="analysis",
            maturity="basic",
            coverage="runs/history, runs/trends",
            known_gaps=("Requires periodic snapshots for meaningful trends.",),
            last_updated=u,
        ),
        Capability(
            id="cap.planning.weekly",
            name="Weekly portfolio planning",
            description="Deterministic weekly plan from inventory, artifacts, escalations.",
            category="planning",
            maturity="basic",
            coverage="runs/planning weekly.md/json",
            known_gaps=("Heuristic prioritization only; no execution.",),
            last_updated=u,
        ),
        Capability(
            id="cap.input.human",
            name="Human input layer",
            description="Structured global/product inputs influencing interpretation.",
            category="planning",
            maturity="basic",
            coverage="runs/input, products/*/input",
            known_gaps=("No UI beyond filesystem/CLI.",),
            last_updated=u,
        ),
        Capability(
            id="cap.ui.dashboard",
            name="Static HTML dashboard",
            description="Portfolio HTML from local runs/ artifacts.",
            category="ui",
            maturity="basic",
            coverage="runs/dashboard/index.html",
            known_gaps=("No auth, no server-side refresh.",),
            last_updated=u,
        ),
        Capability(
            id="cap.execution.actions_contract",
            name="Action contract validation",
            description="Validate and dry-run YAML/JSON action contracts.",
            category="execution",
            maturity="basic",
            coverage="Schema + inventory checks; dry-run only",
            known_gaps=("No scheduler; no automatic execution.",),
            last_updated=u,
        ),
        Capability(
            id="cap.escalation.packets",
            name="Escalation packets",
            description="Halt-and-handoff packets when automation must stop.",
            category="analysis",
            maturity="basic",
            coverage="runs/escalations",
            known_gaps=("Human must act on packets outside Argus.",),
            last_updated=u,
        ),
        Capability(
            id="cap.orchestrator.loop",
            name="Local orchestration loop",
            description="Optional staged pipeline runner (filesystem only).",
            category="execution",
            maturity="stub",
            coverage="CLI loop stages when present in repo",
            known_gaps=("Not a production orchestrator.",),
            last_updated=u,
        ),
    ]


def _snapshot_adapters_declared() -> set[str]:
    return {row["adapter_id"] for row in snapshot_type_catalog()}


def snapshot_usage_filenames(repo_root: Path) -> set[str]:
    """Lowercased filenames seen under products/*/metrics/snapshots (best-effort)."""
    names: set[str] = set()
    root = repo_root.resolve()
    products = root / "products"
    if not products.is_dir():
        return names
    for child in products.iterdir():
        if not child.is_dir():
            continue
        snap = child / "metrics" / "snapshots"
        if not snap.is_dir():
            continue
        for f in snap.iterdir():
            if f.is_file():
                names.add(f.name.lower())
    return names


def infer_missing_capabilities(repo_root: Path) -> list[MissingCapability]:
    """
    Declare gaps that are not implemented (or infer light coverage holes).

    Deterministic: same repo layout → same gaps list (aside from optional usage hints).
    """
    gaps: list[MissingCapability] = []
    declared = _snapshot_adapters_declared()
    used_names = snapshot_usage_filenames(repo_root)

    # Always-scaffold gaps (future work)
    gaps.append(
        MissingCapability(
            id="gap.execution.experiment_tracking",
            name="Experiment assignment tracking",
            description="First-class A/B or experiment exposure metrics and adapters.",
            category="execution",
            reason="No experiment snapshot adapter or signal type in this codebase.",
            priority=10,
        )
    )
    gaps.append(
        MissingCapability(
            id="gap.analysis.causal_attribution",
            name="Causal attribution beyond heuristics",
            description="Model-level attribution for marketing vs product changes.",
            category="analysis",
            reason="Not implemented; findings use rules and deltas only.",
            priority=40,
        )
    )
    gaps.append(
        MissingCapability(
            id="gap.ui.interactive_portfolio",
            name="Interactive authenticated portfolio UI",
            description="Live web UI with auth and multi-operator workflows.",
            category="ui",
            reason="Dashboard is static HTML from local files only.",
            priority=60,
        )
    )

    # Infer: Stripe adapter exists in catalog but no Stripe-named snapshots in repo
    if "stripe_revenue_snapshot" in declared:
        has_stripe_file = any("stripe" in n for n in used_names)
        if not has_stripe_file:
            gaps.append(
                MissingCapability(
                    id="gap.ingestion.revenue_snapshots_unused",
                    name="Revenue snapshot data not observed in repo",
                    description="Stripe snapshot adapter exists but no matching snapshot files under products.",
                    category="ingestion",
                    reason="No metrics/snapshots file name containing 'stripe' under any product.",
                    priority=25,
                )
            )

    # Infer: no snapshot files at all → broad ingestion gap signal
    if not used_names:
        gaps.append(
            MissingCapability(
                id="gap.ingestion.no_business_snapshots",
                name="No business snapshot files",
                description="Operators have not placed JSON/CSV snapshots under products/*/metrics/snapshots/.",
                category="ingestion",
                reason="Directory scan found no snapshot files.",
                priority=15,
            )
        )

    # Dedupe by id (deterministic first wins)
    seen: set[str] = set()
    out: list[MissingCapability] = []
    for g in sorted(gaps, key=lambda x: (x.priority, x.id)):
        if g.id not in seen:
            seen.add(g.id)
            out.append(g)
    return out


def suggest_next_build(missing: list[MissingCapability]) -> str | None:
    """Lowest priority number first; tie-break by id."""
    if not missing:
        return None
    best = sorted(missing, key=lambda x: (x.priority, x.id))[0]
    return best.id
