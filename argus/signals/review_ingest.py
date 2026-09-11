"""Ingest Cursor signal review JSON into a sidecar artifact (does not modify deterministic bundles)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from argus.core.serialize import dumps_json
from argus.signals.cursor_review import (
    SIGNAL_REVIEW_BUNDLE_SCHEMA,
    fingerprint_deterministic_signal_bundle,
    validate_signal_cursor_review_v1,
)
from argus.signals.persistence import latest_path


def signal_review_bundle_path(repo_root: Path, product_id: str) -> Path:
    """Sidecar path: ``runs/signals/review/<product_id>.json``."""
    return repo_root.resolve() / "runs" / "signals" / "review" / f"{product_id}.json"


def load_deterministic_bundle_dict(repo_root: Path, product_id: str) -> dict[str, Any]:
    """Load raw JSON from ``runs/signals/latest/<product_id>.json``."""
    lp = latest_path(repo_root.resolve(), product_id)
    if not lp.is_file():
        raise ValueError(
            f"No deterministic signal bundle for {product_id!r}; run `argus signals collect {product_id}` first",
        )
    data = json.loads(lp.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("deterministic bundle must be a JSON object")
    return data


def load_signal_review_bundle(repo_root: Path, product_id: str) -> dict[str, Any] | None:
    p = signal_review_bundle_path(repo_root, product_id)
    if not p.is_file():
        return None
    data = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("signal review bundle must be a JSON object")
    return data


def ingest_signal_cursor_review(
    repo_root: Path,
    product_id: str,
    raw: dict[str, Any],
    *,
    overwrite: bool = True,
) -> dict[str, Any]:
    """
    Validate ``argus.signal_cursor_review.v1`` and write ``runs/signals/review/<product_id>.json``.

    Preserves a short ``ingest_history`` on reruns (append prior fingerprint + timestamp).
    Never modifies ``runs/signals/latest``. A later ``save_collection`` / ``signals collect`` also
    does **not** delete or rewrite this file — only ``cursor-ingest`` updates it (or use overwrite).
    """
    root = repo_root.resolve()
    det = load_deterministic_bundle_dict(root, product_id)
    fp = fingerprint_deterministic_signal_bundle(det)

    validated = validate_signal_cursor_review_v1(raw, expected_product_id=product_id)

    now = datetime.now(timezone.utc).isoformat()
    prior = load_signal_review_bundle(root, product_id)
    history: list[dict[str, Any]] = []
    if isinstance(prior, dict):
        prev_hist = prior.get("ingest_history")
        if isinstance(prev_hist, list):
            history = [dict(x) for x in prev_hist if isinstance(x, dict)][-16:]

    if not overwrite and isinstance(prior, dict) and prior.get("signal_review"):
        raise ValueError("signal_review already present; use overwrite=True to replace")

    if isinstance(prior, dict) and prior.get("signal_review"):
        prev_fp = prior.get("deterministic_signals_fingerprint")
        prev_ing = prior.get("ingested_at_utc")
        if prev_fp and prev_ing:
            history.append(
                {
                    "deterministic_signals_fingerprint": prev_fp,
                    "ingested_at_utc": prev_ing,
                    "superseded": True,
                }
            )

    validated = dict(validated)
    validated["ingested_at_utc"] = now

    out: dict[str, Any] = {
        "schema": SIGNAL_REVIEW_BUNDLE_SCHEMA,
        "product_id": product_id,
        "deterministic_sources": {
            "bundle_path": str(latest_path(root, product_id).relative_to(root)),
            "schema": str(det.get("schema", "argus.signal_collection.v1")),
        },
        "deterministic_signals_fingerprint": fp,
        "deterministic_collected_at_utc": str(det.get("collected_at_utc", "")),
        "deterministic_record_count": (
            len(recs) if isinstance((recs := det.get("records")), list) else 0
        ),
        "signal_review": validated,
        "ingested_at_utc": now,
        "provenance": {
            "layer": "cursor_interpretation",
            "deterministic_truth": "runs/signals/latest/<product_id>.json (unchanged by this ingest)",
        },
        "ingest_history": history,
    }

    p = signal_review_bundle_path(root, product_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(dumps_json(out) + "\n", encoding="utf-8")
    return out


def load_ingest_json(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("JSON root must be an object")
    return data


def signal_review_operator_summary(repo_root: Path, product_id: str) -> dict[str, Any]:
    """
    Bounded facts for CLI / ``signals show`` JSON: presence, path, provenance, fingerprint alignment.

    ``signal_review_matches_deterministic_fingerprint`` is ``True`` when the review was ingested
    against the same deterministic bundle fingerprint as ``runs/signals/latest`` now; ``False`` when
    collect ran again (review sidecar is **not** deleted — re-run ``cursor-ingest`` to refresh).
    """
    root = repo_root.resolve()
    p = signal_review_bundle_path(root, product_id)
    out: dict[str, Any] = {
        "signal_review_present": p.is_file(),
        "signal_review_path_repo_relative": None,
        "signal_review_ingested_at_utc": None,
        "signal_review_matches_deterministic_fingerprint": None,
        "signal_review_summary_preview": None,
        "signal_review_confidence": None,
    }
    if not p.is_file():
        return out
    out["signal_review_path_repo_relative"] = str(p.relative_to(root))
    rb = load_signal_review_bundle(root, product_id)
    if not isinstance(rb, dict):
        return out
    out["signal_review_ingested_at_utc"] = rb.get("ingested_at_utc")
    stored_fp = rb.get("deterministic_signals_fingerprint")
    try:
        det = load_deterministic_bundle_dict(root, product_id)
        cur_fp = fingerprint_deterministic_signal_bundle(det)
        out["signal_review_matches_deterministic_fingerprint"] = stored_fp == cur_fp
    except ValueError:
        out["signal_review_matches_deterministic_fingerprint"] = None
    sr = rb.get("signal_review")
    if isinstance(sr, dict):
        sl = sr.get("summary_lines")
        if isinstance(sl, list):
            out["signal_review_summary_preview"] = [str(x) for x in sl[:5] if str(x).strip()]
        out["signal_review_confidence"] = sr.get("confidence")
    return out


def bounded_signal_review_for_json(bundle: dict[str, Any] | None) -> dict[str, Any] | None:
    """Smaller slice for ``signals show --json`` (not full nested payloads)."""
    if not isinstance(bundle, dict):
        return None
    sr = bundle.get("signal_review")
    if not isinstance(sr, dict):
        return None
    return {
        "schema": bundle.get("schema"),
        "ingested_at_utc": bundle.get("ingested_at_utc"),
        "deterministic_signals_fingerprint": bundle.get("deterministic_signals_fingerprint"),
        "deterministic_collected_at_utc": bundle.get("deterministic_collected_at_utc"),
        "summary_lines": (sr.get("summary_lines") or [])[:12],
        "confidence": sr.get("confidence"),
        "provenance": sr.get("provenance"),
        "ingest_history_len": len(bundle.get("ingest_history") or [])
        if isinstance(bundle.get("ingest_history"), list)
        else 0,
    }
