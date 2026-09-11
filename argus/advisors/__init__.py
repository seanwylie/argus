"""Multi-advisor board (structured prompts, optional OpenAI-compatible LLM, consensus)."""

from argus.advisors.consensus import build_consensus, run_consensus, run_consultation
from argus.advisors.models import Advisor, AdvisorArchetype, AdvisorResponse, ConsensusResult
from argus.advisors.registry import global_advisors, resolve_advisors
from argus.advisors.runner import run_advisors

__all__ = [
    "Advisor",
    "AdvisorArchetype",
    "AdvisorResponse",
    "ConsensusResult",
    "build_consensus",
    "global_advisors",
    "resolve_advisors",
    "run_advisors",
    "run_consensus",
    "run_consultation",
]
