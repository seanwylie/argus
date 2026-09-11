"""
Inspectability helpers for **evidence maturity** — distinguishing thin bootstrap / interpretation
from established readiness signals.

Used by the operator queue (per-entry hints) and portfolio intervention (suppressing
stagnation-style codes that would mis-label a product that simply has not finished first-pass
bootstrap yet).
"""

from __future__ import annotations

from typing import Any

EVIDENCE_MATURITY_HINT_SCHEMA = "argus.evidence_maturity_hint.v1"


def evidence_maturity_hint_from_snapshot(snap: dict[str, Any]) -> str:
    """
    Coarse label for operator-facing queue rows (durable string; not a tier promotion).

    * ``thin_bootstrap`` — unprofiled and first-pass not yet a successful completion.
    * ``thin_interpretation`` — first-pass succeeded but readiness tier still unprofiled.
    * ``unprofiled_other`` — unprofiled with failed/partial edge cases not covered above.
    * ``established`` — tier is not ``unprofiled`` (normal operating posture for queue scoring).
    """
    rd = snap.get("readiness") if isinstance(snap.get("readiness"), dict) else {}
    tier = str(rd.get("readiness_tier") or "").strip().lower()
    ih = snap.get("import_health") if isinstance(snap.get("import_health"), dict) else {}
    fps = str(ih.get("first_pass_status") or "").strip().lower()
    if tier != "unprofiled":
        return "established"
    if fps in ("pending", "skipped", "", "partial"):
        return "thin_bootstrap"
    if fps == "success":
        return "thin_interpretation"
    return "unprofiled_other"


def thin_evidence_baseline_from_fingerprint(fp: dict[str, Any]) -> bool:
    """
    True when stagnation-style intervention detections would be misleading:

    * readiness tier is still ``unprofiled``, and
    * first-pass has not completed successfully (pending / skipped / empty / partial).

    When True, intervention suppresses **tier stagnation**, **repeated same next_action**, and
    **low-confidence loop** codes — not import failures, blocked progression, or oscillation.
    """
    tr = str(fp.get("readiness_tier") or "").strip().lower()
    if tr != "unprofiled":
        return False
    fps = str(fp.get("first_pass_status") or "").strip().lower()
    return fps in ("pending", "skipped", "", "partial")
