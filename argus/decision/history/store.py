"""Load decision memory from ``runs/decisions/generations/*.json`` bundles."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from argus.decision.history.models import AlternativeCandidate, DecisionMemoryEntry
from argus.decision.persistence import generations_dir

_GEN_FILE = re.compile(r"^(\d{8}T\d{6}Z)_(.+)\.json$")


def _parse_bundle(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    if data.get("schema") != "argus.decisions_bundle.v1":
        return None
    return data


def bundle_to_entry(
    data: dict[str, Any],
    *,
    source_path: str,
    previous_top_summary: str | None,
) -> DecisionMemoryEntry:
    """Map a decisions bundle dict to :class:`DecisionMemoryEntry`."""
    pid = str(data.get("product_id", ""))
    gen_at = str(data.get("generated_at_utc", ""))
    cands = data.get("candidates") or []
    top_summary = ""
    top_intent = ""
    prio: float | None = None
    conf: float | None = None
    rationale = ""
    alts: list[AlternativeCandidate] = []
    if isinstance(cands, list) and cands:
        c0 = cands[0]
        if isinstance(c0, dict):
            top_summary = str(c0.get("summary", ""))
            md = c0.get("metadata") or {}
            if isinstance(md, dict):
                top_intent = str(md.get("intent", "") or "")
            ps = c0.get("priority_score")
            prio = float(ps) if ps is not None else None
            cf = c0.get("confidence")
            conf = float(cf) if cf is not None else None
            rationale = str(c0.get("rationale", "") or "")[:2000]
        for c in cands[1:9]:
            if not isinstance(c, dict):
                continue
            md = c.get("metadata") or {}
            intent = str(md.get("intent", "") or "") if isinstance(md, dict) else ""
            ps = c.get("priority_score")
            cf = c.get("confidence")
            alts.append(
                AlternativeCandidate(
                    summary=str(c.get("summary", ""))[:500],
                    intent=intent,
                    priority_score=float(ps) if ps is not None else None,
                    confidence=float(cf) if cf is not None else None,
                )
            )

    lc = data.get("lifecycle") or {}
    stage = str(lc.get("stage", "") or "")
    kill = bool(lc.get("kill_candidate", False))
    reason = lc.get("reasoning") if isinstance(lc.get("reasoning"), dict) else {}
    rationale_summary = rationale
    if not rationale_summary and isinstance(reason, dict):
        # take first reasoning value as summary hint
        vals = [str(v) for v in reason.values() if v]
        if vals:
            rationale_summary = vals[0][:500]

    if previous_top_summary is None:
        cmp = "first"
    elif previous_top_summary == top_summary:
        cmp = "unchanged"
    else:
        cmp = "changed"

    return DecisionMemoryEntry(
        source_path=source_path,
        generated_at_utc=gen_at,
        product_id=pid,
        top_recommended_action=top_summary,
        top_intent=top_intent,
        priority_score=prio,
        confidence=conf,
        alternatives=alts,
        rationale_summary=rationale_summary,
        compared_to_previous=cmp,
        lifecycle_stage=stage,
        kill_candidate=kill,
    )


def iter_decision_bundle_paths(repo_root: Path) -> list[Path]:
    """All decision bundle files (excludes portfolio reports), newest filename first."""
    base = generations_dir(repo_root.resolve())
    if not base.is_dir():
        return []
    paths: list[Path] = []
    for p in base.glob("*.json"):
        if p.name.endswith("_portfolio.json"):
            continue
        m = _GEN_FILE.match(p.name)
        if not m:
            continue
        paths.append(p)
    return sorted(paths, key=lambda x: x.name, reverse=True)


def load_product_decision_history(
    repo_root: Path,
    product_id: str,
) -> list[DecisionMemoryEntry]:
    """
    Load chronological decision memory for ``product_id`` from generation files.

    Oldest-first order for analysis.
    """
    root = repo_root.resolve()
    rel_root = root
    entries: list[DecisionMemoryEntry] = []
    # Collect matching files, parse ts from filename for stable ordering
    candidates: list[tuple[str, Path]] = []
    for p in generations_dir(root).glob("*.json"):
        if p.name.endswith("_portfolio.json"):
            continue
        m = _GEN_FILE.match(p.name)
        if not m:
            continue
        pid = m.group(2)
        if pid != product_id:
            continue
        candidates.append((m.group(1), p))

    candidates.sort(key=lambda x: x[0])  # oldest first (lexicographic on ts works for UTC Z)

    prev_top: str | None = None
    for _ts, path in candidates:
        raw = _parse_bundle(path)
        if raw is None:
            continue
        rel = path.relative_to(rel_root).as_posix()
        ent = bundle_to_entry(raw, source_path=rel, previous_top_summary=prev_top)
        entries.append(ent)
        prev_top = ent.top_recommended_action

    return entries


def load_all_products_with_history(repo_root: Path) -> dict[str, list[DecisionMemoryEntry]]:
    """Map product_id -> chronological entries (oldest first)."""
    root = repo_root.resolve()
    by_pid: dict[str, list[tuple[str, Path]]] = {}
    base = generations_dir(root)
    if not base.is_dir():
        return {}
    for p in base.glob("*.json"):
        if p.name.endswith("_portfolio.json"):
            continue
        m = _GEN_FILE.match(p.name)
        if not m:
            continue
        ts, pid = m.group(1), m.group(2)
        by_pid.setdefault(pid, []).append((ts, p))

    out: dict[str, list[DecisionMemoryEntry]] = {}
    for pid, pairs in by_pid.items():
        pairs.sort(key=lambda x: x[0])
        prev_top: str | None = None
        ents: list[DecisionMemoryEntry] = []
        for _ts, path in pairs:
            raw = _parse_bundle(path)
            if raw is None:
                continue
            rel = path.relative_to(root).as_posix()
            ent = bundle_to_entry(raw, source_path=rel, previous_top_summary=prev_top)
            ents.append(ent)
            prev_top = ent.top_recommended_action
        if ents:
            out[pid] = ents
    return out
