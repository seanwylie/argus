"""Pluggable OpenAI-compatible HTTP client (stdlib only; optional dependency on network + API key)."""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Protocol

DEFAULT_BASE_URL = "https://api.openai.com/v1"
DEFAULT_MODEL = "gpt-4o-mini"
ENV_API_KEY = "ARGUS_OPENAI_API_KEY"
ENV_BASE_URL = "ARGUS_OPENAI_BASE_URL"
ENV_MODEL = "ARGUS_ADVISORS_MODEL"
ENV_DISABLE = "ARGUS_ADVISORS_DISABLE_LLM"


def _llm_globally_enabled() -> bool:
    """Respect ``ARGUS_LLM_ENABLED`` (canonical) and legacy disable flag."""
    try:
        from argus.llm.client import is_llm_enabled
    except ImportError:
        is_llm_enabled = None  # type: ignore[assignment]
    if is_llm_enabled is not None and not is_llm_enabled():
        return False
    if os.environ.get(ENV_DISABLE, "").strip().lower() in ("1", "true", "yes"):
        return False
    return True


@dataclass
class LLMConfig:
    api_key: str
    base_url: str
    model: str


def llm_config_from_env() -> LLMConfig | None:
    """Return config when API key is set and LLM not explicitly disabled."""
    if not _llm_globally_enabled():
        return None
    key = os.environ.get(ENV_API_KEY, "").strip()
    if not key:
        return None
    base = os.environ.get(ENV_BASE_URL, DEFAULT_BASE_URL).strip().rstrip("/")
    model = os.environ.get(ENV_MODEL, DEFAULT_MODEL).strip() or DEFAULT_MODEL
    return LLMConfig(api_key=key, base_url=base, model=model)


class LLMProvider(Protocol):
    """Pluggable completion provider (OpenAI-compatible)."""

    def complete_json_chat(
        self,
        messages: list[dict[str, str]],
        *,
        model: str | None = None,
        temperature: float = 0.35,
        timeout_s: float = 120.0,
    ) -> str:
        """Return raw assistant text (expected JSON)."""


class OpenAICompatProvider:
    """Minimal OpenAI-compatible `/v1/chat/completions` client."""

    def __init__(self, config: LLMConfig) -> None:
        self._config = config

    def complete_json_chat(
        self,
        messages: list[dict[str, str]],
        *,
        model: str | None = None,
        temperature: float = 0.35,
        timeout_s: float = 120.0,
    ) -> str:
        url = f"{self._config.base_url}/chat/completions"
        payload: dict[str, Any] = {
            "model": model or self._config.model,
            "messages": messages,
            "temperature": temperature,
        }
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self._config.api_key}",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout_s) as resp:
                raw = resp.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"LLM HTTP {e.code}: {err_body[:2000]}") from e
        except urllib.error.URLError as e:
            raise RuntimeError(f"LLM connection error: {e}") from e

        data = json.loads(raw)
        choices = data.get("choices") or []
        if not choices or not isinstance(choices[0], dict):
            raise RuntimeError("LLM response missing choices")
        msg = choices[0].get("message") or {}
        content = msg.get("content")
        if not isinstance(content, str) or not content.strip():
            raise RuntimeError("LLM response missing message.content")
        return content.strip()


_JSON_FENCE = re.compile(r"```(?:json)?\s*([\s\S]*?)```", re.IGNORECASE)


def extract_json_object(text: str) -> dict[str, Any]:
    """Parse JSON from model output; strip fenced blocks if present."""
    t = text.strip()
    m = _JSON_FENCE.search(t)
    if m:
        t = m.group(1).strip()
    try:
        out = json.loads(t)
    except json.JSONDecodeError:
        # try first { ... } slice
        start = t.find("{")
        end = t.rfind("}")
        if start >= 0 and end > start:
            out = json.loads(t[start : end + 1])
        else:
            raise
    if not isinstance(out, dict):
        raise ValueError("parsed JSON is not an object")
    return out


def parse_advisor_json(data: dict[str, Any]) -> dict[str, Any]:
    """Normalize keys for :class:`AdvisorResponse` construction."""
    rec = str(data.get("recommendation", "")).strip()
    rat = str(data.get("rationale", "")).strip()
    risks_raw = data.get("risks")
    risks: list[str] = []
    if isinstance(risks_raw, list):
        risks = [str(x).strip() for x in risks_raw if str(x).strip()]
    elif isinstance(risks_raw, str) and risks_raw.strip():
        risks = [risks_raw.strip()]
    conf = data.get("confidence")
    try:
        confidence = float(conf) if conf is not None else None
    except (TypeError, ValueError):
        confidence = None
    if confidence is not None:
        confidence = max(0.0, min(1.0, confidence))
    stance = data.get("stance")
    try:
        stance_f = float(stance) if stance is not None else None
    except (TypeError, ValueError):
        stance_f = None
    if stance_f is not None:
        stance_f = max(0.0, min(1.0, stance_f))
    ta_raw = data.get("temporal_assumptions")
    temporal_assumptions: list[str] = []
    if isinstance(ta_raw, list):
        temporal_assumptions = [str(x).strip() for x in ta_raw if str(x).strip()]
    fr = data.get("freshness_risk")
    freshness_risk = str(fr).strip().lower() if fr is not None else None
    if freshness_risk and freshness_risk not in ("low", "moderate", "high"):
        freshness_risk = None
    adj = data.get("confidence_adjustment")
    try:
        confidence_adjustment = float(adj) if adj is not None else None
    except (TypeError, ValueError):
        confidence_adjustment = None
    if confidence_adjustment is not None:
        confidence_adjustment = max(-1.0, min(0.0, confidence_adjustment))
    return {
        "recommendation": rec or "(no recommendation)",
        "rationale": rat or "(no rationale)",
        "risks": risks,
        "confidence": confidence,
        "stance": stance_f,
        "temporal_assumptions": temporal_assumptions,
        "freshness_risk": freshness_risk,
        "confidence_adjustment": confidence_adjustment,
    }
