"""Write prompts and raw responses under ``runs/advisors/consultations/``."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from argus.core.serialize import dumps_json

# Strip common secret patterns from logged JSON (defense-in-depth; prompts should not contain keys).
_REDACT_SK = re.compile(r"\bsk-[a-zA-Z0-9]{8,}\b")
_REDACT_BEARER = re.compile(r"(?i)\b(bearer\s+)([a-z0-9._\-/+]{10,})\b")


def redact_secrets_for_audit(obj: Any) -> Any:
    """Recursively redact API-key-like strings from structures written to disk."""
    if isinstance(obj, str):
        s = _REDACT_SK.sub("[REDACTED]", obj)
        s = _REDACT_BEARER.sub(r"\1[REDACTED]", s)
        return s
    if isinstance(obj, list):
        return [redact_secrets_for_audit(x) for x in obj]
    if isinstance(obj, dict):
        return {str(k): redact_secrets_for_audit(v) for k, v in obj.items()}
    return obj


def _safe_segment(product_id: str) -> str:
    return re.sub(r"[^a-zA-Z0-9._-]+", "_", product_id).strip("_") or "product"


def consultation_dir(repo_root: Path, product_id: str) -> Path:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    seg = _safe_segment(product_id)
    d = repo_root.resolve() / "runs" / "advisors" / "consultations" / f"{ts}_{seg}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def write_consultation_artifacts(
    repo_root: Path,
    product_id: str,
    *,
    context_dump: dict[str, Any],
    per_advisor: list[dict[str, Any]],
    run_payload: dict[str, Any],
    consensus_payload: dict[str, Any] | None = None,
) -> Path:
    """
    Persist context, per-advisor prompts/responses, and merged run/consensus JSON.

    ``per_advisor`` entries should include: advisor_id, mode (llm|stub), prompt_messages (optional),
    raw_response (optional), parsed (optional), error (optional).
    """
    base = consultation_dir(repo_root, product_id)
    (base / "context.json").write_text(dumps_json(redact_secrets_for_audit(context_dump)), encoding="utf-8")
    (base / "per_advisor.json").write_text(
        dumps_json(redact_secrets_for_audit({"advisors": per_advisor})),
        encoding="utf-8",
    )
    (base / "run.json").write_text(dumps_json(redact_secrets_for_audit(run_payload)), encoding="utf-8")
    if consensus_payload is not None:
        (base / "consensus.json").write_text(
            dumps_json(redact_secrets_for_audit(consensus_payload)),
            encoding="utf-8",
        )
    (base / "README.txt").write_text(
        "Local Argus advisor consultation log. Prompts and responses for audit.\n",
        encoding="utf-8",
    )
    return base


def write_consensus_sidecar(log_dir: Path, consensus_payload: dict[str, Any]) -> Path:
    """Write or overwrite ``consensus.json`` next to an existing consultation run."""
    p = log_dir / "consensus.json"
    p.write_text(dumps_json(consensus_payload), encoding="utf-8")
    return p
