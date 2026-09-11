"""
Read-only operator policy experiments: compare queue, quiescence, intervention, and cycle synthesis
across policy profiles without writing portfolio progression or orchestration artifacts.
"""

from __future__ import annotations

import copy
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from argus.core.serialize import dumps_json
from argus.policy.operator_policy import (
    _deep_merge,
    default_operator_policy,
    load_operator_policy,
    validate_operator_policy,
)
from argus.portfolio.cycle import synthesize_overall_operator_recommendation
from argus.portfolio.delta_report import evaluate_portfolio_delta_report
from argus.portfolio.intervention import evaluate_portfolio_intervention
from argus.portfolio.operator_queue import OPERATOR_QUEUE_SCHEMA, build_operator_queue_payload
from argus.portfolio.quiescence import evaluate_portfolio_quiescence

OPERATOR_POLICY_EXPERIMENT_SCHEMA = "argus.operator_policy_experiment.v1"

TOP_SLICE_N = 12


def policy_experiment_output_dir(repo_root: Path) -> Path:
    return Path(repo_root).resolve() / "runs" / "policy" / "experiments"


def load_policy_from_yaml_file(path: Path) -> dict[str, Any]:
    """Merge YAML over frozen defaults and validate."""
    p = path.resolve()
    if not p.is_file():
        raise ValueError(f"policy profile file not found: {p}")
    try:
        raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as e:
        raise ValueError(f"Failed to read {p}: {e}") from e
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ValueError(f"{p} must parse to a mapping at the top level")
    merged = _deep_merge(default_operator_policy(), raw)
    validate_operator_policy(merged)
    return merged


def _queue_top_slice(queue_payload: dict[str, Any], *, n: int = TOP_SLICE_N) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for e in list(queue_payload.get("entries") or [])[:n]:
        if not isinstance(e, dict):
            continue
        rows.append(
            {
                "product_id": e.get("product_id"),
                "queue_rank": e.get("queue_rank"),
                "priority_score": e.get("priority_score"),
                "readiness_tier": e.get("readiness_tier"),
                "next_action": e.get("next_action"),
            }
        )
    return rows


def _intervention_category_map(payload: dict[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    for row in payload.get("flagged_products") or []:
        if not isinstance(row, dict):
            continue
        pid = str(row.get("product_id") or "").strip()
        if pid:
            out[pid] = str(row.get("intervention_category") or "")
    return out


def _diff_queue_vs_reference(
    ref_queue: dict[str, Any],
    other_queue: dict[str, Any],
    *,
    score_delta_material: float,
) -> dict[str, Any]:
    """Products whose priority score or rank changes materially vs reference."""
    ref_entries = {str(e.get("product_id")): e for e in (ref_queue.get("entries") or []) if isinstance(e, dict)}
    other_entries = {str(e.get("product_id")): e for e in (other_queue.get("entries") or []) if isinstance(e, dict)}
    pids = sorted(set(ref_entries) | set(other_entries))
    material: list[dict[str, Any]] = []
    for pid in pids:
        a, b = ref_entries.get(pid), other_entries.get(pid)
        if not isinstance(a, dict) or not isinstance(b, dict):
            continue
        try:
            sa = float(a.get("priority_score")) if a.get("priority_score") is not None else None
            sb = float(b.get("priority_score")) if b.get("priority_score") is not None else None
        except (TypeError, ValueError):
            sa, sb = None, None
        ra = int(a.get("queue_rank")) if a.get("queue_rank") is not None else None
        rb = int(b.get("queue_rank")) if b.get("queue_rank") is not None else None
        score_moved = False
        if sa is not None and sb is not None and abs(sb - sa) >= score_delta_material:
            score_moved = True
        rank_moved = ra is not None and rb is not None and ra != rb
        if score_moved or rank_moved:
            material.append(
                {
                    "product_id": pid,
                    "reference_score": sa,
                    "profile_score": sb,
                    "score_delta": round(sb - sa, 4) if sa is not None and sb is not None else None,
                    "reference_rank": ra,
                    "profile_rank": rb,
                    "material_by_score": score_moved,
                    "material_by_rank": rank_moved,
                }
            )
    ref_order = [str(x.get("product_id")) for x in (ref_queue.get("entries") or []) if isinstance(x, dict)]
    other_order = [str(x.get("product_id")) for x in (other_queue.get("entries") or []) if isinstance(x, dict)]
    reordered = ref_order[:TOP_SLICE_N] != other_order[:TOP_SLICE_N]
    return {
        "products_with_material_priority_change": material,
        "top_slice_order_differs": reordered,
    }


def _diff_intervention_vs_reference(
    ref_inv: dict[str, Any],
    other_inv: dict[str, Any],
) -> list[dict[str, Any]]:
    ref_m = _intervention_category_map(ref_inv)
    o_m = _intervention_category_map(other_inv)
    pids = sorted(set(ref_m) | set(o_m))
    rows: list[dict[str, Any]] = []
    for pid in pids:
        a, b = ref_m.get(pid), o_m.get(pid)
        if a == b:
            continue
        rows.append(
            {
                "product_id": pid,
                "reference_category": a if pid in ref_m else None,
                "profile_category": b if pid in o_m else None,
            }
        )
    return rows


def _diff_quiescence_vs_reference(ref_q: dict[str, Any], other_q: dict[str, Any]) -> dict[str, Any]:
    ref_m = set(
        str(x.get("product_id"))
        for x in (ref_q.get("products_with_material_change") or [])
        if isinstance(x, dict)
    )
    o_m = set(
        str(x.get("product_id"))
        for x in (other_q.get("products_with_material_change") or [])
        if isinstance(x, dict)
    )
    return {
        "recommendation_changed": str(ref_q.get("recommendation")) != str(other_q.get("recommendation")),
        "portfolio_quiescent_changed": bool(ref_q.get("portfolio_quiescent")) != bool(other_q.get("portfolio_quiescent")),
        "material_change_product_set_diff": {
            "only_in_reference": sorted(ref_m - o_m),
            "only_in_profile": sorted(o_m - ref_m),
        },
    }


def _assessment_safe_vs_significant(
    *,
    queue_diff: dict[str, Any],
    intervention_diffs: list[dict[str, Any]],
    quiescence_diff: dict[str, Any],
    cycle_changed: bool,
) -> tuple[str, list[str]]:
    codes: list[str] = []
    significant = False
    if cycle_changed:
        significant = True
        codes.append("experiment.significant.cycle_recommendation_changed")
    if intervention_diffs:
        significant = True
        codes.append("experiment.significant.intervention_category_changed")
    if quiescence_diff.get("recommendation_changed") or quiescence_diff.get("portfolio_quiescent_changed"):
        significant = True
        codes.append("experiment.significant.quiescence_interpretation_changed")
    if quiescence_diff.get("material_change_product_set_diff", {}).get("only_in_reference") or quiescence_diff.get(
        "material_change_product_set_diff", {}
    ).get("only_in_profile"):
        significant = True
        codes.append("experiment.significant.quiescence_material_set_changed")
    if queue_diff.get("top_slice_order_differs") or queue_diff.get("products_with_material_priority_change"):
        codes.append("experiment.queue_reordered_or_scores_shifted")
    if significant:
        return "behaviorally_significant", codes
    if queue_diff.get("products_with_material_priority_change") or queue_diff.get("top_slice_order_differs"):
        return "mostly_safe_queue_only", codes
    return "safe_to_try", codes


def _effective_policy_summary(pol: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema": pol.get("schema"),
        "confidence_low_threshold": pol.get("confidence", {}).get("low_threshold"),
        "quiescence": pol.get("quiescence"),
        "intervention_windows": {
            "progression_runs_window": pol.get("intervention", {}).get("progression_runs_window"),
            "delta_reports_window": pol.get("intervention", {}).get("delta_reports_window"),
        },
        "cycle": pol.get("cycle"),
        "queue_weights_version": pol.get("queue_scoring", {}).get("weights_version"),
    }


def evaluate_policy_experiment(
    repo_root: Path,
    *,
    profile_yaml_paths: list[Path] | None = None,
    include_effective: bool = True,
) -> dict[str, Any]:
    """
    Compare operator policy profiles read-only.

    Always includes **default** (frozen builtin). Optionally **effective** (repo config merge).
    Each **profile_yaml_paths** entry is merged over defaults and validated.
    """
    root = repo_root.resolve()
    evaluated_at = datetime.now(timezone.utc).isoformat()
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    profiles: list[dict[str, Any]] = [
        {
            "id": "default",
            "label": "default (builtin)",
            "source": "builtin",
            "policy": default_operator_policy(),
        }
    ]
    if include_effective:
        profiles.append(
            {
                "id": "effective",
                "label": "effective (repo config)",
                "source": "config/operator_policy.yaml",
                "policy": load_operator_policy(root),
            }
        )

    for pth in profile_yaml_paths or []:
        pol = load_policy_from_yaml_file(Path(pth))
        stem = Path(pth).stem
        profiles.append(
            {
                "id": f"file:{stem}",
                "label": f"override:{stem}",
                "source": str(Path(pth).resolve()),
                "policy": pol,
            }
        )

    ref_pol = profiles[0]["policy"]
    score_mat = float(ref_pol["quiescence"]["score_delta_material"])

    per_profile: list[dict[str, Any]] = []
    internals: list[dict[str, Any]] = []
    for spec in profiles:
        pol = spec["policy"]
        queue = build_operator_queue_payload(root, operator_policy=pol)
        if str(queue.get("schema") or "") != OPERATOR_QUEUE_SCHEMA:
            raise ValueError("queue payload schema mismatch")
        quiescence = evaluate_portfolio_quiescence(
            root,
            operator_policy=pol,
            operator_queue_payload=queue,
        )
        delta = evaluate_portfolio_delta_report(
            root,
            operator_policy=pol,
            operator_queue_payload=queue,
        )
        intervention = evaluate_portfolio_intervention(
            root,
            operator_policy=pol,
            operator_queue_payload=queue,
        )
        cycle_rec, cycle_codes = synthesize_overall_operator_recommendation(
            quiescence=quiescence,
            delta=delta,
            intervention=intervention,
            operator_policy=pol,
        )
        internals.append(
            {
                "queue": queue,
                "quiescence": quiescence,
                "intervention": intervention,
            }
        )
        per_profile.append(
            {
                "profile_id": spec["id"],
                "label": spec["label"],
                "source": spec["source"],
                "queue_top_slice": _queue_top_slice(queue),
                "quiescence_recommendation": quiescence.get("recommendation"),
                "quiescence_portfolio_quiescent": quiescence.get("portfolio_quiescent"),
                "delta_recommended_next_portfolio_action": delta.get("recommended_next_portfolio_action"),
                "intervention_flagged_count": len(intervention.get("flagged_products") or []),
                "cycle_overall_operator_recommendation": cycle_rec,
                "cycle_rationale_codes": cycle_codes,
            }
        )

    ref = internals[0]
    recommendation_diffs: list[dict[str, Any]] = []
    priority_changes: list[dict[str, Any]] = []
    intervention_changes: list[dict[str, Any]] = []
    quiescence_interpretation: list[dict[str, Any]] = []
    assessments: list[dict[str, Any]] = []

    for i, spec in enumerate(profiles):
        if i == 0:
            recommendation_diffs.append(
                {
                    "profile_id": spec["id"],
                    "vs_reference": "self",
                    "quiescence_recommendation": per_profile[i]["quiescence_recommendation"],
                    "delta_recommended_action": per_profile[i]["delta_recommended_next_portfolio_action"],
                    "cycle_overall": per_profile[i]["cycle_overall_operator_recommendation"],
                }
            )
            priority_changes.append({"profile_id": spec["id"], "vs_reference": "default_profile", "diff": {}})
            intervention_changes.append({"profile_id": spec["id"], "vs_reference": "default_profile", "diff": []})
            quiescence_interpretation.append(
                {"profile_id": spec["id"], "vs_reference": "default_profile", "diff": {}}
            )
            assessments.append(
                {
                    "profile_id": spec["id"],
                    "assessment": "reference",
                    "codes": ["experiment.reference_profile"],
                }
            )
            continue

        cur = internals[i]
        qd = _diff_queue_vs_reference(ref["queue"], cur["queue"], score_delta_material=score_mat)
        priority_changes.append({"profile_id": spec["id"], "vs_reference": "default", "diff": qd})

        idiff = _diff_intervention_vs_reference(ref["intervention"], cur["intervention"])
        intervention_changes.append(
            {
                "profile_id": spec["id"],
                "vs_reference": "default",
                "products_with_category_change": idiff,
            }
        )

        qdiff = _diff_quiescence_vs_reference(ref["quiescence"], cur["quiescence"])
        quiescence_interpretation.append(
            {
                "profile_id": spec["id"],
                "vs_reference": "default",
                "diff": qdiff,
            }
        )

        rec_diff = {
            "quiescence_recommendation": per_profile[i]["quiescence_recommendation"],
            "delta_recommended_action": per_profile[i]["delta_recommended_next_portfolio_action"],
            "cycle_overall": per_profile[i]["cycle_overall_operator_recommendation"],
            "vs_default_reference": {
                "quiescence_changed": per_profile[i]["quiescence_recommendation"]
                != per_profile[0]["quiescence_recommendation"],
                "delta_changed": per_profile[i]["delta_recommended_next_portfolio_action"]
                != per_profile[0]["delta_recommended_next_portfolio_action"],
                "cycle_changed": per_profile[i]["cycle_overall_operator_recommendation"]
                != per_profile[0]["cycle_overall_operator_recommendation"],
            },
        }
        recommendation_diffs.append(
            {
                "profile_id": spec["id"],
                "vs_reference": "default",
                **rec_diff,
            }
        )

        assess, acodes = _assessment_safe_vs_significant(
            queue_diff=qd,
            intervention_diffs=idiff,
            quiescence_diff=qdiff,
            cycle_changed=bool(rec_diff["vs_default_reference"].get("cycle_changed")),
        )
        assessments.append({"profile_id": spec["id"], "assessment": assess, "codes": acodes})

    effective_summaries = {
        spec["id"]: _effective_policy_summary(spec["policy"]) for spec in profiles
    }

    return {
        "schema": OPERATOR_POLICY_EXPERIMENT_SCHEMA,
        "run_id": run_id,
        "evaluated_at_utc": evaluated_at,
        "read_only": True,
        "note": "No portfolio progression, orchestration advancement, or queue writes; in-memory evaluation only.",
        "compared_profiles": [{"id": s["id"], "label": s["label"], "source": s["source"]} for s in profiles],
        "effective_policy_summaries": effective_summaries,
        "per_profile_results": per_profile,
        "recommendation_differences": recommendation_diffs,
        "products_priority_changes_vs_default": priority_changes,
        "products_intervention_category_changes_vs_default": intervention_changes,
        "quiescence_interpretation_changes_vs_default": quiescence_interpretation,
        "assessments": assessments,
    }


def render_policy_experiment_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Operator policy experiment",
        "",
        f"**Schema:** `{payload.get('schema')}`",
        f"**Run id:** `{payload.get('run_id')}`",
        f"**Evaluated (UTC):** {payload.get('evaluated_at_utc')}",
        "",
        payload.get("note", ""),
        "",
        "## Profiles compared",
        "",
    ]
    for c in payload.get("compared_profiles") or []:
        if isinstance(c, dict):
            lines.append(f"- **`{c.get('id')}`** — {c.get('label')} (`{c.get('source')}`)")
    lines.extend(["", "## Headline by profile", ""])
    for row in payload.get("per_profile_results") or []:
        if not isinstance(row, dict):
            continue
        lines.append(f"### `{row.get('profile_id')}`")
        lines.append("")
        lines.append(str(row.get("label")))
        lines.append("")
        lines.append(
            f"- **Quiescence:** `{row.get('quiescence_recommendation')}` · "
            f"portfolio_quiescent={row.get('quiescence_portfolio_quiescent')}"
        )
        lines.append(f"- **Delta:** `{row.get('delta_recommended_next_portfolio_action')}`")
        lines.append(f"- **Intervention flagged:** {row.get('intervention_flagged_count')}")
        lines.append(f"- **Cycle synthesis:** `{row.get('cycle_overall_operator_recommendation')}`")
        lines.append("")
    lines.extend(["## Top queue slice (first products)", ""])
    for row in payload.get("per_profile_results") or []:
        if not isinstance(row, dict):
            continue
        lines.append(f"### `{row.get('profile_id')}`")
        lines.append("")
        lines.append("| Rank | Product | Score | Tier |")
        lines.append("|------|---------|-------|------|")
        for e in row.get("queue_top_slice") or []:
            if isinstance(e, dict):
                lines.append(
                    f"| {e.get('queue_rank')} | `{e.get('product_id')}` | {e.get('priority_score')} | "
                    f"`{e.get('readiness_tier')}` |"
                )
        lines.append("")

    lines.extend(["## vs default — intervention category changes", ""])
    for block in payload.get("products_intervention_category_changes_vs_default") or []:
        if not isinstance(block, dict):
            continue
        if block.get("profile_id") == "default":
            continue
        dif = block.get("products_with_category_change") or []
        lines.append(f"### `{block.get('profile_id')}`")
        lines.append("")
        if not dif:
            lines.append("—")
        else:
            for r in dif:
                if isinstance(r, dict):
                    lines.append(
                        f"- `{r.get('product_id')}`: {r.get('reference_category')} → {r.get('profile_category')}"
                    )
        lines.append("")

    lines.extend(["## vs default — quiescence interpretation", ""])
    for block in payload.get("quiescence_interpretation_changes_vs_default") or []:
        if not isinstance(block, dict) or block.get("profile_id") == "default":
            continue
        d = block.get("diff") or {}
        lines.append(f"### `{block.get('profile_id')}`")
        lines.append("")
        lines.append(f"- recommendation_changed: {d.get('recommendation_changed')}")
        lines.append(f"- portfolio_quiescent_changed: {d.get('portfolio_quiescent_changed')}")
        lines.append(f"- material_change_set: {d.get('material_change_product_set_diff')}")
        lines.append("")

    lines.extend(["## Safe vs significant", ""])
    for a in payload.get("assessments") or []:
        if not isinstance(a, dict):
            continue
        lines.append(f"- **`{a.get('profile_id')}`:** {a.get('assessment')} — {', '.join(a.get('codes') or [])}")
    lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def write_policy_experiment_artifacts(
    repo_root: Path,
    payload: dict[str, Any],
    *,
    run_id: str | None = None,
) -> tuple[Path, Path, Path, Path]:
    root = repo_root.resolve()
    rid = run_id or str(payload.get("run_id") or "")
    if not rid:
        rid = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    pl = copy.deepcopy(payload)
    pl["run_id"] = rid
    d = policy_experiment_output_dir(root)
    d.mkdir(parents=True, exist_ok=True)
    stamped_json = d / f"{rid}.json"
    stamped_md = d / f"{rid}.md"
    latest_json = d / "latest.json"
    latest_md = d / "latest.md"
    stamped_json.write_text(dumps_json(pl) + "\n", encoding="utf-8")
    stamped_md.write_text(render_policy_experiment_markdown(pl), encoding="utf-8")
    shutil.copyfile(stamped_json, latest_json)
    shutil.copyfile(stamped_md, latest_md)
    return stamped_json, stamped_md, latest_json, latest_md


def run_policy_experiment(
    repo_root: Path,
    *,
    profile_yaml_paths: list[Path] | None = None,
    include_effective: bool = True,
    write_artifacts: bool = True,
) -> dict[str, Any]:
    payload = evaluate_policy_experiment(
        repo_root,
        profile_yaml_paths=profile_yaml_paths,
        include_effective=include_effective,
    )
    if write_artifacts:
        write_policy_experiment_artifacts(repo_root, payload)
    return payload
