"""Apply Tier 1 (suggest-only) autonomy for supervised first runs — no execution enablement."""

from __future__ import annotations

from pathlib import Path

from argus.autonomy.models import AutonomyMode
from argus.autonomy.operator_policy import autonomy_config_path, save_autonomy_config
from argus.autonomy.tiers import AutonomyTier


def apply_first_run_safe_profile(repo_root: Path) -> Path:
    """
    Write ``runs/autonomy/autonomy.json`` with **tier 1** and **manual** mode (suggest-only).

    Does **not** set environment variables; operators still use ``argus loop full`` defaults
    (execution stage is static dry-run analysis only). Real subprocess execution requires
    separate opt-in (``ARGUS_EXECUTION_ENABLED``, approval, policy).
    """
    root = repo_root.resolve()
    return save_autonomy_config(
        root,
        AutonomyMode.MANUAL,
        policy_overrides=None,
        tier=int(AutonomyTier.SUGGEST_ONLY),
    )


def describe_first_run_safety(repo_root: Path) -> dict[str, object]:
    """Structured status for CLI / docs (no secrets)."""
    p = autonomy_config_path(repo_root)
    return {
        "autonomy_config_path": str(p.relative_to(repo_root.resolve())),
        "note": (
            "Tier 1 + manual mode: plans and artifacts only; loop full execution stage stays "
            "dry-run analysis unless --no-dry-run is passed (subprocess still gated elsewhere)."
        ),
    }
