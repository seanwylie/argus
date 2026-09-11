"""
Central policy for **autonomous** execution: actions that may run without human approval.

Used by the approval auto-rules and by the execution runner when
``ARGUS_AUTONOMOUS_SAFE_EXECUTION`` or ``argus execution run --autonomous`` is set.

Allowed without approval (when heuristics pass):

- ``analyze`` / ``investigate``: read-only commands under the product tree policy
- ``generate``: writes only under ``runs/`` (internal artifacts)
- Optional ``experiment_id``: must reference an existing experiment; infrastructure experiments are excluded

Never auto-approved here: lifecycle transitions, destructive/infra/cost/account patterns.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from argus.actions.models import ActionContract
from argus.actions.validate import dangerous_patterns, resolve_working_directory
from argus.core.models.enums import ActionType
from argus.experiments.models import ExperimentType
from argus.experiments.store import load_experiment

ENV_AUTONOMOUS_SAFE_EXECUTION = "ARGUS_AUTONOMOUS_SAFE_EXECUTION"


def is_autonomous_execution_enabled() -> bool:
    v = os.environ.get(ENV_AUTONOMOUS_SAFE_EXECUTION, "").strip().lower()
    return v in ("1", "true", "yes")


@dataclass
class AutonomyEvaluation:
    """Result of :func:`evaluate_safe_autonomy`."""

    autonomous: bool
    reasons: list[str] = field(default_factory=list)


# --- Read-only mutation heuristics (conservative) ---

_RE_MUTATING_READONLY = (
    (re.compile(r"\b(?:cp|mv|install|patch|dd|truncate|ln)\b", re.I), "file copy/move/install/patch/dd/truncate/ln"),
    (re.compile(r"\b(?:rm|rmdir|unlink|shred)\b", re.I), "delete or remove paths"),
    (re.compile(r"\b(?:touch|mkdir|mktemp)\b", re.I), "create files or directories"),
    (re.compile(r"\b(?:chmod|chown|chgrp|setfacl)\b", re.I), "permission or ownership changes"),
    (re.compile(r"\bsed\s+(?:-i|--in-place)\b", re.I), "in-place file edit (sed -i)"),
    (re.compile(r"\bperl\s+-(?:pi|i)\b", re.I), "in-place file edit (perl -i)"),
    (re.compile(r"\bgit\b", re.I), "git (may modify repository state)"),
    (re.compile(r"\b(?:kubectl|helm|terraform)\b", re.I), "infrastructure / cluster tooling"),
    (re.compile(r"\b(?:docker(?:-compose)?|podman)\s+(?:compose\s+)?(?:up|run|build|push)\b", re.I), "container build or run"),
    (re.compile(r"\b(?:pip|pip3|uv)\s+install\b", re.I), "Python package install"),
    (re.compile(r"\b(?:npm|pnpm|yarn)\s+(?:install|add|ci)\b", re.I), "JS package install"),
    (re.compile(r"\b(?:rsync)\b", re.I), "rsync (may write or delete)"),
    (re.compile(r"\b(?:vim|vi|nano|emacs)\b", re.I), "interactive editor"),
)

# For ``generate``: allow mkdir/touch; still block destructive / external tooling
_RE_MUTATING_RUNS_ARTIFACT = tuple(
    x
    for x in _RE_MUTATING_READONLY
    if "create files" not in x[1]  # drop mkdir/touch/mktemp line
)


def _mask_fd_redirects(cmd: str) -> str:
    s = re.sub(r"\b[12]>&[12]\b", " ", cmd)
    return re.sub(r"\b>&[12]\b", " ", s)


def _mentions_config_change_risk(cmd: str) -> list[str]:
    reasons: list[str] = []
    if re.search(r"\b(?:kubectl|helm)\s+apply\b", cmd, re.I):
        reasons.append("command applies cluster configuration")
    if re.search(r"\bhelm\s+upgrade\b", cmd, re.I):
        reasons.append("helm upgrade may change releases")
    if re.search(r"\baws\s+s3\s+cp\b.+\s+s3://", cmd, re.I):
        reasons.append("possible upload to S3")
    return reasons


def _account_identity_blockers(cmd: str) -> list[str]:
    reasons: list[str] = []
    if re.search(r"\b(?:useradd|adduser|userdel|groupadd)\b", cmd, re.I):
        reasons.append("command may create or modify OS accounts (blocked for autonomous execution)")
    if re.search(r"\baws\s+iam\s+create\b", cmd, re.I) or re.search(r"\bgcloud\s+(?:iam|projects\s+create)\b", cmd, re.I):
        reasons.append("command may create cloud identities or projects (blocked for autonomous execution)")
    return reasons


def _cost_spending_blockers(cmd: str) -> list[str]:
    reasons: list[str] = []
    if re.search(r"\baws\s+(?:ce|pricing|purchase)\b", cmd, re.I):
        reasons.append("command may interact with billing or purchasing APIs")
    if re.search(r"\b(?:gcloud|az)\s+.*\b(?:purchase|billing)\b", cmd, re.I):
        reasons.append("command may interact with cloud billing")
    return reasons


def _shell_redirect_blockers_readonly(cmd: str, repo_root: Path, cwd: Path, product_id: str) -> list[str]:
    reasons: list[str] = []
    masked = _mask_fd_redirects(cmd)
    root = repo_root.resolve()
    prod = (root / "products" / product_id.strip()).resolve()

    for m in re.finditer(r"[12]?(?:>>|>)\s+([^\s;&|`]+)", masked):
        t = m.group(1).strip().strip("'\"")
        if not t or t.startswith("&"):
            continue
        if t in ("/dev/null", "/dev/zero"):
            continue
        raw = Path(t)
        try:
            resolved = raw.resolve() if raw.is_absolute() else (cwd / raw).resolve()
        except OSError:
            reasons.append(f"shell redirection to {t!r} (path resolution failed)")
            continue
        try:
            resolved.relative_to(prod)
            reasons.append(f"modifying files (shell redirection to {t!r} under products/{product_id}/)")
        except ValueError:
            try:
                resolved.relative_to(root)
                reasons.append(f"file write outside products/{product_id}/ (redirection to {t!r})")
            except ValueError:
                reasons.append(f"shell redirection to {t!r} (outside repository)")

    if re.search(r"\|\s*tee\b", cmd) or re.search(r"(?:^|[;&(|\s])tee\s+", cmd):
        reasons.append("tee writes to files (not read-only)")

    return reasons


def _shell_redirect_blockers_runs_only(cmd: str, repo_root: Path, cwd: Path) -> list[str]:
    reasons: list[str] = []
    masked = _mask_fd_redirects(cmd)
    runs = (repo_root / "runs").resolve()

    for m in re.finditer(r"[12]?(?:>>|>)\s+([^\s;&|`]+)", masked):
        t = m.group(1).strip().strip("'\"")
        if not t or t.startswith("&"):
            continue
        if t in ("/dev/null", "/dev/zero"):
            continue
        raw = Path(t)
        try:
            resolved = raw.resolve() if raw.is_absolute() else (cwd / raw).resolve()
            resolved.relative_to(runs)
        except OSError:
            reasons.append(f"shell redirection to {t!r} (path resolution failed)")
        except ValueError:
            reasons.append(f"artifact generation: outputs must stay under runs/, got {t!r}")

    if re.search(r"\|\s*tee\b", cmd) or re.search(r"(?:^|[;&(|\s])tee\s+", cmd):
        reasons.append("tee is not allowed for autonomous generate (use simple redirects under runs/)")

    return reasons


def _mutation_blockers_readonly(cmd: str) -> list[str]:
    reasons: list[str] = []
    for rx, label in _RE_MUTATING_READONLY:
        if rx.search(cmd):
            reasons.append(f"command may mutate state ({label})")
    return reasons


def _mutation_blockers_runs_artifact(cmd: str) -> list[str]:
    reasons: list[str] = []
    for rx, label in _RE_MUTATING_RUNS_ARTIFACT:
        if rx.search(cmd):
            reasons.append(f"command may mutate state ({label})")
    if re.search(r"\brm\b", cmd, re.I):
        reasons.append("rm is blocked for autonomous artifact generation")
    return reasons


def _experiment_autonomy_blockers(contract: ActionContract, repo_root: Path) -> list[str]:
    eid = (contract.experiment_id or "").strip()
    if not eid:
        return []
    try:
        exp = load_experiment(repo_root, eid)
    except (OSError, FileNotFoundError, ValueError, KeyError, TypeError):
        return [f"experiment_id {eid!r} not found under runs/experiments/"]
    if exp.product_id != contract.product_id:
        return ["experiment product_id does not match action product_id"]
    if exp.type == ExperimentType.INFRASTRUCTURE:
        return ["infrastructure experiments require manual approval before execution"]
    return []


_READ_ONLY_TYPES = frozenset({ActionType.ANALYZE.value, ActionType.INVESTIGATE.value})


def evaluate_safe_autonomy(
    contract: ActionContract,
    *,
    repo_root: Path,
    decision_metadata: Mapping[str, Any] | None = None,
) -> AutonomyEvaluation:
    """
    Return whether this contract may execute **without human approval** (subject to runner opt-in).

    ``decision_metadata`` is optional context from a :class:`~argus.core.models.decision.DecisionCandidate`
    (e.g. ``freshness_escalation``) when execution is tied to a specific recommendation.
    """
    reasons_ok: list[str] = []
    blockers: list[str] = []

    if contract.lifecycle_transition is not None:
        lt = contract.lifecycle_transition
        blockers.append(
            f"lifecycle_transition is set ({lt.from_stage!r} -> {lt.to_stage!r}); requires manual approval",
        )

    blockers.extend(_experiment_autonomy_blockers(contract, repo_root))

    if decision_metadata is not None:
        from argus.autonomy.constraints.freshness import freshness_autonomy_blockers

        blockers.extend(freshness_autonomy_blockers(decision_metadata, contract))

    at = contract.normalized_action_type()
    cmd = contract.command.strip()
    if not cmd:
        blockers.append("command is empty")

    root = repo_root.resolve()

    danger = dangerous_patterns(
        cmd,
        repo_root=root,
        product_id=contract.product_id.strip(),
        lifecycle=contract.lifecycle_transition,
    )
    if danger:
        blockers.extend(f"unsafe pattern: {d}" for d in danger)

    blockers.extend(_mentions_config_change_risk(cmd))
    blockers.extend(_account_identity_blockers(cmd))
    blockers.extend(_cost_spending_blockers(cmd))

    wd_path, wd_err = resolve_working_directory(root, contract.working_directory)
    if wd_err:
        blockers.append(f"working_directory: {wd_err}")

    if at in _READ_ONLY_TYPES:
        blockers.extend(_mutation_blockers_readonly(cmd))
        if wd_path is not None:
            blockers.extend(_shell_redirect_blockers_readonly(cmd, root, wd_path, contract.product_id))
        else:
            masked = _mask_fd_redirects(cmd)
            if re.search(r"[12]?(?:>>|>)\s+[^\s;&|`]+", masked) and not re.search(
                r"[12]?(?:>>|>)\s+/dev/null\b", masked
            ):
                blockers.append("shell file redirection (cannot classify paths without valid working_directory)")
    elif at == ActionType.GENERATE.value:
        if wd_path is None:
            pass
        else:
            runs_root = (root / "runs").resolve()
            try:
                wd_path.resolve().relative_to(runs_root)
            except ValueError:
                blockers.append(
                    f"action_type generate requires working_directory under runs/ (got {contract.working_directory!r})",
                )
        blockers.extend(_mutation_blockers_runs_artifact(cmd))
        if wd_path is not None:
            try:
                wd_path.resolve().relative_to((root / "runs").resolve())
                blockers.extend(_shell_redirect_blockers_runs_only(cmd, root, wd_path))
            except ValueError:
                pass
    else:
        blockers.append(
            f"action_type is {contract.action_type!r} "
            f"(autonomous types: {', '.join(sorted(_READ_ONLY_TYPES | {ActionType.GENERATE.value}))})",
        )

    if not blockers:
        if at in _READ_ONLY_TYPES:
            reasons_ok.append(f"action_type is {at}")
            reasons_ok.append("command appears read-only (heuristic)")
            reasons_ok.append("no lifecycle_transition on contract")
        elif at == ActionType.GENERATE.value:
            reasons_ok.append("action_type is generate")
            reasons_ok.append("working_directory and outputs constrained to runs/")
        if contract.experiment_id:
            reasons_ok.append(f"experiment linkage {contract.experiment_id!r} allowed")
        if wd_path is not None:
            reasons_ok.append(f"working_directory resolves ({contract.working_directory})")
        return AutonomyEvaluation(autonomous=True, reasons=reasons_ok)

    return AutonomyEvaluation(autonomous=False, reasons=blockers)
