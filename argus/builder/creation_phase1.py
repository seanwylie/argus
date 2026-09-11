"""
Builder Phase 1: proposal-first product scaffold from advisory creation candidates.

No autonomous loops — operator reviews proposal, then optional ``--write`` applies a bounded scaffold.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from argus.core.serialize import dumps_json, to_jsonable
from argus.products.scaffold import (
    TEMPLATE_TYPES,
    create_product_scaffold,
    display_name_from_slug,
    normalize_product_slug,
)
from argus.world_context.persist import (
    WORLD_CONTEXT_CREATION_CANDIDATES_SCHEMA,
    creation_candidates_output_dir,
)

BUILDER_CREATION_PROPOSAL_SCHEMA = "argus.builder.creation_proposal.v1"
BUILDER_CREATION_RESULT_SCHEMA = "argus.builder.creation_result.v1"

_CREATION_CANDIDATES_REL = "runs/world_context/creation_candidates/latest.json"


def builder_creation_phase1_dir(repo_root: Path) -> Path:
    return Path(repo_root).resolve() / "runs" / "builder" / "creation_phase1"


def load_latest_creation_candidates(repo_root: Path) -> dict[str, Any] | None:
    """Load ``runs/world_context/creation_candidates/latest.json`` if present and valid schema."""
    p = creation_candidates_output_dir(repo_root) / "latest.json"
    if not p.is_file():
        return None
    import json

    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if str(data.get("schema") or "") != WORLD_CONTEXT_CREATION_CANDIDATES_SCHEMA:
        return None
    return data


def _pick_candidate(payload: dict[str, Any], candidate_id: str) -> dict[str, Any] | None:
    want = str(candidate_id).strip()
    for c in payload.get("candidates") or []:
        if not isinstance(c, dict):
            continue
        if str(c.get("candidate_id") or "") == want:
            return c
    return None


def _infer_template_type(candidate: dict[str, Any]) -> str:
    """Deterministic template from candidate id / kind (bounded heuristic)."""
    cid = str(candidate.get("candidate_id") or "").lower()
    kind = str(candidate.get("candidate_kind") or "")
    if kind == "restrain" or cid.startswith("observe_restrain"):
        return "static_site"
    if "engagement" in cid or "funnel" in cid:
        return "micro_saas"
    if "canada" in cid:
        return "static_site"
    return "static_site"


def _planned_paths(product_id: str) -> list[str]:
    root = f"products/{product_id}"
    return [
        f"{root}/product.yaml",
        f"{root}/README.md",
        f"{root}/argus.policy.yaml",
        f"{root}/app/.gitkeep",
        f"{root}/config/.gitkeep",
        f"{root}/metrics/",
        f"{root}/scripts/start.sh",
        f"{root}/scripts/stop.sh",
        f"{root}/scripts/analyze.sh",
    ]


def _import_plan_notes(*, entity: str, product_id: str) -> str:
    return (
        f"If `{entity}` is primarily developed in an **external git repository**, prefer importing "
        f"that tree into `products/{product_id}/app/` using the repo importer (see "
        f"`tools/import_product.py` and docs) rather than treating this scaffold as the app source. "
        f"This Phase-1 scaffold is a **credible Argus product node** so portfolio loops can run; "
        f"it does not replace an existing codebase."
    )


def build_creation_proposal(
    repo_root: Path,
    *,
    candidate_id: str,
    operator_product_id: str | None = None,
    template_type: str | None = None,
) -> dict[str, Any]:
    """
    Build inspectable proposal dict (does not write files under ``products/``).

    Raises ``ValueError`` with a short message if inputs are invalid.
    """
    root = repo_root.resolve()
    cc = load_latest_creation_candidates(root)
    if not cc:
        raise ValueError(
            f"missing or invalid {_CREATION_CANDIDATES_REL} — run world-context pipeline first "
            "(e.g. ingest signals and materialize creation candidates)."
        )
    cand = _pick_candidate(cc, candidate_id)
    if not cand:
        raise ValueError(f"candidate_id not found in creation candidates: {candidate_id!r}")

    entities = [str(e) for e in (cand.get("entities") or []) if str(e).strip()]
    if not entities:
        raise ValueError("candidate has no entities — cannot derive a default product slug")

    if operator_product_id:
        try:
            resolved = normalize_product_slug(operator_product_id)
        except ValueError as e:
            raise ValueError(str(e)) from e
    else:
        try:
            resolved = normalize_product_slug(entities[0])
        except ValueError as e:
            raise ValueError(f"cannot normalize default slug from entity {entities[0]!r}: {e}") from e

    tt = template_type if template_type is not None else _infer_template_type(cand)
    if tt not in TEMPLATE_TYPES:
        raise ValueError(f"unknown template_type {tt!r}; expected one of: {sorted(TEMPLATE_TYPES)}")

    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    primary_entity = entities[0]

    request = {
        "schema": "argus.builder.creation_request.v1",
        "candidate_id": str(cand.get("candidate_id") or ""),
        "operator_product_id": operator_product_id,
        "template_type": tt,
    }

    assumptions = [
        "Creation candidates are advisory — this proposal does not validate business merit.",
        f"Default product id derived from operator input or first entity `{primary_entity}`.",
        "Scaffold uses Argus `products scaffold` layout — not a full application implementation.",
    ]

    unimplemented = [
        "Application/domain code beyond stub scripts",
        "Deployment, secrets, third-party integrations",
        "Analytics wiring beyond manifest stubs",
        "Autonomous execution or policy bypass",
    ]

    content_plan = [
        f"Emit validated `product.yaml` for `{resolved}` using template `{tt}`.",
        "Add stub `start` / `stop` / `analyze` scripts and Phase 1 default `argus.policy.yaml`.",
        "Add README describing the Argus product node layout.",
    ]

    proposal: dict[str, Any] = {
        "schema": BUILDER_CREATION_PROPOSAL_SCHEMA,
        "generated_at_utc": now,
        "advisory_only": True,
        "disclaimer": (
            "Builder Phase 1 proposes a bounded filesystem scaffold from an advisory creation candidate. "
            "It is not a product decision and not executed automatically."
        ),
        "source": {
            "creation_candidates_artifact_rel": _CREATION_CANDIDATES_REL,
            "creation_candidates_generated_at_utc": cc.get("generated_at_utc"),
        },
        "request": request,
        "resolved_product_id": resolved,
        "resolved_display_name": display_name_from_slug(resolved),
        "chosen_candidate": {
            "candidate_id": cand.get("candidate_id"),
            "candidate_kind": cand.get("candidate_kind"),
            "title": cand.get("title"),
            "rationale": cand.get("rationale"),
            "entities": entities,
            "evidence_summary": cand.get("evidence_summary"),
            "interpretation_patterns": cand.get("interpretation_patterns"),
            "strength": cand.get("strength"),
        },
        "scaffold_kind": "new_product",
        "template_type": tt,
        "files_to_create": _planned_paths(resolved),
        "content_plan": content_plan,
        "assumptions": assumptions,
        "intentionally_unimplemented": unimplemented,
        "import_plan_notes": _import_plan_notes(entity=primary_entity, product_id=resolved),
    }
    return proposal


def render_creation_proposal_markdown(proposal: dict[str, Any]) -> str:
    """Human-readable proposal for operator review."""
    lines = [
        "# Builder Phase 1 — creation proposal",
        "",
        proposal.get("disclaimer") or "",
        "",
        f"**Generated (UTC):** `{proposal.get('generated_at_utc')}`",
        f"**Resolved product id:** `{proposal.get('resolved_product_id')}`",
        f"**Template:** `{proposal.get('template_type')}`",
        "",
        "## Chosen candidate (advisory)",
        "",
    ]
    ch = proposal.get("chosen_candidate") or {}
    lines.append(f"- **candidate_id:** `{ch.get('candidate_id')}`")
    lines.append(f"- **title:** {ch.get('title')}")
    lines.append(f"- **rationale:** {ch.get('rationale')}")
    lines.append(f"- **entities:** {', '.join(f'`{e}`' for e in (ch.get('entities') or []))}")
    lines.append("")
    lines.append("## Files / directories to create")
    lines.append("")
    for p in proposal.get("files_to_create") or []:
        lines.append(f"- `{p}`")
    lines.append("")
    lines.append("## Content plan")
    lines.append("")
    for row in proposal.get("content_plan") or []:
        lines.append(f"- {row}")
    lines.append("")
    lines.append("## Import alternative (external repo)")
    lines.append("")
    lines.append(str(proposal.get("import_plan_notes") or "—"))
    lines.append("")
    lines.append("## Assumptions")
    lines.append("")
    for row in proposal.get("assumptions") or []:
        lines.append(f"- {row}")
    lines.append("")
    lines.append("## Intentionally not implemented")
    lines.append("")
    for row in proposal.get("intentionally_unimplemented") or []:
        lines.append(f"- {row}")
    lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def write_creation_proposal_artifacts(repo_root: Path, proposal: dict[str, Any]) -> tuple[Path, Path]:
    """Write ``latest_proposal.{json,md}`` under ``runs/builder/creation_phase1/``."""
    d = builder_creation_phase1_dir(repo_root)
    d.mkdir(parents=True, exist_ok=True)
    jp = d / "latest_proposal.json"
    mp = d / "latest_proposal.md"
    jp.write_text(dumps_json(proposal) + "\n", encoding="utf-8")
    mp.write_text(render_creation_proposal_markdown(proposal), encoding="utf-8")
    return jp, mp


def load_latest_creation_proposal(repo_root: Path) -> dict[str, Any] | None:
    p = builder_creation_phase1_dir(repo_root) / "latest_proposal.json"
    if not p.is_file():
        return None
    import json

    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if str(data.get("schema") or "") != BUILDER_CREATION_PROPOSAL_SCHEMA:
        return None
    return data


def apply_creation_proposal(
    repo_root: Path,
    proposal: dict[str, Any],
    *,
    write: bool = False,
    products_dir: Path | None = None,
) -> dict[str, Any]:
    """
    Dry-run by default: returns planned paths. With ``write=True``, runs :func:`create_product_scaffold`.
    """
    root = repo_root.resolve()
    if str(proposal.get("schema") or "") != BUILDER_CREATION_PROPOSAL_SCHEMA:
        return {
            "schema": BUILDER_CREATION_RESULT_SCHEMA,
            "ok": False,
            "error": "invalid proposal schema",
        }

    pid = str(proposal.get("resolved_product_id") or "").strip()
    tt = str(proposal.get("template_type") or "").strip()
    if not pid or tt not in TEMPLATE_TYPES:
        return {
            "schema": BUILDER_CREATION_RESULT_SCHEMA,
            "ok": False,
            "error": "proposal missing resolved_product_id or template_type",
        }

    planned = _planned_paths(pid)
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    if not write:
        return {
            "schema": BUILDER_CREATION_RESULT_SCHEMA,
            "generated_at_utc": now,
            "ok": True,
            "dry_run": True,
            "write": False,
            "resolved_product_id": pid,
            "template_type": tt,
            "would_create_paths": planned,
            "message": "Dry-run only — no files written under products/. Use --write to apply.",
        }

    code, msg, _git = create_product_scaffold(
        root,
        raw_name=pid,
        template_type=tt,
        products_dir=products_dir,
        force=False,
    )
    ok = code == 0
    pdir = (products_dir.resolve() if products_dir is not None else root / "products") / pid
    return {
        "schema": BUILDER_CREATION_RESULT_SCHEMA,
        "generated_at_utc": now,
        "ok": ok,
        "dry_run": False,
        "write": True,
        "exit_code": code,
        "message": msg,
        "product_root": str(pdir) if ok else None,
        "created_paths_hint": planned if ok else [],
    }


def write_creation_result_artifact(repo_root: Path, result: dict[str, Any]) -> Path:
    p = builder_creation_phase1_dir(repo_root) / "latest_result.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(dumps_json(to_jsonable(result)) + "\n", encoding="utf-8")
    return p


__all__ = [
    "BUILDER_CREATION_PROPOSAL_SCHEMA",
    "BUILDER_CREATION_RESULT_SCHEMA",
    "apply_creation_proposal",
    "build_creation_proposal",
    "builder_creation_phase1_dir",
    "load_latest_creation_candidates",
    "load_latest_creation_proposal",
    "render_creation_proposal_markdown",
    "write_creation_proposal_artifacts",
    "write_creation_result_artifact",
]
