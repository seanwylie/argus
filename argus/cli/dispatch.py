"""CLI dispatch: map parsed args to subsystem handlers (behavior matches legacy main)."""

from __future__ import annotations

import sys
from typing import Any

from argus.adapters.cli import run_adapters_command
from argus.advisors.cli import run_advisors_command
from argus.approval.cli import run_approval_command
from argus.audit.cli import run_audit_command
from argus.autonomy.cli import run_autonomy_command
from argus.capabilities.cli import run_capabilities_command
from argus.cli import (
    actions_cmd,
    builder_cmd,
    containment_cmd,
    dashboard_cmd,
    debug_cmd,
    decisions_cmd,
    doctor_cmd,
    doctrine_cmd,
    escalation_cmd,
    findings_cmd,
    lifecycle_cmd,
    portfolio_cmd,
    products_cmd,
    signals_cmd,
    worker_cmd,
)
from argus.cli import run_cmd as run_cmd_mod
from argus.cli.parser_common import discover_product_ids
from argus.cli.repo import repo_root
from argus.council.cli import run_council_command
from argus.economics.cli import run_economics_subcommand
from argus.execution.cli import run_execution_command
from argus.experiments.cli import run_experiments_command
from argus.history.cli import run_history_command
from argus.idea_generation.cli import run_ideas_command
from argus.input.cli import run_input_command
from argus.llm.cli import run_llm_command
from argus.orchestrator.cli import run_loop_command
from argus.orchestrator.state_cli import run_orchestration_command
from argus.planning.cli import run_planning_command
from argus.refinement.cli import run_refine_command
from argus.simulation.cli import run_simulate_command
from argus.strategy.cli import run_strategy_command
from argus.temporal.cli import run_temporal_command
from argus.trends.cli import run_trends_command
from argus.validation.cli import run_validate_command


def dispatch(args: Any) -> int:
    if args.command == "builder":
        return builder_cmd.run_builder_subcommand(args)

    if args.command == "debug":
        return debug_cmd.run_debug_subcommand(args)

    if args.command == "mission":
        from argus.mission.cli import run_mission_command

        return run_mission_command(args)

    if args.command == "policy":
        from argus.policy.cli import run_policy_command

        return run_policy_command(args)

    if args.command == "products":
        return products_cmd.run_products_subcommand(args)

    if args.command == "signals":
        return signals_cmd.run_signals_subcommand(args)

    if args.command == "temporal":
        return run_temporal_command(args)

    if args.command == "adapters":
        return run_adapters_command(args)

    if args.command == "findings":
        return findings_cmd.run_findings_subcommand(args)

    if args.command == "decisions":
        return decisions_cmd.run_decisions_subcommand(args)

    if args.command == "lifecycle":
        return lifecycle_cmd.run_lifecycle_subcommand(args)

    if args.command == "portfolio":
        return portfolio_cmd.run_portfolio_subcommand(args)

    if args.command == "doctrine":
        return doctrine_cmd.run_doctrine_subcommand(args)

    if args.command == "autonomy":
        return run_autonomy_command(args)

    if args.command == "containment":
        return containment_cmd.run_containment_command(args)

    if args.command == "validate":
        return run_validate_command(args)

    if args.command == "doctor":
        return doctor_cmd.run_doctor_command(args)

    if args.command == "dashboard":
        return dashboard_cmd.run_dashboard_command(args)

    if args.command == "actions":
        return actions_cmd.run_actions_subcommand(args)

    if args.command == "approval":
        return run_approval_command(args)

    if args.command == "execution":
        return run_execution_command(args)

    if args.command == "history":
        return run_history_command(args)

    if args.command == "trends":
        return run_trends_command(args)

    if args.command == "planning":
        return run_planning_command(args)

    if args.command == "input":
        return run_input_command(args)

    if args.command == "audit":
        return run_audit_command(args, repo_root())

    if args.command == "council":
        return run_council_command(args, repo_root())

    if args.command == "capabilities":
        return run_capabilities_command(args)

    if args.command == "self":
        from argus.self.cli import run_self_command

        return run_self_command(args)

    if args.command == "confidence":
        from argus.decision_assessment.cli import dispatch_confidence

        return dispatch_confidence(args, repo_root())

    if args.command == "advisors":
        return run_advisors_command(args)

    if args.command == "experiments":
        return run_experiments_command(args)

    if args.command == "ideas":
        return run_ideas_command(args)

    if args.command == "llm":
        return run_llm_command(args)

    if args.command == "refine":
        return run_refine_command(args, repo_root())

    if args.command == "strategy":
        return run_strategy_command(args)

    if args.command == "simulate":
        return run_simulate_command(args)

    if args.command == "orchestration":
        return run_orchestration_command(args, repo_root())

    if args.command == "loop":
        return run_loop_command(args)

    if args.command == "run":
        return run_cmd_mod.run_run_subcommand(args)

    if args.command == "economics":
        return run_economics_subcommand(args)

    if args.command == "escalation":
        return escalation_cmd.run_escalation_subcommand(args)

    if args.command == "worker":
        return worker_cmd.run_worker_subcommand(args)

    if args.command == "reset":
        from argus.cli import reset_cmd

        return reset_cmd.run_reset_command(args, repo_root())

    if args.command == "world-context":
        from argus.cli.world_context_cmd import run_world_context_command

        return run_world_context_command(args, repo_root())

    if args.command == "scan":
        root = args.products_dir or (repo_root() / "products")
        pairs = discover_product_ids(root)
        if not pairs:
            print(
                f"No products found under {root} (expected products/*/product.yaml).",
                file=sys.stderr,
            )
            return 1
        for _path, pid in pairs:
            print(pid)
        return 0

    return 2
