"""Base type for finding rules."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import ClassVar

from argus.findings.candidates import FindingCandidate
from argus.findings.context import RuleContext


class FindingRule(ABC):
    """
    Narrow, testable rule: inspects :class:`RuleContext` and emits candidates.

    Multiple candidates may be merged later by ``(kind, rule_id, issue_key)``.
    """

    rule_id: ClassVar[str]

    @abstractmethod
    def evaluate(self, ctx: RuleContext) -> list[FindingCandidate]:
        """Return zero or more candidates (often one per issue_key)."""
