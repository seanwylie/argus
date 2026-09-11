"""
Builder host readiness — static discovery of tools and env that affect agent containment trust.

This does **not** prove invoke will succeed (runtime permissions, product layout, git state, etc.).
"""

from __future__ import annotations

import os
import platform
import shutil
import sys
from typing import Any

from argus.builder.sandbox import (
    ENV_ALLOW_UNSANDBOXED,
    ENV_BUILDER_NETWORK_MODE,
    ENV_BUILDER_SANDBOX,
    ENV_SKIP_NO_NEW_PRIVS,
    NETWORK_MODE_DEFAULT,
    SANDBOX_MODE_AUTO,
    SANDBOX_MODE_BWRAP,
    SANDBOX_MODE_NONE,
    allow_unsandboxed_from_env,
    resolve_network_mode_from_env,
    resolve_sandbox_mode_from_env,
    which_bwrap,
    which_setpriv,
)

BUILDER_HOST_READINESS_SCHEMA = "argus.builder.host_readiness.v1"

STATUS_OK = "ok"
STATUS_DEGRADED = "degraded"
STATUS_NOT_READY = "not_ready"


def _env_flag_truthy(raw: str | None) -> bool:
    return (raw or "").strip().lower() in ("1", "true", "yes", "on")


def _effective_sandbox_mode() -> str:
    r = resolve_sandbox_mode_from_env()
    if r in (SANDBOX_MODE_AUTO, SANDBOX_MODE_BWRAP, SANDBOX_MODE_NONE):
        return r
    return SANDBOX_MODE_AUTO


def compute_builder_host_readiness() -> dict[str, Any]:
    """
    Return ``argus.builder.host_readiness.v1`` payload (checks, overall status, suggestions).

    Classification mirrors ``build_agent_containment`` refusal/degraded paths for **default** agent
    sandbox expectations, not product-specific git state.
    """
    linux = sys.platform.startswith("linux")
    bwrap = which_bwrap()
    spriv = which_setpriv()
    req = _effective_sandbox_mode()
    allow_u = allow_unsandboxed_from_env()
    skip_nnp = _env_flag_truthy(os.environ.get(ENV_SKIP_NO_NEW_PRIVS))

    net_m = resolve_network_mode_from_env() or NETWORK_MODE_DEFAULT
    env_effective: dict[str, Any] = {
        ENV_BUILDER_SANDBOX: req,
        ENV_ALLOW_UNSANDBOXED: allow_u,
        ENV_SKIP_NO_NEW_PRIVS: skip_nnp,
        ENV_BUILDER_NETWORK_MODE: net_m,
    }

    checks: list[dict[str, Any]] = []
    reasons: list[str] = []
    suggested_actions: list[str] = []

    checks.append(
        {
            "id": "platform",
            "status": "info",
            "detail": {
                "system": platform.system(),
                "release": platform.release(),
                "machine": platform.machine(),
                "linux": linux,
            },
        }
    )

    # --- bubblewrap ---
    if bwrap:
        checks.append(
            {
                "id": "bubblewrap",
                "status": "pass",
                "path": bwrap,
                "detail": "bwrap on PATH (outer sandbox available)",
            }
        )
    else:
        checks.append(
            {
                "id": "bubblewrap",
                "status": "fail",
                "path": None,
                "detail": "bwrap not found on PATH",
            }
        )
        if req in (SANDBOX_MODE_AUTO, SANDBOX_MODE_BWRAP) and not allow_u:
            reasons.append(
                "Agent execution with default/auto or bwrap sandbox will refuse without bubblewrap "
                f"unless {ENV_ALLOW_UNSANDBOXED} is set or --allow-unsandboxed is passed."
            )
            suggested_actions.append(
                "Install bubblewrap (package often named `bubblewrap`) so `bwrap` is on PATH."
            )

    # --- no_new_privs / setpriv (Linux) ---
    if not linux:
        checks.append(
            {
                "id": "no_new_privs",
                "status": "info",
                "path": None,
                "detail": "PR_SET_NO_NEW_PRIVS via setpriv is only applied on Linux; not applicable here.",
            }
        )
    elif skip_nnp:
        checks.append(
            {
                "id": "no_new_privs",
                "status": "warn",
                "path": spriv,
                "detail": f"{ENV_SKIP_NO_NEW_PRIVS} set — no_new_privs will not be requested (weaker posture).",
            }
        )
        reasons.append("no_new_privs skipped by environment (merge_candidate may not require NNP).")
    elif spriv:
        checks.append(
            {
                "id": "no_new_privs",
                "status": "pass",
                "path": spriv,
                "detail": "setpriv on PATH (launcher can apply --no-new-privs on Linux).",
            }
        )
    else:
        checks.append(
            {
                "id": "no_new_privs",
                "status": "warn",
                "path": None,
                "detail": "setpriv not found — agent runs can proceed but trust_degraded_missing_no_new_privs.",
            }
        )
        reasons.append(
            "setpriv missing on Linux: merge_candidate for agent typically requires no_new_privs_applied."
        )
        suggested_actions.append(
            "Install util-linux (or your OS package that provides `setpriv`) so it is on PATH."
        )

    # --- git (branch isolation for nested product repos) ---
    git_p = shutil.which("git")
    if git_p:
        checks.append(
            {
                "id": "git",
                "status": "pass",
                "path": git_p,
                "detail": "git on PATH (required for nested product branch isolation when applicable).",
            }
        )
    else:
        checks.append(
            {
                "id": "git",
                "status": "warn",
                "path": None,
                "detail": "git not on PATH — branch isolation / diffs may fail for nested product workspaces.",
            }
        )
        reasons.append("git missing: nested product git workflows may not run.")
        suggested_actions.append("Install git and ensure it is on PATH.")

    # --- env posture ---
    if allow_u:
        checks.append(
            {
                "id": "allow_unsandboxed_env",
                "status": "warn",
                "detail": f"{ENV_ALLOW_UNSANDBOXED} is set — unsandboxed agent runs allowed (degraded trust).",
            }
        )
        reasons.append("Unsandboxed fallback is allowed by environment (weaker trust by design).")
    if req == SANDBOX_MODE_NONE:
        checks.append(
            {
                "id": "sandbox_mode_env",
                "status": "warn",
                "detail": f"{ENV_BUILDER_SANDBOX}=none — explicit unsandboxed containment (degraded trust).",
            }
        )
        reasons.append("Sandbox mode is explicitly none (unsandboxed agent containment).")

    # --- overall ---
    if req in (SANDBOX_MODE_AUTO, SANDBOX_MODE_BWRAP) and not bwrap and not allow_u:
        overall = STATUS_NOT_READY
    elif (
        allow_u
        or req == SANDBOX_MODE_NONE
        or (linux and not skip_nnp and not spriv)
        or skip_nnp
        or (req in (SANDBOX_MODE_AUTO, SANDBOX_MODE_BWRAP) and not bwrap and allow_u)
        or not git_p
    ):
        overall = STATUS_DEGRADED
    else:
        overall = STATUS_OK

    checks.append(
        {
            "id": "builder_network_mode",
            "status": "info",
            "detail": (
                f"{ENV_BUILDER_NETWORK_MODE} effective from env: {net_m!r} "
                "(invoke --network overrides; see docs/builder-execution-contract.md)."
            ),
        }
    )

    notes = [
        "Readiness is static (PATH/env) only; it does not validate runtime permissions, product layout, or invoke success.",
        "Network modes (default / allow_all / disabled) are recorded on invoke builder_containment; see docs/builder-execution-contract.md.",
    ]

    return {
        "schema": BUILDER_HOST_READINESS_SCHEMA,
        "overall_status": overall,
        "checks": checks,
        "reasons": reasons[:24],
        "suggested_actions": list(dict.fromkeys(suggested_actions))[:16],
        "env_effective": env_effective,
        "notes": notes,
    }


def format_builder_host_readiness_human(payload: dict[str, Any]) -> str:
    """Multi-line summary for terminal use."""
    lines: list[str] = [
        "Builder host readiness",
        f"  overall: {payload.get('overall_status')}",
        "",
        "Checks:",
    ]
    for c in payload.get("checks") or []:
        cid = c.get("id")
        st = c.get("status")
        path = c.get("path")
        detail = c.get("detail")
        extra = f" path={path}" if path else ""
        lines.append(f"  [{st}] {cid}{extra}")
        if detail:
            lines.append(f"       {detail}")
    rs = payload.get("reasons") or []
    if rs:
        lines.extend(["", "Trust / posture notes:"])
        for r in rs[:12]:
            lines.append(f"  — {r}")
    sa = payload.get("suggested_actions") or []
    if sa:
        lines.extend(["", "Suggested actions:"])
        for a in sa[:12]:
            lines.append(f"  — {a}")
    for n in payload.get("notes") or []:
        lines.extend(["", f"Note: {n}"])
    return "\n".join(lines) + "\n"


def exit_code_for_readiness(
    payload: dict[str, Any],
    *,
    strict: bool,
) -> int:
    """CLI exit: not_ready → 1; degraded → 1 if strict else 0."""
    o = payload.get("overall_status")
    if o == STATUS_NOT_READY:
        return 1
    if strict and o == STATUS_DEGRADED:
        return 1
    return 0
