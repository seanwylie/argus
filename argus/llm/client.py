"""Minimal OpenAI chat completion wrapper — gated by ``ARGUS_LLM_ENABLED``; never logs secrets."""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

logger = logging.getLogger(__name__)

ENV_API_KEY = "ARGUS_OPENAI_API_KEY"
ENV_LLM_ENABLED = "ARGUS_LLM_ENABLED"
ENV_BASE_URL = "ARGUS_OPENAI_BASE_URL"
ENV_MODEL = "ARGUS_OPENAI_MODEL"
DEFAULT_BASE_URL = "https://api.openai.com/v1"
DEFAULT_MODEL = "gpt-4o-mini"
DEFAULT_TEMPERATURE = 0.3


class LLMCompletionStatus(StrEnum):
    OK = "ok"
    DISABLED = "disabled"
    NO_API_KEY = "no_api_key"
    ERROR = "error"


@dataclass(frozen=True)
class LLMCompletionResult:
    """Structured outcome so callers avoid treating disabled and empty the same."""

    status: LLMCompletionStatus
    text: str | None = None
    """Assistant text when ``status`` is ``ok``."""

    def ok_text(self) -> str | None:
        return self.text if self.status == LLMCompletionStatus.OK else None


def is_llm_enabled() -> bool:
    """LLM features are off unless ``ARGUS_LLM_ENABLED`` is truthy (1/true/yes/on)."""
    v = os.environ.get(ENV_LLM_ENABLED, "").strip().lower()
    return v in ("1", "true", "yes", "on")


def _api_key() -> str | None:
    k = os.environ.get(ENV_API_KEY, "").strip()
    return k if k else None


@dataclass
class LLMClient:
    """OpenAI-compatible ``/v1/chat/completions`` client (stdlib only)."""

    api_key: str
    base_url: str = DEFAULT_BASE_URL
    model: str = DEFAULT_MODEL
    default_temperature: float = DEFAULT_TEMPERATURE

    def complete(
        self,
        prompt: str,
        *,
        model: str | None = None,
        temperature: float | None = None,
        timeout_s: float = 120.0,
    ) -> LLMCompletionResult:
        if not prompt.strip():
            return LLMCompletionResult(LLMCompletionStatus.ERROR, None)
        url = f"{self.base_url.rstrip('/')}/chat/completions"
        payload: dict[str, Any] = {
            "model": model or self.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": self.default_temperature if temperature is None else float(temperature),
        }
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout_s) as resp:
                raw = resp.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="replace")
            logger.warning("LLM HTTP error (status=%s); falling back.", e.code)
            logger.debug("LLM error body prefix: %s", err_body[:500])
            return LLMCompletionResult(LLMCompletionStatus.ERROR, None)
        except urllib.error.URLError as e:
            logger.warning("LLM connection error; falling back: %s", e)
            return LLMCompletionResult(LLMCompletionStatus.ERROR, None)
        except OSError as e:
            logger.warning("LLM I/O error; falling back: %s", e)
            return LLMCompletionResult(LLMCompletionStatus.ERROR, None)

        try:
            data = json.loads(raw)
            choices = data.get("choices") or []
            if not choices or not isinstance(choices[0], dict):
                logger.warning("LLM response missing choices; falling back.")
                return LLMCompletionResult(LLMCompletionStatus.ERROR, None)
            msg = choices[0].get("message") or {}
            content = msg.get("content")
            if not isinstance(content, str) or not content.strip():
                logger.warning("LLM response missing message.content; falling back.")
                return LLMCompletionResult(LLMCompletionStatus.ERROR, None)
            return LLMCompletionResult(LLMCompletionStatus.OK, content.strip())
        except json.JSONDecodeError:
            logger.warning("LLM response not valid JSON; falling back.")
            return LLMCompletionResult(LLMCompletionStatus.ERROR, None)


def llm_client_from_env() -> LLMClient | None:
    """
    Build a client when LLM is enabled and an API key is present.

    Does not read or persist the key beyond the in-memory client instance.
    """
    if not is_llm_enabled():
        return None
    key = _api_key()
    if not key:
        logger.warning("ARGUS_LLM_ENABLED is set but ARGUS_OPENAI_API_KEY is missing; LLM disabled.")
        return None
    base = os.environ.get(ENV_BASE_URL, DEFAULT_BASE_URL).strip().rstrip("/") or DEFAULT_BASE_URL
    model = os.environ.get(ENV_MODEL, DEFAULT_MODEL).strip() or DEFAULT_MODEL
    return LLMClient(api_key=key, base_url=base, model=model, default_temperature=DEFAULT_TEMPERATURE)


def complete(
    prompt: str,
    *,
    model: str | None = None,
    temperature: float | None = None,
) -> LLMCompletionResult:
    """
    High-level completion: respects ``ARGUS_LLM_ENABLED`` and API key presence.

    Returns ``DISABLED`` / ``NO_API_KEY`` without calling the network.
    """
    if not is_llm_enabled():
        return LLMCompletionResult(LLMCompletionStatus.DISABLED, None)
    if not _api_key():
        return LLMCompletionResult(LLMCompletionStatus.NO_API_KEY, None)
    client = llm_client_from_env()
    if client is None:
        return LLMCompletionResult(LLMCompletionStatus.NO_API_KEY, None)
    return client.complete(prompt, model=model, temperature=temperature)
