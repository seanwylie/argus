"""Ingest Cursor orchestration review JSON (sidecar; does not modify deterministic state JSON)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from argus.core.serialize import dumps_json
from argus.orchestrator.cursor_review import (
    ORCHESTRATION_REVIEW_BUNDLE_SCHEMA,
    fingerprint_orchestration_state,
    validate_orchestration_cursor_review_v1,
)
from argus.orchestrator.eligibility import evaluate_product_orchestration
from argus.orchestrator.state_pass import load_orchestration_state, orchestration_latest_path


def orchestration_review_bundle_path(repo_root: Path, product_id: str) -> Path:
    return repo_root.resolve() / "runs" / "orchestration" / "review" / f"{product_id}.json"


def load_orchestration_review_bundle(repo_root: Path, product_id: str) -> dict[str, Any] | None:
    p = orchestration_review_bundle_path(repo_root, product_id)
    if not p.is_file():
        return None
    data = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("orchestration review bundle must be a JSON object")
    return data


def load_orchestration_state_for_review(repo_root: Path, product_id: str) -> dict[str, Any]:
    """Prefer on-disk latest state; else evaluate fresh."""
    root = repo_root.resolve()
    cached = load_orchestration_state(root, product_id)
    if cached is not None:
        return cached
    return evaluate_product_orchestration(root, product_id)


def ingest_orchestration_cursor_review(
    repo_root: Path,
    product_id: str,
    raw: dict[str, Any],
    *,
    overwrite: bool = True,
) -> dict[str, Any]:
    """
    Validate ``argus.orchestration_cursor_review.v1`` and write ``runs/orchestration/review/<id>.json``.

    Preserves ``ingest_history`` on reruns (prior fingerprint + timestamp). Never modifies
    ``runs/orchestration/latest/<id>.json`` except via normal ``orchestration state`` / ``advance``.
    """
    root = repo_root.resolve()
    state = load_orchestration_state_for_review(root, product_id)
    fp = fingerprint_orchestration_state(state)

    validated = validate_orchestration_cursor_review_v1(raw, expected_product_id=product_id)

    now = datetime.now(timezone.utc).isoformat()
    prior = load_orchestration_review_bundle(root, product_id)
    history: list[dict[str, Any]] = []
    if isinstance(prior, dict):
        prev_hist = prior.get("ingest_history")
        if isinstance(prev_hist, list):
            history = [dict(x) for x in prev_hist if isinstance(x, dict)][-16:]

    if not overwrite and isinstance(prior, dict) and prior.get("orchestration_review"):
        raise ValueError("orchestration_review already present; use overwrite=True to replace")

    if isinstance(prior, dict) and prior.get("orchestration_review"):
        prev_fp = prior.get("deterministic_orchestration_fingerprint")
        prev_ing = prior.get("ingested_at_utc")
        if prev_fp and prev_ing:
            history.append(
                {
                    "deterministic_orchestration_fingerprint": prev_fp,
                    "ingested_at_utc": prev_ing,
                    "superseded": True,
                }
            )

    validated = dict(validated)
    validated["ingested_at_utc"] = now

    rel_state = orchestration_latest_path(root, product_id)
    out: dict[str, Any] = {
        "schema": ORCHESTRATION_REVIEW_BUNDLE_SCHEMA,
        "product_id": product_id,
        "deterministic_sources": {
            "state_path": str(rel_state.relative_to(root)),
            "schema": str(state.get("schema", "argus.orchestration_state.v1")),
        },
        "deterministic_orchestration_fingerprint": fp,
        "deterministic_evaluated_at_utc": str(state.get("evaluated_at_utc", "")),
        "orchestration_review": validated,
        "ingested_at_utc": now,
        "provenance": {
            "layer": "cursor_interpretation",
            "deterministic_truth": "runs/orchestration/latest/<product_id>.json (unchanged by this ingest)",
        },
        "ingest_history": history,
    }

    p = orchestration_review_bundle_path(root, product_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(dumps_json(out) + "\n", encoding="utf-8")
    return out


def load_ingest_json(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("JSON root must be an object")
    return data


def orchestration_review_operator_summary(repo_root: Path, product_id: str) -> dict[str, Any]:
    """Bounded facts for CLI / JSON: presence, path, fingerprint alignment."""
    root = repo_root.resolve()
    p = orchestration_review_bundle_path(root, product_id)
    out: dict[str, Any] = {
        "orchestration_review_present": p.is_file(),
        "orchestration_review_path_repo_relative": None,
        "orchestration_review_ingested_at_utc": None,
        "orchestration_review_matches_state_fingerprint": None,
        "orchestration_review_summary_preview": None,
        "orchestration_review_confidence": None,
    }
    if not p.is_file():
        return out
    out["orchestration_review_path_repo_relative"] = str(p.relative_to(root))
    rb = load_orchestration_review_bundle(root, product_id)
    if not isinstance(rb, dict):
        return out
    out["orchestration_review_ingested_at_utc"] = rb.get("ingested_at_utc")
    stored_fp = rb.get("deterministic_orchestration_fingerprint")
    try:
        st = load_orchestration_state_for_review(root, product_id)
        cur_fp = fingerprint_orchestration_state(st)
        out["orchestration_review_matches_state_fingerprint"] = stored_fp == cur_fp
    except (OSError, ValueError):
        out["orchestration_review_matches_state_fingerprint"] = None
    sr = rb.get("orchestration_review")
    if isinstance(sr, dict):
        sl = sr.get("summary_lines")
        if isinstance(sl, list):
            out["orchestration_review_summary_preview"] = [str(x) for x in sl[:5] if str(x).strip()]
        out["orchestration_review_confidence"] = sr.get("confidence")
    return out
