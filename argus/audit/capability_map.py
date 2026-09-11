"""Derive capability entries from product.yaml + filesystem evidence (conservative)."""

from __future__ import annotations

from pathlib import Path

from argus.audit.models import AuditCapabilityEntry, AuditEvidence, AuditEvidenceKind, AuditStatus
from argus.audit.scanner import is_nontrivial_script
from argus.core.models.product import ProductNode


def _rel_script(product_root: Path, raw: str | None) -> Path | None:
    if not raw:
        return None
    s = raw.strip()
    if s.startswith("./"):
        s = s[2:]
    p = (product_root / s).resolve()
    try:
        if not str(p).startswith(str(product_root.resolve())):
            return None
    except Exception:
        return None
    return p


def build_capability_entries(
    product_id: str,
    product_root: Path,
    node: ProductNode,
) -> list[AuditCapabilityEntry]:
    """
    MVP capability rows: actions, metrics dirs, signals config, doctrine, cost note.

    Prefer **unknown** over **missing** when evidence is inconclusive.
    """
    entries: list[AuditCapabilityEntry] = []
    pr = product_root.resolve()

    # --- Declared actions (scripts) ---
    for name, raw in (
        ("start", node.actions.start),
        ("stop", node.actions.stop),
        ("analyze", node.actions.analyze),
    ):
        cid = f"audit.cap.action.{name}"
        path = _rel_script(pr, raw)
        if raw is None or not str(raw).strip():
            entries.append(
                AuditCapabilityEntry(
                    capability_id=cid,
                    product_id=product_id,
                    description=f"Action {name!r} not declared in product.yaml",
                    status=AuditStatus.MISSING,
                    confidence="high",
                    evidence=[],
                    notes="No path in actions map",
                )
            )
            continue
        if path is None:
            entries.append(
                AuditCapabilityEntry(
                    capability_id=cid,
                    product_id=product_id,
                    description=f"Action {name} path could not be resolved safely",
                    status=AuditStatus.UNKNOWN,
                    confidence="low",
                    evidence=[
                        AuditEvidence(AuditEvidenceKind.CONFIG_REF, "actions", detail=str(raw)),
                    ],
                    notes="Path resolution failed or escaped product root",
                )
            )
            continue
        if not path.is_file():
            entries.append(
                AuditCapabilityEntry(
                    capability_id=cid,
                    product_id=product_id,
                    description=f"Declared script for {name} not found on disk",
                    status=AuditStatus.MISSING,
                    confidence="high",
                    evidence=[
                        AuditEvidence(AuditEvidenceKind.CONFIG_REF, "product.yaml", detail=str(raw)),
                    ],
                    notes="",
                )
            )
            continue
        ok = is_nontrivial_script(path)
        st = AuditStatus.IMPLEMENTED if ok else AuditStatus.PARTIAL
        entries.append(
            AuditCapabilityEntry(
                capability_id=cid,
                product_id=product_id,
                description=f"Action {name} script at {path.name}",
                status=st,
                confidence="high" if ok else "medium",
                evidence=[
                    AuditEvidence(AuditEvidenceKind.SCRIPT_REF, str(path.relative_to(pr))),
                    AuditEvidence(AuditEvidenceKind.FILE_PATH, str(path.relative_to(pr)), detail=f"bytes={path.stat().st_size}"),
                ],
                notes="" if ok else "Script exists but appears placeholder or trivial",
            )
        )

    # --- Metrics paths ---
    mpaths = list(node.metrics.local_paths or [])
    if not mpaths:
        entries.append(
            AuditCapabilityEntry(
                capability_id="audit.cap.metrics.local_paths",
                product_id=product_id,
                description="No metrics.local_paths in product.yaml",
                status=AuditStatus.UNKNOWN,
                confidence="medium",
                evidence=[AuditEvidence(AuditEvidenceKind.CONFIG_REF, "metrics.local_paths", detail="empty")],
                notes="Cannot infer metrics layout without declared paths",
            )
        )
    else:
        any_present = False
        any_content = False
        for mp in mpaths:
            d = (pr / mp).resolve()
            if not str(d).startswith(str(pr)):
                continue
            if d.is_dir():
                any_present = True
                try:
                    for child in list(d.iterdir())[:40]:
                        if child.is_file():
                            any_content = True
                            break
                        if child.is_dir():
                            for gc in list(child.iterdir())[:20]:
                                if gc.is_file():
                                    any_content = True
                                    break
                            if any_content:
                                break
                except OSError:
                    pass
        if not any_present:
            st = AuditStatus.MISSING
            conf = "high"
            notes = "Declared metrics paths do not exist"
        elif any_content:
            st = AuditStatus.IMPLEMENTED
            conf = "medium"
            notes = "At least one file under metrics paths"
        else:
            st = AuditStatus.PARTIAL
            conf = "medium"
            notes = "Directories exist but no files observed (empty or shallow scan)"
        entries.append(
            AuditCapabilityEntry(
                capability_id="audit.cap.metrics.local_paths",
                product_id=product_id,
                description="Local metrics directories per product.yaml",
                status=st,
                confidence=conf,
                evidence=[AuditEvidence(AuditEvidenceKind.CONFIG_REF, "metrics.local_paths", detail=",".join(mpaths))],
                notes=notes,
            )
        )

    # --- Signals (enabled in yaml — local wiring only) ---
    enabled = [s for s in node.signals if s.enabled]
    if not enabled:
        entries.append(
            AuditCapabilityEntry(
                capability_id="audit.cap.signals.config",
                product_id=product_id,
                description="No enabled signal sources in product.yaml",
                status=AuditStatus.PARTIAL,
                confidence="medium",
                evidence=[AuditEvidence(AuditEvidenceKind.CONFIG_REF, "signals", detail="none enabled")],
                notes="May be intentional",
            )
        )
    else:
        kinds = ",".join(sorted(s.type for s in enabled))
        entries.append(
            AuditCapabilityEntry(
                capability_id="audit.cap.signals.config",
                product_id=product_id,
                description=f"Signal types declared: {kinds}",
                status=AuditStatus.IMPLEMENTED,
                confidence="high",
                evidence=[AuditEvidence(AuditEvidenceKind.CONFIG_REF, "product.yaml", detail=kinds)],
                notes="Runtime behavior not verified",
            )
        )

    # --- Doctrine file ---
    docp = pr / "doctrine.yaml"
    if docp.is_file():
        entries.append(
            AuditCapabilityEntry(
                capability_id="audit.cap.doctrine.file",
                product_id=product_id,
                description="doctrine.yaml present",
                status=AuditStatus.IMPLEMENTED,
                confidence="high",
                evidence=[AuditEvidence(AuditEvidenceKind.FILE_PATH, "doctrine.yaml")],
                notes="Content not validated",
            )
        )
    else:
        entries.append(
            AuditCapabilityEntry(
                capability_id="audit.cap.doctrine.file",
                product_id=product_id,
                description="No doctrine.yaml in product root",
                status=AuditStatus.MISSING,
                confidence="high",
                evidence=[],
                notes="Optional file",
            )
        )

    # --- Cost declaration (yaml only) ---
    if node.cost.monthly_usd is not None:
        entries.append(
            AuditCapabilityEntry(
                capability_id="audit.cap.cost.declared",
                product_id=product_id,
                description="Monthly cost estimate in yaml",
                status=AuditStatus.IMPLEMENTED,
                confidence="medium",
                evidence=[AuditEvidence(AuditEvidenceKind.CONFIG_REF, "cost.monthly_usd")],
                notes="Not verified against billing",
            )
        )
    else:
        entries.append(
            AuditCapabilityEntry(
                capability_id="audit.cap.cost.declared",
                product_id=product_id,
                description="No monthly_usd in product.yaml",
                status=AuditStatus.UNKNOWN,
                confidence="low",
                evidence=[],
                notes="Optional; unknown if cost tracked elsewhere",
            )
        )

    # --- Application source (heuristic) ---
    src_dirs = [p for p in (pr / "src", pr / "app") if p.is_dir()]
    if src_dirs:
        py_files: list[Path] = []
        for d in src_dirs:
            try:
                for p in d.iterdir():
                    if p.suffix == ".py" and p.is_file():
                        py_files.append(p)
                        break
                    if p.is_dir():
                        for q in list(p.iterdir())[:25]:
                            if q.suffix == ".py" and q.is_file():
                                py_files.append(q)
                                break
            except OSError:
                pass
        if py_files:
            entries.append(
                AuditCapabilityEntry(
                    capability_id="audit.cap.code.application",
                    product_id=product_id,
                    description="Application source files present under src/ or app/",
                    status=AuditStatus.PARTIAL,
                    confidence="low",
                    evidence=[
                        AuditEvidence(AuditEvidenceKind.FILE_PATH, str(py_files[0].relative_to(pr))),
                    ],
                    notes="Shallow check only; not a full codebase audit",
                )
            )
        else:
            entries.append(
                AuditCapabilityEntry(
                    capability_id="audit.cap.code.application",
                    product_id=product_id,
                    description="src/ or app/ exists but no .py files found (quick scan)",
                    status=AuditStatus.UNKNOWN,
                    confidence="low",
                    evidence=[],
                    notes="May be non-Python product",
                )
            )

    return entries
