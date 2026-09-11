"""Evaluate yes / no / confirm against policy and optional environment alignment."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from argus.actions.models import ActionContract
from argus.project_permissions.audit import PHASE1_PERMISSION_DECISION_SCHEMA
from argus.project_permissions.environment import environment_supports_phase1
from argus.project_permissions.errors import ProjectPermissionPolicyError
from argus.project_permissions.load import load_project_permission_policy
from argus.project_permissions.schema import PHASE1_KEYS, Phase1PermissionKey


def _norm_cmd(cmd: str) -> str:
    return " ".join(cmd.lower().split())


def infer_phase1_key(contract: ActionContract) -> Phase1PermissionKey:
    """
    Map an action contract to a Phase 1 permission key.

    Callers may set ``project_permission_key`` on the contract for an explicit override.
    """
    explicit = contract.project_permission_key
    if isinstance(explicit, str) and explicit.strip():
        e = explicit.strip()
        if e in PHASE1_KEYS:
            return e  # type: ignore[return-value]
    cmd = _norm_cmd(contract.command)
    at = contract.normalized_action_type()

    if contract.experiment_id:
        return "change_experiments"

    if "git push" in cmd or "git push" in at:
        return "push_remote"
    if "git commit" in cmd or "git commit" in at:
        return "commit_local"

    if "deploy" in cmd or "serverless deploy" in cmd or at == "deploy":
        return "deploy"

    if any(x in cmd for x in ("aws ", "terraform apply", "pulumi up", "cdk deploy")):
        # Heuristic: prod vs non-prod cannot be inferred reliably — prefer nonprod bucket.
        if any(p in cmd for p in ("prod", "production", "prd")):
            return "mutate_prod"
        return "mutate_nonprod"

    if any(x in cmd for x in ("kubectl apply", "helm upgrade", "docker push")):
        return "deploy"

    if any(x in cmd for x in ("observe", "signals", "metrics", "read-only")):
        return "observe_prod_signals"

    # Default: local mutation under nonprod-like scripts
    return "mutate_nonprod"


def decide_phase1(
    policy_value: str,
) -> str:
    """Return 'allow' | 'deny' | 'confirm'."""
    v = str(policy_value or "").strip().lower()
    if v == "no":
        return "deny"
    if v == "confirm":
        return "confirm"
    if v == "yes":
        return "allow"
    return "deny"


def evaluate_phase1_for_keys(
    repo_root: Path,
    product_id: str,
    keys: Sequence[str],
    *,
    products_dir: Path | None = None,
    check_environment: bool = True,
    execution_path: str = "",
    action_description: str = "",
    orchestration_action_id: str | None = None,
    subprocess_grant_action_id: str | None = None,
) -> dict[str, Any]:
    """
    Structured Phase 1 evaluation for one or more keys (all must allow for aggregate ``allowed``).

    ``aggregate_decision`` is one of:
    ``allowed``, ``refused``, ``blocked_pending_confirmation``, ``capability_mismatch``,
    ``error_missing_product``, ``error_invalid_policy``.

    When ``orchestration_action_id`` is set (orchestration step executor), or
    ``subprocess_grant_action_id`` is set (subprocess ``action_id`` for ``confirm_once`` scope),
    Phase 2 approval grants under ``runs/policy/approval_grants/`` may satisfy ``confirm`` policy
    without editing YAML. Do not set both identifiers for one evaluation.
    """
    from argus.project_permissions.approvals import find_grant_for_confirm

    root = repo_root.resolve()
    pid = str(product_id or "").strip()
    ts = datetime.now(timezone.utc).isoformat()
    key_list = [str(k).strip() for k in keys if str(k).strip()]
    orch_aid = str(orchestration_action_id).strip() if orchestration_action_id else ""
    sub_aid = str(subprocess_grant_action_id).strip() if subprocess_grant_action_id else ""
    grant_aid = orch_aid or sub_aid

    base: dict[str, Any] = {
        "schema": PHASE1_PERMISSION_DECISION_SCHEMA,
        "product_id": pid,
        "action_description": str(action_description or "").strip(),
        "phase1_policy_fields_evaluated": list(key_list),
        "execution_path": str(execution_path or "").strip(),
        "evaluated_at_utc": ts,
        "environment_check_performed": bool(check_environment),
        "phase1_approval_applied": None,
        "phase1_pending_consumption": None,
        "phase1_approval_required": None,
        "phase1_approval_found": None,
    }

    if not pid:
        base["aggregate_decision"] = "error_missing_product"
        base["reason"] = "project permission gate: missing product_id"
        base["technical_capability_available"] = None
        base["execution_proceeds"] = False
        base["per_key"] = []
        return base

    try:
        pol = load_project_permission_policy(root, pid, products_dir=products_dir)
    except ProjectPermissionPolicyError as e:
        base["aggregate_decision"] = "error_invalid_policy"
        base["reason"] = str(e)
        base["technical_capability_available"] = None
        base["execution_proceeds"] = False
        base["per_key"] = []
        return base

    per_key: list[dict[str, Any]] = []

    for k in key_list:
        raw = pol.get(k)
        d = decide_phase1(raw)
        row: dict[str, Any] = {
            "phase1_policy_field": k,
            "phase1_policy_value": raw,
            "policy_decision": d,
            "environment_supports": None,
            "environment_reason": None,
        }
        if check_environment and d == "allow":
            ok, env_reason = environment_supports_phase1(root, pid, k)  # type: ignore[arg-type]
            row["environment_supports"] = ok
            row["environment_reason"] = env_reason
        per_key.append(row)

    # Refusal: any deny
    for row in per_key:
        if row["policy_decision"] == "deny":
            k = row["phase1_policy_field"]
            raw = row["phase1_policy_value"]
            base["aggregate_decision"] = "refused"
            base["reason"] = (
                f"project policy denies {k!r} (argus.policy.yaml: {raw!r}) — "
                f"edit products/{pid}/argus.policy.yaml to allow if appropriate."
            )
            base["technical_capability_available"] = False
            base["execution_proceeds"] = False
            base["per_key"] = per_key
            return base

    # Resolve confirm via Phase 2 grants (needs scoped action id for confirm_once)
    if grant_aid:
        for row in per_key:
            if row["policy_decision"] != "confirm":
                continue
            g = find_grant_for_confirm(root, pid, str(row["phase1_policy_field"]), grant_aid)
            if g is None:
                continue
            kind = str(g.get("kind") or "")
            if kind == "always":
                row["policy_decision"] = "allow"
                row["phase1_approval_applied"] = {
                    "response": "always",
                    "grant_id": g.get("grant_id"),
                    "phase1_policy_field": row["phase1_policy_field"],
                }
            elif kind == "confirm_once":
                row["policy_decision"] = "allow"
                row["phase1_approval_applied"] = {
                    "response": "confirm_once",
                    "grant_id": g.get("grant_id"),
                    "phase1_policy_field": row["phase1_policy_field"],
                    "orchestration_action_id": grant_aid,
                }

    # Confirmation: any confirm still unresolved blocks automatic execution
    for row in per_key:
        if row["policy_decision"] == "confirm":
            k = row["phase1_policy_field"]
            base["aggregate_decision"] = "blocked_pending_confirmation"
            base["reason"] = (
                f"project policy requires confirmation for {k!r} (value: confirm). "
                f"Argus will not run this automatically. Approve out-of-band, set policy to 'yes' for "
                f"this key after review, or attach an explicit human-approved execution path. "
                f"Policy file: products/{pid}/argus.policy.yaml"
            )
            base["technical_capability_available"] = None
            base["execution_proceeds"] = False
            base["phase1_approval_required"] = True
            base["phase1_approval_found"] = False
            base["per_key"] = per_key
            return base

    # All allow — optional environment
    if check_environment:
        for row in per_key:
            if row["policy_decision"] != "allow":
                continue
            k = row["phase1_policy_field"]
            ok = row.get("environment_supports")
            env_reason = row.get("environment_reason")
            if ok is False:
                base["aggregate_decision"] = "capability_mismatch"
                base["reason"] = (
                    f"policy allows {k!r} (yes) but environment does not currently support it: {env_reason}. "
                    f"Grant prerequisites (see `argus containment escalation-report`) or adjust policy. "
                    f"Product: {pid}"
                )
                base["technical_capability_available"] = False
                base["execution_proceeds"] = False
                base["per_key"] = per_key
                return base
        base["technical_capability_available"] = True
    else:
        base["technical_capability_available"] = None

    applied: dict[str, Any] | None = None
    pending_consume: dict[str, Any] | None = None
    for row in per_key:
        aa = row.get("phase1_approval_applied")
        if isinstance(aa, dict):
            applied = dict(aa)
            if aa.get("response") == "confirm_once" and aa.get("grant_id"):
                pending_consume = {
                    "grant_id": str(aa.get("grant_id")),
                    "product_id": pid,
                    "phase1_policy_field": str(row.get("phase1_policy_field") or ""),
                    "orchestration_action_id": grant_aid,
                }
            break

    base["aggregate_decision"] = "allowed"
    base["reason"] = "phase 1 policy allows execution for evaluated keys"
    base["execution_proceeds"] = True
    base["per_key"] = per_key
    if applied is not None:
        base["phase1_approval_applied"] = applied
    if pending_consume is not None:
        base["phase1_pending_consumption"] = pending_consume
    if applied is not None and applied.get("response") == "always":
        base["phase1_approval_found"] = True
        base["phase1_approval_required"] = True
    elif applied is not None and applied.get("response") == "confirm_once":
        base["phase1_approval_found"] = True
        base["phase1_approval_required"] = True
    return base


def require_phase1_for_execution(
    repo_root: Path,
    contract: ActionContract,
    *,
    products_dir: Path | None = None,
    check_environment: bool = True,
) -> list[str]:
    """
    Return a list of human-readable blockers (empty if execution may proceed).

    - ``no`` → refusal
    - ``confirm`` → refusal with escalation text (Phase 1: no auto-run)
    - ``yes`` → if environment does not support, refusal with mismatch text
    """
    pid = str(contract.product_id or "").strip()
    if not pid:
        return ["project permission gate: action contract missing product_id"]

    key = infer_phase1_key(contract)
    ev = evaluate_phase1_for_keys(
        repo_root,
        pid,
        (key,),
        products_dir=products_dir,
        check_environment=check_environment,
        execution_path="subprocess_execution",
        action_description=str(contract.command or "")[:2000],
        subprocess_grant_action_id=str(contract.action_id),
    )
    if ev.get("execution_proceeds") is True:
        return []
    reason = str(ev.get("reason") or "project permission gate blocked execution")
    return [reason]


def phase1_summary_for_product(
    repo_root: Path,
    product_id: str,
    *,
    products_dir: Path | None = None,
) -> dict[str, Any]:
    """Operator-facing payload: policy + per-key env alignment."""
    try:
        pol = load_project_permission_policy(repo_root, product_id, products_dir=products_dir)
    except ProjectPermissionPolicyError as e:
        return {
            "policy_load_error": str(e),
            "policy_errors": list(e.errors),
            "policy_path": str(e.policy_path) if e.policy_path else None,
            "policy": None,
            "environment_alignment": {},
            "policy_environment_mismatches": [],
        }

    from argus.project_permissions.environment import build_phase1_environment_alignment

    align = build_phase1_environment_alignment(repo_root, pol.product_id)
    mismatches: list[dict[str, Any]] = []
    for k in PHASE1_KEYS:
        pv = pol.get(k)
        env_ok = bool((align.get(k) or {}).get("environment_supports"))
        if pv in ("yes", "confirm") and not env_ok:
            mismatches.append(
                {
                    "permission_key": k,
                    "policy": pv,
                    "environment_supports": False,
                    "reason": (align.get(k) or {}).get("reason"),
                },
            )

    return {
        "policy": pol.to_summary_dict(),
        "environment_alignment": align,
        "policy_environment_mismatches": mismatches,
    }
