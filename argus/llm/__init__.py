"""Optional OpenAI augmentation: idea expansion and advisor council (advisory only; gated by env)."""

from __future__ import annotations

from argus.llm.client import LLMCompletionStatus, complete, is_llm_enabled, llm_client_from_env

__all__ = [
    "LLMCompletionStatus",
    "complete",
    "is_llm_enabled",
    "llm_client_from_env",
]
