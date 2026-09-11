"""Finding rules registry."""

from argus.findings.rules.base import FindingRule
from argus.findings.rules.builtins import (
    CostRiskRule,
    DeprecationCandidateRule,
    GrowthOpportunityRule,
    InactivityRule,
    LaunchCandidateRule,
    LowSignalRule,
    QualityGapRule,
    default_rules,
)
from argus.findings.rules.temporal import TEMPORAL_FINDING_KINDS, TemporalSignalsRule

__all__ = [
    "CostRiskRule",
    "DeprecationCandidateRule",
    "FindingRule",
    "GrowthOpportunityRule",
    "InactivityRule",
    "LaunchCandidateRule",
    "LowSignalRule",
    "QualityGapRule",
    "TEMPORAL_FINDING_KINDS",
    "TemporalSignalsRule",
    "default_rules",
]
