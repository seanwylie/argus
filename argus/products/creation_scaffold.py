"""
Bridge approved creation proposals to on-disk product scaffolds.

Deterministic: only uses proposal payloads, registry-backed mission ids, and existing scaffold helpers.
"""

from __future__ import annotations

import hashlib
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from argus.core.serialize import dumps_json
from argus.mission.mission import load_mission_registry, resolve_creation_mission
from argus.mission.product_mission import (
    parse_product_mission_from_yaml,
    validate_product_mission_registry,
)
from argus.products.creation import (
    INITIAL_SUGGESTED_MISSION_SCHEMA,
    PRODUCT_CREATION_PROPOSALS_SCHEMA,
    creation_proposals_dir,
)
from argus.products.git_lifecycle import init_argus_product_git
from argus.products.loader import attach_paths, load_yaml_file
from argus.products.scaffold import (
    _TEMPLATE_SPECS,
    TEMPLATE_TYPES,
    _dump_yaml,
    _readme_text,
    _script_stub,
    _signals_block,
    display_name_from_slug,
    normalize_product_slug,
)
from argus.products.validate import validate_manifest
from argus.project_permissions.load import default_policy_yaml_text

PRODUCT_CREATION_SCAFFOLD_SCHEMA = "argus.product_creation_scaffold.v1"

_DEFAULT_TEMPLATE = "content_stream"

_ROLE_TO_TEMPLATE: dict[str, str] = {
    "seed": "content_stream",
    "complement": "micro_saas",
    "pipeline": "content_stream",
    "growth": "micro_saas",
    "remedy": "utility_api",
    "mission_coverage": "content_stream",
    "unspecified": "content_stream",
}


def creation_scaffold_dir(repo_root: Path) -> Path:
    return Path(repo_root).resolve() / "runs" / "products" / "creation_scaffold"


def _load_json(path: Path) -> dict[str, Any] | None:
    import json

    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return raw if isinstance(raw, dict) else None


def _registry_profile_ids(repo_root: Path) -> set[str]:
    reg = load_mission_registry(repo_root)
    profs = reg.get("profiles") or {}
    if not isinstance(profs, dict):
        return set()
    return {str(k).strip().lower() for k in profs.keys() if str(k).strip()}


def _filter_profile_id_refs(
    raw_list: list[str],
    *,
    objective: str,
    registry_ids: set[str],
    excluded: set[str] | None = None,
) -> list[str]:
    """Keep only strings that are known mission profile ids and not in excluded (objective + prior picks)."""
    obj = objective.strip().lower()
    ex = {obj, *(excluded or set())}
    out: list[str] = []
    seen: set[str] = set()
    for x in raw_list:
        if not isinstance(x, str):
            continue
        tid = x.strip().lower()
        if not tid or tid in seen or tid in ex:
            continue
        if tid not in registry_ids:
            continue
        seen.add(tid)
        out.append(tid)
    return sorted(out)


def _mission_block_for_scaffold(repo_root: Path, proposal: dict[str, Any]) -> dict[str, Any]:
    sm = proposal.get("initial_suggested_mission") or {}
    if not isinstance(sm, dict):
        sm = {}
    reg_ids = _registry_profile_ids(repo_root)
    objective = str(sm.get("objective") or "").strip().lower()
    if not objective or objective not in reg_ids:
        raise ValueError(
            "proposal.initial_suggested_mission.objective must be a known mission profile id "
            f"(from config/mission_profiles.yaml); got {sm.get('objective')!r}"
        )
    dr = sm.get("drivers") or []
    gr = sm.get("guardrails") or []
    if not isinstance(dr, list):
        dr = []
    if not isinstance(gr, list):
        gr = []
    dr_s = [str(x).strip().lower() for x in dr if isinstance(x, str) and str(x).strip()]
    gr_s = [str(x).strip().lower() for x in gr if isinstance(x, str) and str(x).strip()]

    machine = bool(sm.get("mission_profile_fields_are_registry_ids")) or str(sm.get("schema") or "") in (
        INITIAL_SUGGESTED_MISSION_SCHEMA,
        "argus.initial_suggested_mission.v1",
    )

    if machine:
        drivers = sorted({x for x in dr_s if x in reg_ids and x != objective})
        ex = {objective, *drivers}
        guardrails = sorted({x for x in gr_s if x in reg_ids and x not in ex})
    else:
        drivers = _filter_profile_id_refs(dr_s, objective=objective, registry_ids=reg_ids)
        guardrails = _filter_profile_id_refs(
            gr_s,
            objective=objective,
            registry_ids=reg_ids,
            excluded=set(drivers),
        )
    rp_raw = sm.get("risk_posture")
    rp: str | None = None
    if rp_raw is not None:
        rp = str(rp_raw).strip().lower()
        if rp not in ("conservative", "moderate", "aggressive"):
            rp = None
    block: dict[str, Any] = {
        "objective": objective,
        "drivers": drivers,
        "guardrails": guardrails,
    }
    if rp:
        block["risk_posture"] = rp
    return block


def _template_for_proposal(proposal: dict[str, Any]) -> str:
    role = str(proposal.get("expected_role_in_portfolio") or "").strip().lower()
    t = _ROLE_TO_TEMPLATE.get(role, _DEFAULT_TEMPLATE)
    if t not in TEMPLATE_TYPES:
        return _DEFAULT_TEMPLATE
    return t


def _find_proposal(repo_root: Path, proposal_id: str) -> dict[str, Any] | None:
    pid = str(proposal_id).strip()
    if not pid:
        return None
    d = creation_proposals_dir(repo_root)
    candidates: list[Path] = []
    latest = d / "latest.json"
    if latest.is_file():
        candidates.append(latest)
    for p in sorted(d.glob("*.json"), reverse=True):
        if p.name == "latest.json":
            continue
        candidates.append(p)
    seen: set[str] = set()
    for path in candidates:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        raw = _load_json(path)
        if not raw or str(raw.get("schema") or "") != PRODUCT_CREATION_PROPOSALS_SCHEMA:
            continue
        for prop in raw.get("proposals") or []:
            if isinstance(prop, dict) and str(prop.get("proposal_id") or "") == pid:
                return prop
    return None


def _starter_signals_yaml(*, product_id: str) -> str:
    return (
        f"# Starter signal manifest — extend after onboarding.\n"
        f"schema: argus.product_signal_manifest.v1\n"
        f"product_id: {product_id}\n"
        f"\n"
        f"signals:\n"
        f"  - id: scaffold_readme\n"
        f"    category: quality\n"
        f"    source_type: filesystem\n"
        f"    path: README.md\n"
        f"    freshness_sla: 30d\n"
        f"    value_type: string\n"
        f"    required_for: none\n"
        f"    trust_level: heuristic\n"
        f"    description: \"Product README present — replace with concrete signals.\"\n"
    )


def _starter_doctrine_yaml() -> str:
    return """schema: argus.doctrine.v1
summary: Bootstrap doctrine from creation scaffold — refine with strategy and constraints.
principles:
  - Align execution with mission objective and guardrails in product.yaml.
  - Ship small, measure, iterate.
constraints: {}
scoring:
  experiment_score_boost: 0.1
"""


def _creation_notes_markdown(*, proposal: dict[str, Any], product_id: str) -> str:
    import json

    sm = proposal.get("initial_suggested_mission") or {}
    ev = proposal.get("evidence_summary") or {}
    sm_txt = json.dumps(sm, indent=2, sort_keys=True) if sm else "{}"
    return f"""# Creation record

This product was scaffolded from an **Argus creation proposal** (deterministic bridge).

| Field | Value |
|-------|-------|
| Proposal id | `{proposal.get("proposal_id")}` |
| Concept | {proposal.get("concept_title")} |
| Role | `{proposal.get("expected_role_in_portfolio")}` |
| Product id | `{product_id}` |

## Evidence (from proposal)

- **Gap:** `{ev.get("gap_id")}` — {ev.get("gap_title")}
- **Severity:** `{ev.get("gap_severity")}`
- **Opportunity type:** `{ev.get("opportunity_type")}`

## Rationale (proposal)

{proposal.get("rationale") or "—"}

## Suggested mission snapshot (pre-filter)

```json
{sm_txt}
```

When `initial_suggested_mission.mission_profile_fields_are_registry_ids` is true (v1 schema),
drivers and guardrails are copied as registry profile ids. Legacy proposals without that flag
filter unknown strings at scaffold time.
"""


def build_product_yaml_from_creation_proposal(
    repo_root: Path,
    *,
    product_id: str,
    proposal: dict[str, Any],
) -> dict[str, Any]:
    """Assemble the `product.yaml` mapping for validate_manifest."""
    root = repo_root.resolve()
    slug = str(product_id).strip()
    display_name = display_name_from_slug(slug)
    template_type = _template_for_proposal(proposal)
    spec = _TEMPLATE_SPECS[template_type]
    mission_block = _mission_block_for_scaffold(root, proposal)

    payload: dict[str, Any] = {
        "id": slug,
        "name": display_name,
        "type": template_type,
        "status": "experimental",
        "state": "idea",
        "owner": {
            "team": "argus",
            "operator": "",
        },
        "metrics": {
            "local_paths": ["metrics/"],
            "primary": list(spec["metrics_primary"]),
        },
        "cost": {
            "monthly_usd": spec["cost_monthly_usd"],
            "notes": spec["cost_notes"],
        },
        "signals": _signals_block(),
        "actions": {
            "start": "./scripts/start.sh",
            "stop": "./scripts/stop.sh",
            "analyze": "./scripts/analyze.sh",
        },
        "constraints": {
            "max_monthly_cost_usd": spec["max_monthly_cost_usd"],
            "min_activity_threshold": spec["min_activity_threshold"],
        },
        "lifecycle": {
            "stage": "idea",
            "next_gate": spec["next_gate"],
        },
        "mission": mission_block,
        "tags": ["argus-creation-scaffold"],
        "raw_extensions": {
            "argus_creation_provenance": {
                "schema": "argus.creation_provenance.v1",
                "proposal_id": str(proposal.get("proposal_id") or ""),
                "concept_title": str(proposal.get("concept_title") or ""),
                "creation_mission_used": str(proposal.get("creation_mission_used") or ""),
                "expected_role_in_portfolio": str(proposal.get("expected_role_in_portfolio") or ""),
            },
        },
    }
    spec_obj, err = parse_product_mission_from_yaml(payload)
    if err:
        raise ValueError(err)
    if spec_obj:
        reg_err = validate_product_mission_registry(root, spec_obj)
        if reg_err:
            raise ValueError(reg_err)
    return payload


def _planned_file_map(
    repo_root: Path,
    *,
    product_id: str,
    proposal: dict[str, Any],
) -> dict[str, str]:
    """Relative path (posix under repo) -> full text."""
    root = repo_root.resolve()
    out: dict[str, str] = {}
    yml = build_product_yaml_from_creation_proposal(root, product_id=product_id, proposal=proposal)
    out[f"products/{product_id}/product.yaml"] = _dump_yaml(yml)
    for action in ("start", "stop", "analyze"):
        out[f"products/{product_id}/scripts/{action}.sh"] = _script_stub(action, product_id)
    out[f"products/{product_id}/README.md"] = _readme_text(
        product_id=product_id,
        display_name=display_name_from_slug(product_id),
        template_type=_template_for_proposal(proposal),
    )
    out[f"products/{product_id}/signals.yaml"] = _starter_signals_yaml(product_id=product_id)
    out[f"products/{product_id}/doctrine.yaml"] = _starter_doctrine_yaml()
    out[f"products/{product_id}/notes/CREATION.md"] = _creation_notes_markdown(proposal=proposal, product_id=product_id)
    out[f"products/{product_id}/app/.gitkeep"] = "# Placeholder so empty directories are tracked by git.\n"
    out[f"products/{product_id}/config/.gitkeep"] = "# Placeholder so empty directories are tracked by git.\n"
    out[f"products/{product_id}/metrics/.gitkeep"] = "# Placeholder so empty directories are tracked by git.\n"
    out[f"products/{product_id}/argus.policy.yaml"] = default_policy_yaml_text()
    return out


def evaluate_product_creation_scaffold(
    repo_root: Path,
    *,
    proposal_id: str,
    product_id_override: str | None = None,
    dry_run: bool = False,
    init_git: bool = True,
) -> dict[str, Any]:
    root = repo_root.resolve()
    evaluated_at = datetime.now(timezone.utc).isoformat()
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    prop = _find_proposal(root, proposal_id)
    if prop is None:
        return {
            "schema": PRODUCT_CREATION_SCAFFOLD_SCHEMA,
            "run_id": run_id,
            "evaluated_at_utc": evaluated_at,
            "ok": False,
            "error": f"proposal_id not found in {creation_proposals_dir(root)}: {proposal_id!r}",
            "dry_run": dry_run,
        }

    creation_mission = resolve_creation_mission(root)

    pid_override = str(product_id_override).strip() if product_id_override else None
    if pid_override:
        try:
            slug = normalize_product_slug(pid_override)
        except ValueError as e:
            return {
                "schema": PRODUCT_CREATION_SCAFFOLD_SCHEMA,
                "run_id": run_id,
                "evaluated_at_utc": evaluated_at,
                "ok": False,
                "error": str(e),
                "dry_run": dry_run,
            }
    else:
        try:
            slug = normalize_product_slug(str(prop.get("concept_title") or "product"))
        except ValueError as e:
            return {
                "schema": PRODUCT_CREATION_SCAFFOLD_SCHEMA,
                "run_id": run_id,
                "evaluated_at_utc": evaluated_at,
                "ok": False,
                "error": f"could not derive product id from concept_title: {e}",
                "dry_run": dry_run,
            }

    product_root = root / "products" / slug
    if product_root.exists():
        return {
            "schema": PRODUCT_CREATION_SCAFFOLD_SCHEMA,
            "run_id": run_id,
            "evaluated_at_utc": evaluated_at,
            "ok": False,
            "error": f"refusing to overwrite existing product directory: {product_root}",
            "proposal_id": proposal_id,
            "product_id": slug,
            "dry_run": dry_run,
        }

    try:
        yml_payload = build_product_yaml_from_creation_proposal(root, product_id=slug, proposal=prop)
    except ValueError as e:
        return {
            "schema": PRODUCT_CREATION_SCAFFOLD_SCHEMA,
            "run_id": run_id,
            "evaluated_at_utc": evaluated_at,
            "ok": False,
            "error": str(e),
            "proposal_id": proposal_id,
            "product_id": slug,
            "dry_run": dry_run,
        }

    planned = _planned_file_map(root, product_id=slug, proposal=prop)
    planned_sha = {k: hashlib.sha256(v.encode("utf-8")).hexdigest()[:16] for k, v in sorted(planned.items())}

    if dry_run:
        return {
            "schema": PRODUCT_CREATION_SCAFFOLD_SCHEMA,
            "run_id": run_id,
            "evaluated_at_utc": evaluated_at,
            "ok": True,
            "dry_run": True,
            "proposal_id": proposal_id,
            "product_id": slug,
            "proposal_snapshot": {
                "concept_title": prop.get("concept_title"),
                "expected_role_in_portfolio": prop.get("expected_role_in_portfolio"),
                "creation_mission_used": prop.get("creation_mission_used"),
            },
            "mission_block": yml_payload.get("mission"),
            "creation_mission_at_scaffold": {
                "resolved_mission_id": creation_mission.get("resolved_mission_id"),
                "note": "Compared with proposal.creation_mission_used for drift awareness.",
            },
            "template_type": _template_for_proposal(prop),
            "planned_files": planned,
            "planned_file_sha256_prefix": planned_sha,
            "paths_created": [],
        }

    # Write files
    paths_created: list[str] = []
    for rel, text in sorted(planned.items()):
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        if rel.endswith(".sh"):
            try:
                path.chmod(0o755)
            except OSError:
                pass
        paths_created.append(rel)

    config_path = root / "products" / slug / "product.yaml"
    raw, err = load_yaml_file(config_path)
    if err is not None or raw is None:
        shutil.rmtree(product_root, ignore_errors=True)
        return {
            "schema": PRODUCT_CREATION_SCAFFOLD_SCHEMA,
            "run_id": run_id,
            "evaluated_at_utc": evaluated_at,
            "ok": False,
            "error": f"internal error loading written yaml: {err}",
            "dry_run": False,
        }
    merged = attach_paths(raw, repo_root=root, product_root=product_root, config_path=config_path)
    result = validate_manifest(merged, repo_root=root, product_root=product_root, config_path=config_path)
    if result.errors:
        shutil.rmtree(product_root, ignore_errors=True)
        return {
            "schema": PRODUCT_CREATION_SCAFFOLD_SCHEMA,
            "run_id": run_id,
            "evaluated_at_utc": evaluated_at,
            "ok": False,
            "error": "generated product failed validation: " + "; ".join(result.errors),
            "dry_run": False,
        }

    git_info: dict[str, Any] = {}
    if init_git:
        git_info = init_argus_product_git(product_root)

    return {
        "schema": PRODUCT_CREATION_SCAFFOLD_SCHEMA,
        "run_id": run_id,
        "evaluated_at_utc": evaluated_at,
        "ok": True,
        "dry_run": False,
        "proposal_id": proposal_id,
        "product_id": slug,
        "proposal_snapshot": {
            "concept_title": prop.get("concept_title"),
            "expected_role_in_portfolio": prop.get("expected_role_in_portfolio"),
            "creation_mission_used": prop.get("creation_mission_used"),
        },
        "mission_block": yml_payload.get("mission"),
        "creation_mission_at_scaffold": {
            "resolved_mission_id": creation_mission.get("resolved_mission_id"),
        },
        "template_type": _template_for_proposal(prop),
        "paths_created": paths_created,
        "validation_warnings": result.warnings,
        "git": git_info,
    }


def render_product_creation_scaffold_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Product creation scaffold",
        "",
        f"**Schema:** `{payload.get('schema')}`",
        f"**Run id:** `{payload.get('run_id')}`",
        f"**Evaluated (UTC):** {payload.get('evaluated_at_utc')}",
        "",
    ]
    if not payload.get("ok"):
        lines.append(f"**Error:** {payload.get('error')}")
        lines.append("")
        return "\n".join(lines)

    lines.append(f"- **Proposal:** `{payload.get('proposal_id')}`")
    lines.append(f"- **Product id:** `{payload.get('product_id')}`")
    lines.append(f"- **Dry run:** {payload.get('dry_run')}")
    mb = payload.get("mission_block") or {}
    if mb:
        lines.append(f"- **Mission objective:** `{mb.get('objective')}`")
    if payload.get("dry_run"):
        lines.extend(["", "## Planned files", ""])
        for p in sorted((payload.get("planned_files") or {}).keys()):
            lines.append(f"- `{p}`")
    else:
        lines.extend(["", "## Created paths", ""])
        for p in payload.get("paths_created") or []:
            lines.append(f"- `{p}`")
    lines.append("")
    return "\n".join(lines)


def write_product_creation_scaffold_artifacts(
    repo_root: Path,
    payload: dict[str, Any],
    *,
    run_id: str | None = None,
) -> tuple[Path, Path, Path, Path]:
    root = repo_root.resolve()
    rid = run_id or str(payload.get("run_id") or "")
    if not rid:
        rid = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    pl = dict(payload)
    pl["run_id"] = rid
    d = creation_scaffold_dir(root)
    d.mkdir(parents=True, exist_ok=True)
    stamped_json = d / f"{rid}.json"
    stamped_md = d / f"{rid}.md"
    latest_json = d / "latest.json"
    latest_md = d / "latest.md"
    stamped_json.write_text(dumps_json(pl) + "\n", encoding="utf-8")
    stamped_md.write_text(render_product_creation_scaffold_markdown(pl), encoding="utf-8")
    shutil.copyfile(stamped_json, latest_json)
    shutil.copyfile(stamped_md, latest_md)
    return stamped_json, stamped_md, latest_json, latest_md


def run_product_creation_scaffold(
    repo_root: Path,
    *,
    proposal_id: str,
    product_id: str | None = None,
    dry_run: bool = False,
    write_artifacts: bool = True,
    init_git: bool = True,
) -> dict[str, Any]:
    payload = evaluate_product_creation_scaffold(
        repo_root,
        proposal_id=proposal_id,
        product_id_override=product_id,
        dry_run=dry_run,
        init_git=init_git,
    )
    if write_artifacts and payload.get("schema") == PRODUCT_CREATION_SCAFFOLD_SCHEMA:
        write_product_creation_scaffold_artifacts(repo_root, payload)
    return payload


__all__ = [
    "PRODUCT_CREATION_SCAFFOLD_SCHEMA",
    "build_product_yaml_from_creation_proposal",
    "creation_scaffold_dir",
    "evaluate_product_creation_scaffold",
    "render_product_creation_scaffold_markdown",
    "run_product_creation_scaffold",
    "write_product_creation_scaffold_artifacts",
]
