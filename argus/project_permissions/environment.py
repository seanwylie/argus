"""
Declarative environment support hints for Phase 1 keys (no live cloud probes).

Uses :func:`argus.containment.policy.build_capability_map` where it aligns; otherwise
repo-local heuristics (signals paths, git metadata, experiments dirs).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from argus.containment.policy import build_capability_map
from argus.project_permissions.schema import Phase1PermissionKey


def _cap_status(caps: dict[str, Any], key: str) -> tuple[bool, str]:
    row = caps.get(key)
    if not isinstance(row, dict):
        return False, f"capability {key!r} unknown"
    st = str(row.get("status") or "")
    reason = str(row.get("reason") or "")
    ok = st == "available"
    return ok, reason or st


def environment_supports_phase1(
    repo_root: Path,
    product_id: str,
    key: Phase1PermissionKey,
) -> tuple[bool, str]:
    """
    Return (supported, reason). When False, policy may still be yes/confirm — report mismatch.

    This is intentionally shallow: it reflects what Argus can **see** in-repo, not IAM proofs.
    """
    root = repo_root.resolve()
    pid = str(product_id or "").strip()
    cmap = build_capability_map(root)
    caps = cmap.get("capabilities") if isinstance(cmap.get("capabilities"), dict) else {}

    if key == "observe_prod_signals":
        sig = root / "runs" / "signals" / "latest" / f"{pid}.json"
        metrics = root / "products" / pid / "metrics"
        if sig.is_file():
            return True, f"found {sig.relative_to(root)}"
        if metrics.is_dir() and any(metrics.iterdir()):
            return True, f"product metrics dir has content: {metrics.relative_to(root)}"
        return False, "no runs/signals/latest/<product>.json and no metrics content — add signals or relax expectations"

    if key == "mutate_nonprod":
        ok, r = _cap_status(caps, "aws_write_staging")
        return ok, r

    if key == "mutate_prod":
        ok, r = _cap_status(caps, "aws_write_prod")
        return ok, r

    if key == "commit_local":
        if (root / ".git").exists():
            return True, "git metadata present"
        return False, "no .git in repo root — not a git working tree"

    if key == "push_remote":
        ok, r = _cap_status(caps, "git_push")
        return ok, r

    if key == "deploy":
        ok, r = _cap_status(caps, "deploy")
        return ok, r

    if key == "change_experiments":
        exp_root = root / "runs" / "experiments"
        if exp_root.is_dir():
            return True, f"found {exp_root.relative_to(root)}"
        seed = root / "products" / pid / "experiments" if pid else None
        if seed and seed.is_dir():
            return True, f"found {seed.relative_to(root)}"
        return False, "no runs/experiments/ or products/<id>/experiments/ — experiments not established"

    if key == "builder_execute":
        # Local subprocess + containment; no separate cloud capability map — policy is the gate.
        return True, "builder invoke execution is local; phase 1 policy governs allowance"

    return False, f"unhandled key {key!r}"


def build_phase1_environment_alignment(
    repo_root: Path,
    product_id: str,
) -> dict[str, Any]:
    """Per-key support flag + reason for operator summaries and readiness."""
    from argus.project_permissions.schema import PHASE1_KEYS

    rows: dict[str, Any] = {}
    for k in PHASE1_KEYS:
        ok, reason = environment_supports_phase1(repo_root, product_id, k)  # type: ignore[arg-type]
        rows[k] = {"environment_supports": ok, "reason": reason}
    return rows
