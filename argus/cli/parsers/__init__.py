"""Register Argus CLI subcommands (split from the former monolithic main)."""

from __future__ import annotations

from argus.cli.parsers.audit import register_audit_commands
from argus.cli.parsers.autonomy import register_autonomy_commands
from argus.cli.parsers.builder import register_builder_commands
from argus.cli.parsers.containment import register_containment_commands
from argus.cli.parsers.core import register_core_commands
from argus.cli.parsers.council_capabilities import register_council_capabilities_commands
from argus.cli.parsers.debug import register_debug_commands
from argus.cli.parsers.escalation_execution import register_escalation_execution_commands
from argus.cli.parsers.experiments import register_experiments_commands
from argus.cli.parsers.findings_chain import register_findings_chain_commands
from argus.cli.parsers.history_planning_input import register_history_planning_input_commands
from argus.cli.parsers.ideas_llm_refine import register_ideas_llm_refine_commands
from argus.cli.parsers.loop_run_economics_validate_doctor_scan import (
    register_loop_run_economics_validate_doctor_scan_commands,
)
from argus.cli.parsers.mission import register_mission_commands
from argus.cli.parsers.orchestration import register_orchestration_commands
from argus.cli.parsers.policy import register_policy_commands
from argus.cli.parsers.reset import register_reset_commands
from argus.cli.parsers.self_confidence_advisors import register_self_confidence_advisors_commands
from argus.cli.parsers.strategy_sim_dashboard import register_strategy_sim_dashboard_commands
from argus.cli.parsers.worker import register_worker_commands
from argus.cli.parsers.world_context import register_world_context_commands


def register_all_commands(sub) -> None:
    """Add all top-level command groups to *sub*."""
    register_builder_commands(sub)
    register_core_commands(sub)
    register_mission_commands(sub)
    register_policy_commands(sub)
    register_findings_chain_commands(sub)
    register_autonomy_commands(sub)
    register_containment_commands(sub)
    register_escalation_execution_commands(sub)
    register_history_planning_input_commands(sub)
    register_audit_commands(sub)
    register_council_capabilities_commands(sub)
    register_self_confidence_advisors_commands(sub)
    register_experiments_commands(sub)
    register_ideas_llm_refine_commands(sub)
    register_strategy_sim_dashboard_commands(sub)
    register_orchestration_commands(sub)
    register_loop_run_economics_validate_doctor_scan_commands(sub)
    register_worker_commands(sub)
    register_debug_commands(sub)
    register_reset_commands(sub)
    register_world_context_commands(sub)
