"""Local-only credential containment v0 — policy, capability map, subprocess env sanitization."""

from argus.containment.policy import (
    ContainmentError,
    build_capability_map,
    is_containment_enforced,
    load_containment_policy,
    refuse_unless_capability,
    sanitized_subprocess_environment,
    subprocess_env_for_repo,
)
from argus.containment.report import render_escalation_report

__all__ = [
    "ContainmentError",
    "build_capability_map",
    "is_containment_enforced",
    "load_containment_policy",
    "refuse_unless_capability",
    "render_escalation_report",
    "sanitized_subprocess_environment",
    "subprocess_env_for_repo",
]
