"""
Read-only mission experiments — compare operator-facing behavior under alternate mission profiles.

Does not write progression, orchestration, or mission truth artifacts. Uses
:func:`argus.mission.mission.mission_experiment_scope` so overrides never leak outside evaluation.
"""

from __future__ import annotations

import copy
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from argus.core.serialize import dumps_json
from argus.dashboard.operator_summary import evaluate_operator_summary
from argus.mission.mission import MissionExperimentComposition, mission_experiment_scope
from argus.mission.provenance import build_portfolio_mission_provenance
from argus.policy.operator_policy import load_operator_policy
from argus.policy.recommendations import evaluate_operator_policy_recommendations
from argus.portfolio.intervention import evaluate_portfolio_intervention
from argus.portfolio.operator_queue import OPERATOR_QUEUE_SCHEMA, build_operator_queue_payload
from argus.portfolio.quiescence import evaluate_portfolio_quiescence
from argus.products.inventory import build_inventory

MISSION_EXPERIMENT_SCHEMA = "argus.mission_experiment.v2"
TOP_SLICE_N = 12

ExperimentMode = Literal["sweep_all", "sweep_mixed", "targeted"]
ExperimentVariantKind = Literal["profiles", "compositions"]


def parse_mission_composition_experiment_arg(raw: str) -> MissionExperimentComposition:
    """
    Parse a compact composition string for CLI / APIs.

    Format (space-separated ``key=value`` tokens)::

        objective=revenue drivers=engagement,retention guardrails=education,engagement

    ``objective`` is required; ``drivers`` and ``guardrails`` are comma-separated **mission profile ids**
    from the registry (same ids as ``product.yaml`` structured ``mission:``).
    """
    s = str(raw or "").strip()
    if not s:
        raise ValueError("empty mission composition string")
    found: dict[str, str] = {}
    for m in re.finditer(r"\b(objective|drivers|guardrails)\s*=\s*([^\s]+)", s, flags=re.IGNORECASE):
        key = m.group(1).lower()
        found[key] = m.group(2).strip()

    obj = found.get("objective")
    if not obj:
        raise ValueError("mission composition requires objective=<mission_profile_id>")

    def _split_ids(key: str) -> tuple[str, ...]:
        part = found.get(key)
        if not part:
            return ()
        return tuple(x.strip() for x in part.split(",") if x.strip())

    dr = _split_ids("drivers")
    gr = _split_ids("guardrails")
    return MissionExperimentComposition(objective=obj, drivers=dr, guardrails=gr)


def mission_experiment_output_dir(repo_root: Path) -> Path:
    return Path(repo_root).resolve() / "runs" / "mission" / "experiments"


def _validate_profiles(repo_root: Path, profile_ids: list[str]) -> None:
    from argus.mission.mission import load_mission_by_id

    for mid in profile_ids:
        load_mission_by_id(repo_root, str(mid).strip())


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


def _policy_operator_snippet(pol: dict[str, Any]) -> dict[str, Any]:
    mi = pol.get("mission_integration") or {}
    return {
        "confidence_low_threshold": (pol.get("confidence") or {}).get("low_threshold"),
        "quiescence": pol.get("quiescence"),
        "intervention_windows": {
            "progression_runs_window": (pol.get("intervention") or {}).get("progression_runs_window"),
            "delta_reports_window": (pol.get("intervention") or {}).get("delta_reports_window"),
            "stagnation_min_delta_reports": (pol.get("intervention") or {}).get(
                "stagnation_min_delta_reports"
            ),
        },
        "resolved_mission_id": mi.get("resolved_mission_id"),
        "risk_posture": mi.get("risk_posture"),
        "adjustments_applied_count": len(mi.get("adjustments_applied") or []),
    }


def _intervention_category_map(payload: dict[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    for row in payload.get("flagged_products") or []:
        if not isinstance(row, dict):
            continue
        pid = str(row.get("product_id") or "").strip()
        if pid:
            out[pid] = str(row.get("intervention_category") or "")
    return out


def _scope_for_profile(
    *,
    profile_id: str,
    all_inventory: list[str],
    product_ids_filter: list[str] | None,
    mode: ExperimentMode,
) -> tuple[dict[str, str], str | None]:
    p = str(profile_id).strip()
    inv_set = set(all_inventory)
    if mode == "sweep_all":
        if product_ids_filter:
            ids = sorted(inv_set & set(product_ids_filter))
        else:
            ids = sorted(inv_set)
        return {pid: p for pid in ids}, p
    if mode == "sweep_mixed":
        assert product_ids_filter
        ids = sorted(inv_set & set(product_ids_filter))
        return {pid: p for pid in ids}, None
    assert product_ids_filter
    ids = sorted(inv_set & set(product_ids_filter))
    return {pid: p for pid in ids}, p


def _scope_for_composition(
    *,
    composition: MissionExperimentComposition,
    all_inventory: list[str],
    product_ids_filter: list[str] | None,
    mode: ExperimentMode,
) -> tuple[dict[str, MissionExperimentComposition], str | None]:
    inv_set = set(all_inventory)
    oid = str(composition.objective).strip()
    if mode == "sweep_all":
        if product_ids_filter:
            ids = sorted(inv_set & set(product_ids_filter))
        else:
            ids = sorted(inv_set)
        return {pid: composition for pid in ids}, oid
    if mode == "sweep_mixed":
        assert product_ids_filter
        ids = sorted(inv_set & set(product_ids_filter))
        return {pid: composition for pid in ids}, None
    assert product_ids_filter
    ids = sorted(inv_set & set(product_ids_filter))
    return {pid: composition for pid in ids}, oid


def _evaluate_under_scope(
    repo_root: Path,
    *,
    product_overrides: dict[str, str] | None = None,
    product_compositions: dict[str, MissionExperimentComposition] | None = None,
    global_mission_id: str | None,
    products_for_provenance: list[str],
) -> dict[str, Any]:
    root = repo_root.resolve()
    with mission_experiment_scope(
        product_mission_overrides=product_overrides,
        product_mission_compositions=product_compositions,
        global_mission_id=global_mission_id,
    ):
        pol_global = load_operator_policy(root)
        queue = build_operator_queue_payload(root)
        if str(queue.get("schema") or "") != OPERATOR_QUEUE_SCHEMA:
            raise ValueError("operator queue schema mismatch")
        quiescence = evaluate_portfolio_quiescence(
            root,
            operator_policy=pol_global,
            operator_queue_payload=queue,
        )
        intervention = evaluate_portfolio_intervention(
            root,
            operator_policy=pol_global,
            operator_queue_payload=queue,
        )
        recommendations = evaluate_operator_policy_recommendations(root)
        summary = evaluate_operator_summary(
            root,
            operator_queue_override=queue,
            portfolio_quiescence_override=quiescence,
        )
        per_pol: dict[str, dict[str, Any]] = {}
        for pid in products_for_provenance:
            per_pol[pid] = _policy_operator_snippet(load_operator_policy(root, product_id=pid))
        mission_mix = build_portfolio_mission_provenance(root, products_for_provenance)
    return {
        "operator_policy_global": pol_global,
        "operator_queue": queue,
        "quiescence": quiescence,
        "intervention": intervention,
        "policy_recommendations": recommendations,
        "operator_summary": summary,
        "per_product_operator_policy": per_pol,
        "mission_mix_used": mission_mix,
    }


def _diff_policy_snippets(
    ref: dict[str, Any], other: dict[str, Any], *, label: str
) -> dict[str, Any]:
    deltas: dict[str, tuple[Any, Any]] = {}
    for k in set(ref) | set(other):
        if ref.get(k) != other.get(k):
            deltas[k] = (ref.get(k), other.get(k))
    return {"reference": label, "fields_changed": sorted(deltas.keys()), "values": {k: list(v) for k, v in deltas.items()}}


def evaluate_mission_experiment(
    repo_root: Path,
    compared_profiles: list[str] | None = None,
    *,
    compared_compositions: list[MissionExperimentComposition] | None = None,
    product_ids: list[str] | None = None,
    mode: ExperimentMode = "sweep_all",
) -> dict[str, Any]:
    """
    Compare read-only operator layers under mission profile ids and/or **structured compositions**
    (objective + driver + guardrail overlays from the mission registry).

    Pass **either** ``compared_profiles`` **or** ``compared_compositions``, not both.

    * ``mode=sweep_all`` — force each variant for all products (or ``product_ids`` filter); aligns
      global mission to each variant's **objective** for portfolio-wide policy consumers.
    * ``mode=sweep_mixed`` — force variants only for ``product_ids``; repository global mission unchanged.
    * ``mode=targeted`` — ``product_ids`` required; aligns global to each variant's objective.
    """
    root = repo_root.resolve()
    evaluated_at = datetime.now(timezone.utc).isoformat()
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    pids_arg = [str(x).strip() for x in (product_ids or []) if str(x).strip()]

    comps_in = list(compared_compositions or [])
    profs_in = [str(x).strip() for x in (compared_profiles or []) if str(x).strip()]
    if comps_in and profs_in:
        raise ValueError("pass either compared_profiles or compared_compositions, not both")
    if comps_in:
        variant_kind: ExperimentVariantKind = "compositions"
        for c in comps_in:
            c.validate_registry(root)
        variant_labels = [c.label() for c in comps_in]
    elif profs_in:
        variant_kind = "profiles"
        _validate_profiles(root, profs_in)
        variant_labels = list(profs_in)
    else:
        raise ValueError("compared_profiles or compared_compositions must be non-empty")

    inv = build_inventory(root)
    all_inventory = sorted(inv.valid.keys())
    if not all_inventory:
        raise ValueError("no valid products in inventory")

    if mode == "targeted" and not pids_arg:
        raise ValueError("targeted mode requires product_ids")
    if mode == "sweep_mixed" and not pids_arg:
        raise ValueError("sweep_mixed mode requires product_ids")

    products_evaluated = sorted(set(all_inventory) & set(pids_arg)) if pids_arg else list(all_inventory)

    per_profile: list[dict[str, Any]] = []
    raw_list: list[dict[str, Any]] = []

    n_variants = len(variant_labels)
    for i in range(n_variants):
        if variant_kind == "profiles":
            mid = profs_in[i]
            ovr, glob = _scope_for_profile(
                profile_id=mid,
                all_inventory=all_inventory,
                product_ids_filter=pids_arg if pids_arg else None,
                mode=mode,
            )
            if not ovr:
                raise ValueError(f"no products in scope for mission experiment profile {mid!r}")
            bundle = _evaluate_under_scope(
                root,
                product_overrides=ovr,
                product_compositions=None,
                global_mission_id=glob,
                products_for_provenance=products_evaluated,
            )
            label = mid
            comp_payload: dict[str, Any] | None = None
        else:
            comp = comps_in[i]
            ovr, glob = _scope_for_composition(
                composition=comp,
                all_inventory=all_inventory,
                product_ids_filter=pids_arg if pids_arg else None,
                mode=mode,
            )
            if not ovr:
                raise ValueError(f"no products in scope for mission experiment composition {comp.label()!r}")
            bundle = _evaluate_under_scope(
                root,
                product_overrides=None,
                product_compositions=ovr,
                global_mission_id=glob,
                products_for_provenance=products_evaluated,
            )
            label = comp.label()
            comp_payload = {
                "objective": comp.objective,
                "drivers": list(comp.drivers),
                "guardrails": list(comp.guardrails),
            }

        raw_list.append(bundle)
        pq = bundle["policy_recommendations"]
        rec_ids = [
            str(r.get("recommendation_id"))
            for r in (pq.get("recommendations") or [])
            if isinstance(r, dict) and r.get("recommendation_id")
        ]
        per_profile.append(
            {
                "profile_id": label,
                "composition": comp_payload,
                "mission_mix_used": bundle["mission_mix_used"],
                "operator_policy_global": _policy_operator_snippet(bundle["operator_policy_global"]),
                "per_product_operator_policy": bundle["per_product_operator_policy"],
                "queue_top_slice": _queue_top_slice(bundle["operator_queue"]),
                "quiescence": {
                    "recommendation": (bundle["quiescence"] or {}).get("recommendation"),
                    "portfolio_quiescent": (bundle["quiescence"] or {}).get("portfolio_quiescent"),
                    "reason_codes_head": list((bundle["quiescence"] or {}).get("reason_codes") or [])[:24],
                },
                "intervention": {
                    "flagged_count": len((bundle["intervention"] or {}).get("flagged_products") or []),
                    "categories_by_product": _intervention_category_map(bundle["intervention"] or {}),
                },
                "policy_recommendation_ids": rec_ids,
                "operator_summary": {
                    "headline_status": (bundle["operator_summary"] or {}).get("headline_status"),
                    "confidence_level": (bundle["operator_summary"] or {}).get("confidence_level"),
                    "recommended_next_step": (bundle["operator_summary"] or {}).get("recommended_next_step"),
                },
            }
        )

    ref_id = variant_labels[0]
    ref = raw_list[0]
    policy_deltas: list[dict[str, Any]] = []
    recommendation_deltas: list[dict[str, Any]] = []
    intervention_deltas: list[dict[str, Any]] = []
    queue_order_diffs: list[dict[str, Any]] = []

    ref_pol = _policy_operator_snippet(ref["operator_policy_global"])
    ref_rec = {
        str(r.get("recommendation_id"))
        for r in (ref["policy_recommendations"].get("recommendations") or [])
        if isinstance(r, dict) and r.get("recommendation_id")
    }
    ref_q_order = [str(e.get("product_id")) for e in (ref["operator_queue"].get("entries") or [])[:TOP_SLICE_N]]
    ref_iv = _intervention_category_map(ref["intervention"])

    for idx in range(1, len(variant_labels)):
        mid = variant_labels[idx]
        oth = raw_list[idx]
        o_pol = _policy_operator_snippet(oth["operator_policy_global"])
        policy_deltas.append(_diff_policy_snippets(ref_pol, o_pol, label=f"{ref_id}_vs_{mid}"))

        o_rec = {
            str(r.get("recommendation_id"))
            for r in (oth["policy_recommendations"].get("recommendations") or [])
            if isinstance(r, dict) and r.get("recommendation_id")
        }
        recommendation_deltas.append(
            {
                "profiles": [ref_id, mid],
                "only_in_first": sorted(ref_rec - o_rec),
                "only_in_second": sorted(o_rec - ref_rec),
            }
        )

        o_iv = _intervention_category_map(oth["intervention"])
        pids_d = sorted(set(ref_iv) | set(o_iv))
        iv_rows = []
        for pid in pids_d:
            a, b = ref_iv.get(pid), o_iv.get(pid)
            if a != b:
                iv_rows.append({"product_id": pid, "first": a, "second": b})
        intervention_deltas.append({"profiles": [ref_id, mid], "category_changes": iv_rows})

        o_q_order = [str(e.get("product_id")) for e in (oth["operator_queue"].get("entries") or [])[:TOP_SLICE_N]]
        queue_order_diffs.append(
            {
                "profiles": [ref_id, mid],
                "top_slice_order_equal": ref_q_order == o_q_order,
                "first_order": ref_q_order,
                "second_order": o_q_order,
            }
        )

    quiescence_deltas: list[dict[str, Any]] = []
    summary_deltas: list[dict[str, Any]] = []
    for idx in range(1, len(variant_labels)):
        mid = variant_labels[idx]
        oth = raw_list[idx]
        rq, oq = ref["quiescence"], oth["quiescence"]
        quiescence_deltas.append(
            {
                "profiles": [ref_id, mid],
                "recommendation_changed": (rq.get("recommendation") != oq.get("recommendation")),
                "portfolio_quiescent_changed": bool(rq.get("portfolio_quiescent"))
                != bool(oq.get("portfolio_quiescent")),
            }
        )
        rs, os_ = ref["operator_summary"], oth["operator_summary"]
        summary_deltas.append(
            {
                "profiles": [ref_id, mid],
                "headline_changed": rs.get("headline_status") != os_.get("headline_status"),
                "next_step_changed": rs.get("recommended_next_step") != os_.get("recommended_next_step"),
            }
        )

    layers = {
        "operator_policy_global": ref_pol,
        "policy_recommendation_ids": sorted(ref_rec),
        "queue_top_order": ref_q_order,
        "intervention_categories": ref_iv,
        "quiescence": {
            "recommendation": ref["quiescence"].get("recommendation"),
            "portfolio_quiescent": ref["quiescence"].get("portfolio_quiescent"),
        },
        "operator_summary_headline": ref["operator_summary"].get("headline_status"),
    }
    unchanged: list[str] = []
    if len(variant_labels) < 2:
        unchanged.append("cross_profile_comparison (single profile — no deltas)")
    else:
        if not any(d.get("fields_changed") for d in policy_deltas):
            unchanged.append("operator_policy_global (vs first profile)")
        if not any(d.get("only_in_first") or d.get("only_in_second") for d in recommendation_deltas):
            unchanged.append("policy_recommendation_ids (vs first profile)")
        if not any(d.get("category_changes") for d in intervention_deltas):
            unchanged.append("intervention_categories (vs first profile)")
        if not any(not d.get("top_slice_order_equal") for d in queue_order_diffs):
            unchanged.append("queue_top_slice_order (vs first profile)")
        if not any(d.get("recommendation_changed") or d.get("portfolio_quiescent_changed") for d in quiescence_deltas):
            unchanged.append("quiescence_interpretation (vs first profile)")
        if not any(d.get("headline_changed") or d.get("next_step_changed") for d in summary_deltas):
            unchanged.append("operator_summary_headline_or_next_step (vs first profile)")

    behavior_notes: list[str] = []
    if len(variant_labels) > 1:
        if policy_deltas and any(d.get("fields_changed") for d in policy_deltas):
            if variant_kind == "compositions":
                behavior_notes.append(
                    "Structured mission compositions merge objective, driver overlays, and guardrail "
                    "constraints into operator policy (global and per-product)."
                )
            else:
                behavior_notes.append("Mission profile changes merge into operator policy (global and per-product).")
        if queue_order_diffs and any(not x.get("top_slice_order_equal") for x in queue_order_diffs):
            behavior_notes.append("Queue ranking changed materially under alternate missions (per-product scoring).")
        if recommendation_deltas and any(d.get("only_in_first") or d.get("only_in_second") for d in recommendation_deltas):
            behavior_notes.append("Heuristic policy recommendations differed — mission-conditioned thresholds affect proposals.")

    compared_compositions_serialized: list[dict[str, Any]] | None = None
    if variant_kind == "compositions":
        compared_compositions_serialized = [
            {"objective": c.objective, "drivers": list(c.drivers), "guardrails": list(c.guardrails)}
            for c in comps_in
        ]

    return {
        "schema": MISSION_EXPERIMENT_SCHEMA,
        "run_id": run_id,
        "evaluated_at_utc": evaluated_at,
        "read_only": True,
        "note": (
            "No portfolio progression, orchestration advancement, or truth-layer writes; "
            "mission overrides apply only inside mission_experiment_scope."
        ),
        "experiment_mode": mode,
        "experiment_variant_kind": variant_kind,
        "compared_profiles": variant_labels,
        "compared_compositions": compared_compositions_serialized,
        "products_evaluated": products_evaluated,
        "reference_profile": ref_id,
        "per_profile": per_profile,
        "policy_deltas_vs_reference": policy_deltas,
        "recommendation_deltas_vs_reference": recommendation_deltas,
        "intervention_deltas_vs_reference": intervention_deltas,
        "quiescence_deltas_vs_reference": quiescence_deltas,
        "queue_top_slice_order_deltas_vs_reference": queue_order_diffs,
        "operator_summary_deltas_vs_reference": summary_deltas,
        "narrative": {
            "note": (
                "Operator narrative is artifact-history-driven and not recomputed per mission in this experiment; "
                "use queue, policy, quiescence, and intervention slices for mission-conditioned behavior."
            )
        },
        "behavior_change_summary": behavior_notes,
        "areas_no_meaningful_change_vs_reference": unchanged,
        "reference_layer_fingerprints": layers,
    }


def render_mission_experiment_markdown(payload: dict[str, Any]) -> str:
    vk = payload.get("experiment_variant_kind") or "profiles"
    comp_lines = ""
    if vk == "compositions" and payload.get("compared_compositions"):
        comp_lines = "\n**Compared compositions (structured):**\n"
        for i, c in enumerate(payload["compared_compositions"] or [], start=1):
            if not isinstance(c, dict):
                continue
            comp_lines += (
                f"{i}. objective=`{c.get('objective')}` "
                f"drivers={c.get('drivers') or []} "
                f"guardrails={c.get('guardrails') or []}\n"
            )
    lines = [
        "# Mission experiment",
        "",
        f"**Schema:** `{payload.get('schema')}`",
        f"**Run id:** `{payload.get('run_id')}`",
        f"**Evaluated (UTC):** {payload.get('evaluated_at_utc')}",
        "",
        str(payload.get("note") or ""),
        "",
        f"**Mode:** `{payload.get('experiment_mode')}`",
        f"**Variant kind:** `{vk}`",
        f"**Compared variants:** {', '.join(payload.get('compared_profiles') or [])}",
        f"**Products evaluated:** {', '.join(payload.get('products_evaluated') or [])}",
        comp_lines.rstrip(),
        "",
        "## Behavior change summary",
        "",
    ]
    for n in payload.get("behavior_change_summary") or []:
        lines.append(f"- {n}")
    if not payload.get("behavior_change_summary"):
        lines.append("— *No cross-profile deltas (single profile or no material differences).*")
    lines.extend(["", "## Areas with no meaningful change vs reference", ""])
    for n in payload.get("areas_no_meaningful_change_vs_reference") or []:
        lines.append(f"- {n}")
    lines.extend(["", "## Per variant — queue top slice", ""])
    for row in payload.get("per_profile") or []:
        if not isinstance(row, dict):
            continue
        lines.append(f"### `{row.get('profile_id')}`")
        cm = row.get("composition")
        if isinstance(cm, dict) and cm.get("objective"):
            lines.append(
                f"*objective* `{cm.get('objective')}` · *drivers* `{cm.get('drivers')}` · "
                f"*guardrails* `{cm.get('guardrails')}`"
            )
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
    lines.extend(["## Narrative", "", str((payload.get('narrative') or {}).get('note') or ''), ""])
    return "\n".join(lines).rstrip() + "\n"


def write_mission_experiment_artifacts(
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
    d = mission_experiment_output_dir(root)
    d.mkdir(parents=True, exist_ok=True)
    stamped_json = d / f"{rid}.json"
    stamped_md = d / f"{rid}.md"
    latest_json = d / "latest.json"
    latest_md = d / "latest.md"
    stamped_json.write_text(dumps_json(pl) + "\n", encoding="utf-8")
    stamped_md.write_text(render_mission_experiment_markdown(pl), encoding="utf-8")
    shutil.copyfile(stamped_json, latest_json)
    shutil.copyfile(stamped_md, latest_md)
    return stamped_json, stamped_md, latest_json, latest_md


def run_mission_experiment(
    repo_root: Path,
    compared_profiles: list[str] | None = None,
    *,
    compared_compositions: list[MissionExperimentComposition] | None = None,
    product_ids: list[str] | None = None,
    mode: ExperimentMode = "sweep_all",
    write_artifacts: bool = True,
) -> dict[str, Any]:
    payload = evaluate_mission_experiment(
        repo_root,
        compared_profiles,
        compared_compositions=compared_compositions,
        product_ids=product_ids,
        mode=mode,
    )
    if write_artifacts:
        write_mission_experiment_artifacts(repo_root, payload)
    return payload
