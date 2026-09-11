"""
Stable ``rule_id`` strings for Builder reconcile / invoke trust escalation.

Shared by :mod:`argus.builder.escalation_bridge` and :mod:`argus.escalation.rules`
so the bridge does not depend on the full rules module surface for identifiers.
"""

from __future__ import annotations

# Logged in ``EscalationPacket.triggering_rules`` — keep in sync with bridge triggers.
RULE_BUILDER_ARGUS_CORE_BREACH = "builder_argus_core_breach"
RULE_BUILDER_NON_PRODUCT_ROOT_BREACH = "builder_non_product_root_breach"
RULE_BUILDER_SEMANTIC_SCOPE_BREACH = "builder_semantic_scope_breach"
RULE_BUILDER_EXECUTION_OUTCOME_BLOCKED = "builder_execution_outcome_blocked"
RULE_BUILDER_EXECUTION_OUTCOME_BREACHED = "builder_execution_outcome_breached"
RULE_BUILDER_TRUST_UNSANDBOXED = "builder_trust_unsandboxed_or_sandbox_fallback"
RULE_BUILDER_TRUST_MISSING_NO_NEW_PRIVS = "builder_trust_missing_no_new_privs"
RULE_BUILDER_TRUST_DIRTY_TREE = "builder_trust_dirty_tree_before_branch"

__all__ = [
    "RULE_BUILDER_ARGUS_CORE_BREACH",
    "RULE_BUILDER_NON_PRODUCT_ROOT_BREACH",
    "RULE_BUILDER_SEMANTIC_SCOPE_BREACH",
    "RULE_BUILDER_EXECUTION_OUTCOME_BLOCKED",
    "RULE_BUILDER_EXECUTION_OUTCOME_BREACHED",
    "RULE_BUILDER_TRUST_UNSANDBOXED",
    "RULE_BUILDER_TRUST_MISSING_NO_NEW_PRIVS",
    "RULE_BUILDER_TRUST_DIRTY_TREE",
]
