"""Phase 1 project permission policy (yes / no / confirm) — thin layer over provider enforcement."""

from argus.project_permissions.defaults import DEFAULT_PHASE1
from argus.project_permissions.errors import ProjectPermissionPolicyError
from argus.project_permissions.gate import (
    evaluate_phase1_for_keys,
    infer_phase1_key,
    phase1_summary_for_product,
    require_phase1_for_execution,
)
from argus.project_permissions.load import (
    default_policy_yaml_text,
    load_project_permission_policy,
    policy_file_path,
    write_default_policy_file,
)
from argus.project_permissions.model import ProjectPermissionPolicy
from argus.project_permissions.schema import PHASE1_KEYS, PROJECT_PERMISSION_POLICY_SCHEMA

__all__ = [
    "DEFAULT_PHASE1",
    "PHASE1_KEYS",
    "ProjectPermissionPolicyError",
    "PROJECT_PERMISSION_POLICY_SCHEMA",
    "ProjectPermissionPolicy",
    "default_policy_yaml_text",
    "evaluate_phase1_for_keys",
    "infer_phase1_key",
    "load_project_permission_policy",
    "phase1_summary_for_product",
    "policy_file_path",
    "require_phase1_for_execution",
    "write_default_policy_file",
]
