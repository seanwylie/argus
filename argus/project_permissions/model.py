"""Loaded Phase 1 project permission policy."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from argus.project_permissions.schema import PHASE1_KEYS, PROJECT_PERMISSION_POLICY_SCHEMA


@dataclass
class ProjectPermissionPolicy:
    """Effective yes/no/confirm map for one product node."""

    product_id: str
    policy_path: Path | None
    values: dict[str, str] = field(default_factory=dict)
    load_warnings: list[str] = field(default_factory=list)

    def get(self, key: str) -> str:
        return str(self.values.get(key, "no")).strip().lower()

    def to_summary_dict(self) -> dict[str, Any]:
        return {
            "schema": PROJECT_PERMISSION_POLICY_SCHEMA,
            "product_id": self.product_id,
            "policy_path": str(self.policy_path) if self.policy_path else None,
            "permissions": {k: self.get(k) for k in PHASE1_KEYS},
            "load_warnings": list(self.load_warnings),
        }
