"""Conservative defaults — stewardship stance: observe first, mutate with friction."""

from __future__ import annotations

from argus.project_permissions.schema import PHASE1_KEYS, PermissionValue

# Typed defaults for each key (human-editable via products/<id>/argus.policy.yaml).
DEFAULT_PHASE1: dict[str, PermissionValue] = {
    "observe_prod_signals": "confirm",
    "mutate_nonprod": "confirm",
    "mutate_prod": "no",
    "commit_local": "yes",
    "push_remote": "confirm",
    "deploy": "no",
    "change_experiments": "confirm",
    # Headless Builder invoke (`argus builder invoke --execute`); set "no" to refuse by policy.
    "builder_execute": "yes",
}


def merged_defaults(overrides: dict[str, str] | None) -> dict[str, str]:
    """Return full key set with defaults, applying overrides for known keys only."""
    out = {k: DEFAULT_PHASE1[k] for k in PHASE1_KEYS}
    if not overrides:
        return out
    for k, v in overrides.items():
        if k in out and isinstance(v, str):
            out[k] = v.strip().lower()
    return out
