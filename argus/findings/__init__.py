"""Finding engine: signals → consolidated :class:`~argus.core.models.finding.Finding`."""

from argus.findings.engine import generate_findings
from argus.findings.persistence import (
    FindingsBundle,
    load_all_latest_summaries,
    load_latest_findings,
    save_findings_bundle,
)

__all__ = [
    "FindingsBundle",
    "generate_findings",
    "load_all_latest_summaries",
    "load_latest_findings",
    "save_findings_bundle",
]
