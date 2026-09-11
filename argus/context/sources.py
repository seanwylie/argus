"""Shared loaders for doctrine and strategy text used by refinement cycle cache and context packets."""

from __future__ import annotations

from pathlib import Path

from argus.doctrine.load import load_doctrine_for_product
from argus.products.inventory import build_inventory
from argus.strategy.apply import load_strategy_record


def doctrine_excerpt(repo_root: Path, product_id: str | None, *, max_chars: int = 8000) -> str:
    if not product_id:
        return ""
    try:
        inv = build_inventory(repo_root)
        if product_id not in inv.valid:
            return ""
        pr = inv.valid[product_id].node.product_root
        doc, _ = load_doctrine_for_product(repo_root, product_id, product_root=pr)
        if doc is None:
            return ""
        parts = [doc.summary or "", " ".join(doc.principles)]
        return "\n".join(parts).strip()[:max_chars]
    except Exception:
        return ""


def strategy_summary(repo_root: Path, *, max_chars: int = 6000) -> tuple[str | None, str]:
    raw = load_strategy_record(repo_root)
    if not isinstance(raw, dict):
        return None, "(no strategy record)"
    mode = raw.get("mode")
    mstr = str(mode).strip() if mode is not None else ""
    lines: list[str] = []
    if mstr:
        lines.append(f"mode={mstr}")
    prof = raw.get("effective_profile")
    if isinstance(prof, dict):
        for k in ("kill_score_min", "move_forward_max", "experiment_bias"):
            if k in prof:
                lines.append(f"{k}={prof[k]}")
    ep = raw.get("persisted")
    if isinstance(ep, dict) and ep.get("updated_at_utc"):
        lines.append(f"record_updated={ep.get('updated_at_utc')}")
    text = "\n".join(lines) if lines else "(no strategy record)"
    return (mstr or None), text[:max_chars]
