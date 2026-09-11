"""
Provider cleanup seam for shutdown / archive (AWS, DNS, SaaS billing).

Default is a **structured no-op**: safe, inspectable, and replaceable when automation
is approved. Destructive external calls must remain behind capability requests + policy.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def plan_provider_cleanup(
    product_id: str,
    *,
    archive_path: Path | None = None,
) -> dict[str, Any]:
    """
    Describe intended provider teardown steps without executing them.

    Wire real implementations (e.g. boto3, provider SDKs) only after explicit approval;
    keep execution contracts in ``argus.actions`` / autonomy policy.
    """
    return {
        "executed": False,
        "stub": True,
        "product_id": product_id,
        "archive_path": str(archive_path) if archive_path else None,
        "suggested_providers": ["aws", "dns", "billing_saas", "analytics_project"],
        "notes": (
            "No cloud resources torn down. Replace with approved hooks; "
            "see docs/execution.md and RESOURCE_CLEANUP_STUB_NOTES in autonomy/shutdown.py."
        ),
    }
