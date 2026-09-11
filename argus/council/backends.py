"""Review backends: deterministic, OpenAI, Cursor placeholder (explicit selection)."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from argus.advisors.llm import extract_json_object
from argus.llm.client import LLMCompletionStatus, complete, is_llm_enabled, llm_client_from_env


class ReviewBackendId(StrEnum):
    DETERMINISTIC = "deterministic"
    OPENAI = "openai"
    CURSOR = "cursor"


def resolve_active_backend(requested: ReviewBackendId) -> ReviewBackendId:
    """
    Never silently upgrade to OpenAI; Cursor path is not wired — falls back to deterministic with marker.

    Returns the backend that will actually run.
    """
    if requested == ReviewBackendId.CURSOR:
        return ReviewBackendId.DETERMINISTIC
    if requested == ReviewBackendId.OPENAI:
        if is_llm_enabled() and llm_client_from_env() is not None:
            return ReviewBackendId.OPENAI
        return ReviewBackendId.DETERMINISTIC
    return ReviewBackendId.DETERMINISTIC


def completion_json(prompt: str, *, temperature: float = 0.2) -> tuple[dict[str, Any] | None, str]:
    """Returns (parsed_json, status) where status is ok|disabled|error."""
    if not is_llm_enabled() or llm_client_from_env() is None:
        return None, "llm_disabled"
    res = complete(prompt, temperature=temperature)
    if res.status != LLMCompletionStatus.OK or not res.text:
        return None, res.status.value
    try:
        return extract_json_object(res.text), res.status.value
    except (ValueError, RuntimeError):
        return None, "parse_error"
