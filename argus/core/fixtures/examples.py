"""Realistic example objects for docs, tests, and manual exploration."""

from __future__ import annotations

from datetime import datetime, timezone

from argus.core.models.decision import ActionProposal, DecisionCandidate
from argus.core.models.enums import (
    ActionType,
    EffortBucket,
    FindingKind,
    LifecycleStage,
    RunResult,
    RunStage,
    SeverityLevel,
    SignalType,
)
from argus.core.models.finding import Finding
from argus.core.models.product import (
    ActionsMap,
    ConstraintsDefinition,
    CostDefinition,
    MetricsDefinition,
    OwnerInfo,
    ProductLifecycle,
    ProductNode,
    ProductTypeInfo,
    SignalDefinition,
)
from argus.core.models.run import RunRecord
from argus.core.models.signal import SignalRecord
from argus.core.models.validation import (
    validate_finding,
    validate_product_node,
    validate_run_record,
    validate_signal_record,
)


def example_demo_content_product_node() -> ProductNode:
    """Example content-catalog product node for tests and docs."""
    node = ProductNode(
        id="demo-content",
        name="Demo Content",
        owner=OwnerInfo(team="argus", operator="operator"),
        metrics=MetricsDefinition(
            local_paths=["metrics/"],
            primary=[
                "clips_per_day",
                "usable_clips_per_week",
                "avg_clip_score",
                "views",
            ],
        ),
        cost=CostDefinition(monthly_usd=5.0, notes="Placeholder estimate"),
        signals=[
            SignalDefinition(type="filesystem", enabled=True),
            SignalDefinition(type="analytics", enabled=False),
            SignalDefinition(type="cost", enabled=False),
        ],
        actions=ActionsMap(
            start="./scripts/start.sh",
            stop="./scripts/stop.sh",
            analyze="./scripts/analyze.sh",
        ),
        constraints=ConstraintsDefinition(
            max_monthly_cost_usd=20.0,
            min_activity_threshold=1.0,
        ),
        lifecycle=ProductLifecycle(
            stage=LifecycleStage.VALIDATE,
            next_gate="demonstrate repeatable clip generation",
        ),
        product_root="products/demo-content",
        config_path="products/demo-content/product.yaml",
        type_info=ProductTypeInfo(
            type="content_stream",
            status="experimental",
            state="validate",
        ),
    )
    validate_product_node(node)
    return node


def example_low_cost_saas_product_node() -> ProductNode:
    """Hypothetical small SaaS utility: active, low burn, maintenance stage."""
    node = ProductNode(
        id="sample-service",
        name="Sample Service",
        owner=OwnerInfo(team="argus", operator="operator"),
        metrics=MetricsDefinition(
            local_paths=["metrics/"],
            primary=["mau", "api_calls", "error_rate", "mrr_usd"],
        ),
        cost=CostDefinition(monthly_usd=12.0, notes="Shared RDS + tiny ECS task"),
        signals=[
            SignalDefinition(type="analytics", enabled=True),
            SignalDefinition(type="cost", enabled=True),
            SignalDefinition(type="logs", enabled=True),
        ],
        actions=ActionsMap(
            start="./scripts/up.sh",
            stop="./scripts/down.sh",
            analyze="./scripts/report_usage.sh",
        ),
        constraints=ConstraintsDefinition(
            max_monthly_cost_usd=75.0,
            min_activity_threshold=5.0,
        ),
        lifecycle=ProductLifecycle(
            stage=LifecycleStage.MAINTAIN,
            next_gate="keep SLO within 99.5% monthly",
        ),
        product_root="products/sample-service",
        config_path="products/sample-service/product.yaml",
        type_info=ProductTypeInfo(
            type="saas_api",
            status="active",
            state="maintain",
        ),
    )
    validate_product_node(node)
    return node


def example_deprecation_finding() -> Finding:
    """A product flagged for sunsetting based on cost and usage signals."""
    ts = datetime(2026, 4, 1, 12, 0, 0, tzinfo=timezone.utc)
    finding = Finding(
        id="find-deprec-001",
        product_id="legacywidgets",
        kind=FindingKind.DEPRECATION_CANDIDATE,
        severity=SeverityLevel.MEDIUM,
        effort=EffortBucket.SMALL,
        title="Low usage and rising static hosting cost",
        summary=(
            "Monthly active users under threshold for 90d; cost per active user "
            "exceeds portfolio median."
        ),
        recommendation=(
            "Announce read-only mode, publish export path, schedule shutdown date."
        ),
        source_signals=["sig-usage-12", "sig-cost-03"],
        evidence={"mau_90d": 4, "monthly_usd": 48},
        confidence=0.72,
        created_at=ts,
    )
    validate_finding(finding)
    return finding


def example_signal_for_deprecation() -> SignalRecord:
    """Companion signal for ``example_deprecation_finding``."""
    sig = SignalRecord(
        id="sig-cost-03",
        product_id="legacywidgets",
        signal_type=SignalType.COST,
        source="aws_cost_and_usage",
        observed_at=datetime(2026, 4, 1, 8, 15, tzinfo=timezone.utc),
        payload={"service": "s3", "monthly_usd": 48.0, "currency": "USD"},
        severity_hint=SeverityLevel.MEDIUM,
        confidence=0.9,
        tags=["cost", "portfolio-review"],
    )
    validate_signal_record(sig)
    return sig


def example_decision_candidate_deprecate() -> DecisionCandidate:
    """Possible decision tied to deprecation finding."""
    return DecisionCandidate(
        id="decide-deprec-001",
        product_id="legacywidgets",
        action_type=ActionType.DEPRECATE,
        summary="Enter read-only mode and publish shutdown timeline",
        expected_impact="Reduce monthly cost ~$45; free capacity for other products",
        estimated_cost=2.0,
        confidence=0.65,
        rationale="Usage and cost signals cross constraints for two quarters.",
        priority_score=0.81,
        metadata={"finding_id": "find-deprec-001"},
    )


def example_run_record_sample() -> RunRecord:
    """Single audit-style run embedding findings and a selected action."""
    started = datetime(2026, 4, 12, 9, 0, 0, tzinfo=timezone.utc)
    finished = datetime(2026, 4, 12, 9, 4, 30, tzinfo=timezone.utc)
    finding = example_deprecation_finding()
    proposal = ActionProposal(
        id="act-001",
        product_id="legacywidgets",
        action_type=ActionType.DEPRECATE,
        command="./scripts/enter_readonly.sh",
        reason="Match recommendation for deprecation_candidate finding find-deprec-001",
        expected_outcome="Site becomes read-only; banner shows sunset date",
        rollback_notes="Revert DNS to previous stack if rollback needed within 24h",
        selected_at=finished,
    )
    run = RunRecord(
        run_id="run-20260412-0900",
        started_at=started,
        finished_at=finished,
        stage=RunStage.SUCCEEDED,
        product_ids=["legacywidgets"],
        findings=[finding],
        selected_actions=[proposal],
        result=RunResult.PARTIAL,
        notes="Deprecation path selected; execution tracked outside this sample.",
        metadata={"trigger": "scheduled_audit"},
    )
    validate_run_record(run)
    return run
