"""In-process execution of a single orchestration action_id (deterministic; no LLM; no worker)."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from argus.adapters.loader import load_adapter_config
from argus.audit.cache import run_audit
from argus.core.serialize import dumps_json
from argus.experiments.execution_apply import (
    apply_execution_outcomes,
    pending_unapplied_execution_outcomes_for_product,
)
from argus.orchestrator.artifact_snapshot import (
    list_refinement_sessions_for_product,
    pick_latest_session,
)
from argus.orchestrator.eligibility import evaluate_product_orchestration
from argus.orchestrator.execution_feedback import write_orchestration_execution_feedback
from argus.orchestrator.orchestration_phase1 import (
    ORCHESTRATION_PHASE1_MAPPING_REF,
    action_description_for_orchestration,
    phase1_keys_for_orchestration_action,
    validate_orchestration_registry_phase1_mapping,
)
from argus.orchestrator.phase1_execution_detail import (
    embed_phase1_blocked,
    embed_phase1_evaluated,
    embed_phase1_not_evaluated,
)
from argus.orchestrator.state_models import (
    ACTION_AUDIT_RUN,
    ACTION_DECISIONS_GENERATE,
    ACTION_DECISIONS_REFRESH_FROM_SURFACED_FINDINGS,
    ACTION_ESCALATION_CONSIDER,
    ACTION_ESCALATION_PACKET_GENERATE,
    ACTION_EXECUTION_OUTCOMES_APPLY,
    ACTION_EXPERIMENTS_ACTIVATE,
    ACTION_EXPERIMENTS_CLOSE_STALE,
    ACTION_EXPERIMENTS_CREATE,
    ACTION_EXPERIMENTS_EVALUATE,
    ACTION_EXPERIMENTS_PRIORITIZE,
    ACTION_EXPERIMENTS_PROPOSE,
    ACTION_EXPERIMENTS_SURFACE_FINDINGS,
    ACTION_FINDINGS_GENERATE,
    ACTION_IDEAS_GENERATE,
    ACTION_IDEAS_REFRESH_FROM_SURFACED_FINDINGS,
    ACTION_IMPLEMENTATION_PLAN_GENERATE,
    ACTION_ORCHESTRATION_STATE_REFRESH,
    ACTION_PLANNING_REFRESH_FROM_STRATEGY,
    ACTION_REFINEMENT_RUN,
    ACTION_REFINEMENT_START_IDEA,
    ACTION_REFINEMENT_START_PRODUCT_SPEC,
    ACTION_REFINEMENT_SUBMIT_REVIEWS_IN,
    ACTION_SIGNALS_COLLECT,
    ACTION_STATUS_BLOCKED,
    ACTION_STATUS_EXECUTED,
    ACTION_STATUS_FAILED,
    ACTION_STATUS_QUEUED_UNHANDLED,
    ACTION_STRATEGY_REFRESH_FROM_DECISION_EVOLUTION,
    ACTION_TEMPORAL_REFRESH,
    ORCHESTRATION_ESCALATION_CONSIDER_ARTIFACT_SCHEMA,
)
from argus.orchestrator.state_pass import write_orchestration_state
from argus.products.external_bindings import parse_external_bindings
from argus.products.inventory import build_inventory
from argus.refinement.models import ArtifactType
from argus.refinement.persistence import read_json
from argus.refinement.queries import (
    refinement_reviews_in_missing,
    reviews_in_round_path,
    session_json_path,
)
from argus.refinement.session import create_session, run_refinement_cycle
from argus.signals.adapters import default_builtin_adapters
from argus.signals.persistence import save_collection
from argus.signals.registry import AdapterRegistry
from argus.signals.runner import collect_for_product, product_root_path


def _merge_adapter_layer(repo_root: Path) -> bool:
    cfg = load_adapter_config(repo_root)
    sc = cfg.get("signals_collect") or {}
    return isinstance(sc, dict) and bool(sc.get("merge_adapter_layer"))


def _registry() -> AdapterRegistry:
    return AdapterRegistry(default_builtin_adapters())


_REFINEMENT_RUN_SESSION_RE = re.compile(r"session (ref_[A-Za-z0-9_]+)")


def _session_id_for_refinement_run(state: dict[str, Any]) -> str | None:
    """Match orchestration ``eligible_actions`` reason text to a session id."""
    for row in state.get("eligible_actions") or []:
        if not isinstance(row, dict):
            continue
        if str(row.get("action_id") or "") != ACTION_REFINEMENT_RUN:
            continue
        m = _REFINEMENT_RUN_SESSION_RE.search(str(row.get("reason") or ""))
        if m:
            return m.group(1)
    return None


def _invalid_product_failure(product_id: str) -> dict[str, Any]:
    """Return the standard failed result when ``product_id`` is missing from inventory."""
    return {
        "action_status": ACTION_STATUS_FAILED,
        "execution_detail": embed_phase1_not_evaluated(
            {"product_id": product_id},
            reason="product not in inventory — Phase 1 policy not evaluated",
        ),
        "execution_error": f"unknown or invalid product: {product_id!r}",
    }


def execute_orchestration_action(
    repo_root: Path | str,
    product_id: str,
    action_id: str,
) -> dict[str, Any]:
    """
    Run the in-process handler for ``action_id`` when one exists.

    Writes ``argus.orchestration_execution_feedback.v1`` under ``runs/execution/<product_id>/``
    so a later ``signals collect`` can ingest them as execution-outcome signals.

    Phase 1 project permissions are enforced here (before handlers). Each evaluation writes
    ``argus.project_permission_decision.v1`` under ``runs/policy/phase1_decisions/<product_id>/``.

    Returns a dict with:
    - ``action_status``: ``executed``, ``failed``, ``blocked``, or ``queued_unhandled``
    - ``execution_detail``: JSON-serializable summary (always a dict)
    - ``execution_error``: error string or ``None``
    """
    from argus.project_permissions.audit import write_phase1_permission_decision
    from argus.project_permissions.gate import evaluate_phase1_for_keys

    root = Path(repo_root).resolve()
    pid = str(product_id).strip()
    aid = str(action_id).strip()
    fn = STEP_EXECUTION_REGISTRY.get(aid)
    if fn is not None:
        inv = build_inventory(root)
        if pid not in inv.valid:
            result = _invalid_product_failure(pid)
            write_orchestration_execution_feedback(root, pid, aid, result)
            return result
        orch_keys = phase1_keys_for_orchestration_action(aid)
        if orch_keys is None:
            result = {
                "action_status": ACTION_STATUS_FAILED,
                "execution_detail": embed_phase1_not_evaluated(
                    {
                        "reason": "orchestration action_id missing Phase 1 key mapping (invariant violation)",
                        "action_id": aid,
                        "fix": f"add entry to {ORCHESTRATION_PHASE1_MAPPING_REF}",
                        "mapping_table": ORCHESTRATION_PHASE1_MAPPING_REF,
                    },
                    reason="internal: no Phase 1 key mapping — policy not evaluated",
                ),
                "execution_error": (
                    f"Unmapped orchestration action: {aid!r}. "
                    f"Add mapping in {ORCHESTRATION_PHASE1_MAPPING_REF}."
                ),
            }
            write_orchestration_execution_feedback(root, pid, aid, result)
            return result
        desc = action_description_for_orchestration(aid)
        ev = evaluate_phase1_for_keys(
            root,
            pid,
            orch_keys,
            check_environment=False,
            execution_path="orchestration_step_executor",
            action_description=desc,
            orchestration_action_id=aid,
        )
        audit_path = write_phase1_permission_decision(root, ev, product_id=pid, action_slug=aid)
        try:
            rel_audit = str(audit_path.relative_to(root)).replace("\\", "/")
        except ValueError:
            rel_audit = str(audit_path)
        if ev.get("execution_proceeds") is not True:
            from argus.project_permissions.approvals import write_pending_approval_request

            blocked_detail = embed_phase1_blocked(
                permission_decision=ev,
                audit_path_repo_relative=rel_audit,
            )
            if str(ev.get("aggregate_decision") or "") == "blocked_pending_confirmation":
                pk = ev.get("per_key") or []
                field = ""
                if isinstance(pk, list) and pk:
                    for row in pk:
                        if isinstance(row, dict) and row.get("policy_decision") == "confirm":
                            field = str(row.get("phase1_policy_field") or "")
                            break
                if field:
                    ppath = write_pending_approval_request(
                        root,
                        product_id=pid,
                        orchestration_action_id=aid,
                        phase1_policy_field=field,
                        reason=str(ev.get("reason") or ""),
                        execution_path="orchestration_step_executor",
                        phase1_decision_audit_path_repo_relative=rel_audit,
                    )
                    try:
                        rel_pend = str(ppath.relative_to(root)).replace("\\", "/")
                    except ValueError:
                        rel_pend = str(ppath)
                    blocked_detail["phase1_pending_approval_request_path"] = rel_pend
            result = {
                "action_status": ACTION_STATUS_BLOCKED,
                "execution_detail": blocked_detail,
                "execution_error": str(ev.get("reason") or "phase 1 policy blocked orchestration action"),
            }
            write_orchestration_execution_feedback(root, pid, aid, result)
            return result
        result = _execute_orchestration_action_impl(root, pid, aid)
        ed = result.get("execution_detail")
        if isinstance(ed, dict):
            base_for_embed: dict[str, Any] = ed
        elif ed is None:
            base_for_embed = {}
        else:
            base_for_embed = {
                "orchestration_handler_execution_detail_non_object": True,
                "repr": repr(ed),
            }
        merged_detail = embed_phase1_evaluated(
            base_for_embed,
            permission_decision=ev,
            audit_path_repo_relative=rel_audit,
        )
        result = {**result, "execution_detail": merged_detail}
        write_orchestration_execution_feedback(root, pid, aid, result)
        if result.get("action_status") == ACTION_STATUS_EXECUTED and isinstance(
            ev.get("phase1_pending_consumption"),
            dict,
        ):
            from argus.project_permissions.approvals import consume_confirm_once_grant

            pc = ev["phase1_pending_consumption"]
            gid = str(pc.get("grant_id") or "").strip()
            if gid:
                consume_confirm_once_grant(root, pid, gid)
        return result

    result = _execute_orchestration_action_impl(root, pid, aid)
    detail = result.get("execution_detail")
    result = {
        **result,
        "execution_detail": embed_phase1_not_evaluated(
            detail if isinstance(detail, dict) else None,
            reason=(
                "action_id not in STEP_EXECUTION_REGISTRY — orchestration step executor does not "
                "apply Phase 1 to this path (queued_unhandled)"
            ),
        ),
    }
    write_orchestration_execution_feedback(root, pid, aid, result)
    return result


def _execute_orchestration_action_impl(root: Path, product_id: str, action_id: str) -> dict[str, Any]:
    aid = str(action_id).strip()
    fn = STEP_EXECUTION_REGISTRY.get(aid)
    if fn is not None:
        return fn(root, product_id)
    return {
        "action_status": ACTION_STATUS_QUEUED_UNHANDLED,
        "execution_detail": {
            "reason": "no in-process executor for this action_id",
            "action_id": aid,
        },
        "execution_error": None,
    }


def _execute_signals_collect(root: Path, product_id: str) -> dict[str, Any]:
    inv = build_inventory(root)
    if product_id not in inv.valid:
        return _invalid_product_failure(product_id)
    node = inv.valid[product_id].node
    try:
        records = collect_for_product(
            root,
            node,
            _registry(),
            merge_adapter_layer=_merge_adapter_layer(root),
        )
        path, norm = save_collection(
            root,
            product_id,
            records,
            signal_manifest=node.signal_manifest,
            product_root=product_root_path(root, node),
            external_bindings=parse_external_bindings(node.raw_extensions),
        )
        apply_execution_outcomes(root)
    except Exception as e:
        return {
            "action_status": ACTION_STATUS_FAILED,
            "execution_detail": {"exception_type": type(e).__name__},
            "execution_error": str(e),
        }
    try:
        rel_coll = str(path.relative_to(root))
    except ValueError:
        rel_coll = str(path)
    return {
        "action_status": ACTION_STATUS_EXECUTED,
        "execution_detail": {
            "collection_path": rel_coll,
            "record_count": len(norm),
        },
        "execution_error": None,
    }


def _execute_temporal_refresh(root: Path, product_id: str) -> dict[str, Any]:
    """Recompute ``runs/temporal/latest/<product_id>.json`` from ``runs/signals/latest`` (no adapters)."""
    inv = build_inventory(root)
    if product_id not in inv.valid:
        return _invalid_product_failure(product_id)
    from argus.temporal.persistence import (
        refresh_temporal_from_signals_latest,
        temporal_latest_path,
    )

    try:
        ok = refresh_temporal_from_signals_latest(root, product_id)
    except Exception as e:
        return {
            "action_status": ACTION_STATUS_FAILED,
            "execution_detail": {"exception_type": type(e).__name__},
            "execution_error": str(e),
        }
    if not ok:
        return {
            "action_status": ACTION_STATUS_FAILED,
            "execution_detail": {"reason": "missing runs/signals/latest bundle"},
            "execution_error": "temporal_refresh requires runs/signals/latest/<product_id>.json",
        }
    p = temporal_latest_path(root, product_id)
    try:
        rel = str(p.relative_to(root))
    except ValueError:
        rel = str(p)
    return {
        "action_status": ACTION_STATUS_EXECUTED,
        "execution_detail": {
            "temporal_latest_path": rel,
            "schema": "argus.temporal_bundle.v1",
        },
        "execution_error": None,
    }


def _execute_findings_generate(root: Path, product_id: str) -> dict[str, Any]:
    """Derive findings from ``runs/signals/latest`` (same core path as ``argus findings generate``)."""
    inv = build_inventory(root)
    if product_id not in inv.valid:
        return _invalid_product_failure(product_id)
    facts = evaluate_product_orchestration(root, product_id).get("eligibility_facts") or {}
    if not facts.get("findings_generate_eligible"):
        return {
            "action_status": ACTION_STATUS_FAILED,
            "execution_detail": {
                "product_id": product_id,
                "findings_generate_eligible": False,
            },
            "execution_error": "findings_generate requires runs/signals/latest with non-stale collected_at_utc",
        }
    node = inv.valid[product_id].node
    from argus.findings.engine import generate_findings
    from argus.findings.persistence import latest_path, save_findings_bundle
    from argus.input.apply import apply_to_findings
    from argus.signals.persistence import load_latest_bundle

    bundle = load_latest_bundle(root, product_id)
    if bundle is None:
        return {
            "action_status": ACTION_STATUS_FAILED,
            "execution_detail": {"product_id": product_id},
            "execution_error": "findings_generate requires runs/signals/latest/<product_id>.json",
        }
    try:
        findings = generate_findings(node, bundle.records, repo_root=root)
        findings = apply_to_findings(root, product_id, findings)
        gen_path = save_findings_bundle(root, product_id, findings)
        apply_execution_outcomes(root)
    except Exception as e:
        return {
            "action_status": ACTION_STATUS_FAILED,
            "execution_detail": {"exception_type": type(e).__name__},
            "execution_error": str(e),
        }
    try:
        rel_gen = str(gen_path.relative_to(root))
    except ValueError:
        rel_gen = str(gen_path)
    lp = latest_path(root, product_id)
    try:
        rel_latest = str(lp.relative_to(root))
    except ValueError:
        rel_latest = str(lp)
    return {
        "action_status": ACTION_STATUS_EXECUTED,
        "execution_detail": {
            "schema": "argus.findings_bundle.v1",
            "generation_path": rel_gen,
            "findings_latest_path": rel_latest,
            "finding_count": len(findings),
        },
        "execution_error": None,
    }


def _execute_decisions_generate(root: Path, product_id: str) -> dict[str, Any]:
    """Derive decisions from latest findings (same core path as ``argus decisions generate``)."""
    inv = build_inventory(root)
    if product_id not in inv.valid:
        return _invalid_product_failure(product_id)
    facts = evaluate_product_orchestration(root, product_id).get("eligibility_facts") or {}
    if not facts.get("decisions_generate_eligible"):
        return {
            "action_status": ACTION_STATUS_FAILED,
            "execution_detail": {
                "product_id": product_id,
                "decisions_generate_eligible": False,
            },
            "execution_error": (
                "decisions_generate requires fresh runs/signals/latest and runs/findings/latest/<product_id>.json"
            ),
        }
    from argus.decision.engine import generate_decisions
    from argus.decision.evolution import (
        build_decision_lineage_payload,
        change_summary_from_bundle_extras,
    )
    from argus.decision.persistence import latest_product_path, save_product_decisions
    from argus.decision_assessment.evaluate import evaluate_decision_context
    from argus.decision_assessment.persistence import save_assessment
    from argus.findings.experiment_surfaced import merged_findings_for_decisions
    from argus.findings.persistence import load_latest_findings

    fb = load_latest_findings(root, product_id)
    if fb is None:
        return {
            "action_status": ACTION_STATUS_FAILED,
            "execution_detail": {"product_id": product_id},
            "execution_error": "decisions_generate requires runs/findings/latest/<product_id>.json",
        }
    node = inv.valid[product_id].node
    try:
        findings = merged_findings_for_decisions(root, product_id)
        assessment, candidates = generate_decisions(node, findings, repo_root=root)
        ctx_obj = evaluate_decision_context(root, product_id)
        save_assessment(root, ctx_obj)
        ctx_payload = ctx_obj.to_jsonable()
        lin = build_decision_lineage_payload(root, product_id, candidates)
        gen_path = save_product_decisions(
            root,
            product_id,
            assessment,
            candidates,
            decision_context=ctx_payload,
            lineage_bundle_extras=lin["bundle_extras"],
            lineage_candidate_augmentations=lin["candidate_augmentations"],
        )
        apply_execution_outcomes(root)
    except Exception as e:
        return {
            "action_status": ACTION_STATUS_FAILED,
            "execution_detail": {"exception_type": type(e).__name__},
            "execution_error": str(e),
        }
    try:
        rel_gen = str(gen_path.relative_to(root))
    except ValueError:
        rel_gen = str(gen_path)
    lp = latest_product_path(root, product_id)
    try:
        rel_latest = str(lp.relative_to(root))
    except ValueError:
        rel_latest = str(lp)
    stage = assessment.stage.value if hasattr(assessment.stage, "value") else str(assessment.stage)
    bx = lin["bundle_extras"]
    det: dict[str, Any] = {
        "schema": "argus.decisions_bundle.v1",
        "generation_path": rel_gen,
        "decisions_latest_path": rel_latest,
        "candidate_count": len(candidates),
        "lifecycle_stage": stage,
        "change_summary": change_summary_from_bundle_extras(bx),
    }
    if bx.get("previous_decisions_generated_at_utc") is not None:
        det["previous_decisions_generated_at_utc"] = bx["previous_decisions_generated_at_utc"]
    return {
        "action_status": ACTION_STATUS_EXECUTED,
        "execution_detail": det,
        "execution_error": None,
    }


def _execute_decisions_refresh_from_surfaced_findings(root: Path, product_id: str) -> dict[str, Any]:
    """Re-materialize decisions bundle and assessment using merged canonical + experiment-surfaced findings."""
    inv = build_inventory(root)
    if product_id not in inv.valid:
        return _invalid_product_failure(product_id)
    facts = evaluate_product_orchestration(root, product_id).get("eligibility_facts") or {}
    if not facts.get("decisions_refresh_from_surfaced_findings_eligible"):
        return {
            "action_status": ACTION_STATUS_FAILED,
            "execution_detail": {
                "product_id": product_id,
                "decisions_refresh_from_surfaced_findings_eligible": False,
            },
            "execution_error": (
                "decisions_refresh_from_surfaced_findings requires fresh runs/signals/latest, "
                "loadable runs/findings/latest/<product_id>.json, ≥1 loadable experiment-surfaced finding, "
                "and experiment-surfaced generated_at_utc newer than runs/decisions/latest/<product_id>.json "
                "when present (see decisions_refresh_from_surfaced_findings_eligible)"
            ),
        }
    from argus.decision.engine import generate_decisions
    from argus.decision.evolution import (
        build_decision_lineage_payload,
        change_summary_from_bundle_extras,
    )
    from argus.decision.persistence import latest_product_path, save_product_decisions
    from argus.decision_assessment.evaluate import evaluate_decision_context
    from argus.decision_assessment.persistence import save_assessment
    from argus.findings.experiment_surfaced import (
        load_experiment_surfaced_findings,
        merged_findings_for_decisions,
    )
    from argus.findings.persistence import load_latest_findings

    fb = load_latest_findings(root, product_id)
    if fb is None:
        return {
            "action_status": ACTION_STATUS_FAILED,
            "execution_detail": {"product_id": product_id},
            "execution_error": "decisions_refresh_from_surfaced_findings requires runs/findings/latest/<product_id>.json",
        }
    extra = load_experiment_surfaced_findings(root, product_id)
    if len(extra) < 1:
        return {
            "action_status": ACTION_STATUS_FAILED,
            "execution_detail": {"product_id": product_id, "surfaced_finding_count": 0},
            "execution_error": (
                "decisions_refresh_from_surfaced_findings requires at least one loadable experiment-surfaced finding"
            ),
        }
    node = inv.valid[product_id].node
    try:
        findings = merged_findings_for_decisions(root, product_id)
        assessment, candidates = generate_decisions(node, findings, repo_root=root)
        ctx_obj = evaluate_decision_context(root, product_id)
        ap = save_assessment(root, ctx_obj)
        ctx_payload = ctx_obj.to_jsonable()
        lin = build_decision_lineage_payload(root, product_id, candidates)
        gen_path = save_product_decisions(
            root,
            product_id,
            assessment,
            candidates,
            decision_context=ctx_payload,
            lineage_bundle_extras=lin["bundle_extras"],
            lineage_candidate_augmentations=lin["candidate_augmentations"],
        )
        apply_execution_outcomes(root)
    except Exception as e:
        return {
            "action_status": ACTION_STATUS_FAILED,
            "execution_detail": {"exception_type": type(e).__name__},
            "execution_error": str(e),
        }
    try:
        rel_gen = str(gen_path.relative_to(root))
    except ValueError:
        rel_gen = str(gen_path)
    lp = latest_product_path(root, product_id)
    try:
        rel_latest = str(lp.relative_to(root))
    except ValueError:
        rel_latest = str(lp)
    try:
        rel_assess = str(ap.relative_to(root))
    except ValueError:
        rel_assess = str(ap)
    surfaced_n = sum(
        1
        for f in findings
        if (f.evidence or {}).get("provenance") == "experiment_surfaced"
        or str(f.id).startswith("exp_surface:")
    )
    stage = assessment.stage.value if hasattr(assessment.stage, "value") else str(assessment.stage)
    bx = lin["bundle_extras"]
    det_rf: dict[str, Any] = {
        "schema": "argus.decisions_bundle.v1",
        "generation_path": rel_gen,
        "decisions_latest_path": rel_latest,
        "assessment_latest_path": rel_assess,
        "candidate_count": len(candidates),
        "surfaced_findings_used_count": surfaced_n,
        "lifecycle_stage": stage,
        "change_summary": change_summary_from_bundle_extras(bx),
        "authority_note": (
            "decisions bundle and decision_assessment re-materialized from persisted canonical and "
            "experiment-surfaced findings only; does not claim new external evidence or re-run experiments"
        ),
    }
    if bx.get("previous_decisions_generated_at_utc") is not None:
        det_rf["previous_decisions_generated_at_utc"] = bx["previous_decisions_generated_at_utc"]
    return {
        "action_status": ACTION_STATUS_EXECUTED,
        "execution_detail": det_rf,
        "execution_error": None,
    }


def _execute_ideas_generate(root: Path, product_id: str) -> dict[str, Any]:
    """Derive ideas bundle (deterministic orchestration path: no advisor or LLM expansion)."""
    inv = build_inventory(root)
    if product_id not in inv.valid:
        return _invalid_product_failure(product_id)
    facts = evaluate_product_orchestration(root, product_id).get("eligibility_facts") or {}
    if not facts.get("ideas_generate_eligible"):
        return {
            "action_status": ACTION_STATUS_FAILED,
            "execution_detail": {
                "product_id": product_id,
                "ideas_generate_eligible": False,
            },
            "execution_error": (
                "ideas_generate requires fresh runs/signals/latest, runs/findings/latest, "
                "and runs/decisions/latest/<product_id>.json"
            ),
        }
    from argus.idea_generation.pipeline import latest_path, run_pipeline

    try:
        gen_path, bundle = run_pipeline(
            root,
            product_id,
            advisor_expansion=False,
            llm_idea_expansion=False,
            max_advisor_expansion_ideas=0,
            max_llm_expansion_ideas=0,
        )
        apply_execution_outcomes(root)
    except Exception as e:
        return {
            "action_status": ACTION_STATUS_FAILED,
            "execution_detail": {"exception_type": type(e).__name__},
            "execution_error": str(e),
        }
    try:
        rel_gen = str(gen_path.relative_to(root))
    except ValueError:
        rel_gen = str(gen_path)
    lp = latest_path(root)
    try:
        rel_latest = str(lp.relative_to(root))
    except ValueError:
        rel_latest = str(lp)
    return {
        "action_status": ACTION_STATUS_EXECUTED,
        "execution_detail": {
            "schema": bundle.schema,
            "generation_path": rel_gen,
            "ideas_latest_path": rel_latest,
            "idea_count": len(bundle.ideas),
            "advisor_expansion": False,
            "llm_idea_expansion": False,
        },
        "execution_error": None,
    }


def _execute_ideas_refresh_from_surfaced_findings(root: Path, product_id: str) -> dict[str, Any]:
    """Re-materialize ideas bundle using merged canonical + experiment-surfaced findings (orchestration-bounded)."""
    inv = build_inventory(root)
    if product_id not in inv.valid:
        return _invalid_product_failure(product_id)
    facts = evaluate_product_orchestration(root, product_id).get("eligibility_facts") or {}
    if not facts.get("ideas_refresh_from_surfaced_findings_eligible"):
        return {
            "action_status": ACTION_STATUS_FAILED,
            "execution_detail": {
                "product_id": product_id,
                "ideas_refresh_from_surfaced_findings_eligible": False,
            },
            "execution_error": (
                "ideas_refresh_from_surfaced_findings requires Phase-2 spine (signals, findings, decisions), "
                "canonical findings latest, loadable experiment-surfaced findings, "
                "and experiment-surfaced generated_at_utc newer than runs/ideas/latest.json when present "
                "(see ideas_refresh_from_surfaced_findings_eligible)"
            ),
        }
    from argus.findings.experiment_surfaced import (
        load_experiment_surfaced_findings,
        merged_findings_for_decisions,
    )
    from argus.findings.persistence import load_latest_findings
    from argus.idea_generation.pipeline import latest_path, run_pipeline

    fb = load_latest_findings(root, product_id)
    if fb is None:
        return {
            "action_status": ACTION_STATUS_FAILED,
            "execution_detail": {"product_id": product_id},
            "execution_error": "ideas_refresh_from_surfaced_findings requires runs/findings/latest/<product_id>.json",
        }
    extra = load_experiment_surfaced_findings(root, product_id)
    if len(extra) < 1:
        return {
            "action_status": ACTION_STATUS_FAILED,
            "execution_detail": {"product_id": product_id, "surfaced_finding_count": 0},
            "execution_error": (
                "ideas_refresh_from_surfaced_findings requires at least one loadable experiment-surfaced finding"
            ),
        }
    merged = merged_findings_for_decisions(root, product_id)
    try:
        gen_path, bundle = run_pipeline(
            root,
            product_id,
            advisor_expansion=False,
            llm_idea_expansion=False,
            max_advisor_expansion_ideas=0,
            max_llm_expansion_ideas=0,
            findings_rows=merged,
        )
        apply_execution_outcomes(root)
    except Exception as e:
        return {
            "action_status": ACTION_STATUS_FAILED,
            "execution_detail": {"exception_type": type(e).__name__},
            "execution_error": str(e),
        }
    try:
        rel_gen = str(gen_path.relative_to(root))
    except ValueError:
        rel_gen = str(gen_path)
    lp = latest_path(root)
    try:
        rel_latest = str(lp.relative_to(root))
    except ValueError:
        rel_latest = str(lp)
    surfaced_n = sum(
        1
        for f in merged
        if (f.evidence or {}).get("provenance") == "experiment_surfaced"
        or str(f.id).startswith("exp_surface:")
    )
    return {
        "action_status": ACTION_STATUS_EXECUTED,
        "execution_detail": {
            "schema": bundle.schema,
            "generation_path": rel_gen,
            "ideas_latest_path": rel_latest,
            "idea_count": len(bundle.ideas),
            "surfaced_findings_used_count": surfaced_n,
            "advisor_expansion": False,
            "llm_idea_expansion": False,
            "authority_note": (
                "ideas bundle re-materialized from persisted canonical and experiment-surfaced findings only; "
                "does not claim new external evidence or re-run experiments"
            ),
        },
        "execution_error": None,
    }


def _execute_experiments_propose(root: Path, product_id: str) -> dict[str, Any]:
    """Deterministic experiment proposals (same path as ``argus experiments propose``); not execution authority."""
    inv = build_inventory(root)
    if product_id not in inv.valid:
        return _invalid_product_failure(product_id)
    facts = evaluate_product_orchestration(root, product_id).get("eligibility_facts") or {}
    if not facts.get("experiments_propose_eligible"):
        return {
            "action_status": ACTION_STATUS_FAILED,
            "execution_detail": {
                "product_id": product_id,
                "experiments_propose_eligible": False,
            },
            "execution_error": (
                "experiments_propose requires a valid product, fresh runs/signals/latest, "
                "runs/findings/latest/<product_id>.json, and runs/decisions/latest/<product_id>.json"
            ),
        }
    from argus.experiments.propose import propose_experiments, save_proposals_run_latest
    from argus.orchestrator.artifact_snapshot import list_refinement_sessions_for_product

    try:
        run = propose_experiments(root, product_id=product_id, inventory=inv)
        out_path = save_proposals_run_latest(root, product_id, run)
        apply_execution_outcomes(root)
    except Exception as e:
        return {
            "action_status": ACTION_STATUS_FAILED,
            "execution_detail": {"exception_type": type(e).__name__},
            "execution_error": str(e),
        }
    try:
        rel = str(out_path.relative_to(root))
    except ValueError:
        rel = str(out_path)
    n_ref = len(list_refinement_sessions_for_product(root, product_id))
    ideas_path = root / "runs" / "ideas" / "latest.json"
    ideas_for_product = False
    if ideas_path.is_file():
        try:
            raw = json.loads(ideas_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeError):
            raw = None
        ideas_for_product = isinstance(raw, dict) and str(raw.get("product_id") or "") == product_id
    return {
        "action_status": ACTION_STATUS_EXECUTED,
        "execution_detail": {
            "schema": "argus.experiment_proposals_run.v1",
            "proposals_latest_path": rel,
            "proposal_artifact_paths": [rel],
            "proposal_count": len(run.proposals),
            "ideas_latest_present_for_product": ideas_for_product,
            "refinement_session_count": n_ref,
            "authority_note": (
                "proposed experiments are candidate tracked hypotheses only; not approval or execution authority"
            ),
        },
        "execution_error": None,
    }


def _execute_experiments_prioritize(root: Path, product_id: str) -> dict[str, Any]:
    """Rank experiments from persisted proposals latest only (advisory; not execution authority)."""
    inv = build_inventory(root)
    if product_id not in inv.valid:
        return _invalid_product_failure(product_id)
    facts = evaluate_product_orchestration(root, product_id).get("eligibility_facts") or {}
    if not facts.get("experiments_prioritize_eligible"):
        return {
            "action_status": ACTION_STATUS_FAILED,
            "execution_detail": {
                "product_id": product_id,
                "experiments_prioritize_eligible": False,
            },
            "execution_error": (
                "experiments_prioritize requires a valid product, fresh runs/signals/latest, "
                "runs/findings/latest/<product_id>.json, runs/decisions/latest/<product_id>.json, "
                "and runs/experiments/proposals/latest/<product_id>.json"
            ),
        }
    from argus.experiments.prioritize import (
        prioritize_experiments_from_proposal_run,
        save_prioritization_run_latest,
    )
    from argus.experiments.propose import load_proposals_run_latest

    try:
        proposal_run = load_proposals_run_latest(root, product_id)
    except FileNotFoundError:
        return {
            "action_status": ACTION_STATUS_FAILED,
            "execution_detail": {
                "product_id": product_id,
                "experiments_prioritize_eligible": False,
            },
            "execution_error": (
                "experiments_prioritize requires runs/experiments/proposals/latest/<product_id>.json"
            ),
        }
    except Exception as e:
        return {
            "action_status": ACTION_STATUS_FAILED,
            "execution_detail": {"exception_type": type(e).__name__},
            "execution_error": str(e),
        }
    try:
        run = prioritize_experiments_from_proposal_run(root, proposal_run, inventory=inv)
        out_path = save_prioritization_run_latest(root, product_id, run)
        apply_execution_outcomes(root)
    except Exception as e:
        return {
            "action_status": ACTION_STATUS_FAILED,
            "execution_detail": {"exception_type": type(e).__name__},
            "execution_error": str(e),
        }
    try:
        rel = str(out_path.relative_to(root))
    except ValueError:
        rel = str(out_path)
    rows = run.by_product.get(product_id) or []
    ranked_count = len(rows)
    top_id = rows[0].proposal.proposal_id if rows else None
    top_score = rows[0].score if rows else None
    return {
        "action_status": ACTION_STATUS_EXECUTED,
        "execution_detail": {
            "schema": "argus.experiment_prioritization_run.v2",
            "prioritization_latest_path": rel,
            "ranked_count": ranked_count,
            "top_experiment_id": top_id,
            "top_score": top_score,
            "authority_note": (
                "experiment ranking is advisory only; not approval or execution authority"
            ),
        },
        "execution_error": None,
    }


def _execute_experiments_create(root: Path, product_id: str) -> dict[str, Any]:
    """Materialize top-ranked proposal from persisted prioritization only (tracked JSON; not external execution)."""
    inv = build_inventory(root)
    if product_id not in inv.valid:
        return _invalid_product_failure(product_id)
    facts = evaluate_product_orchestration(root, product_id).get("eligibility_facts") or {}
    if not facts.get("experiments_create_eligible"):
        return {
            "action_status": ACTION_STATUS_FAILED,
            "execution_detail": {
                "product_id": product_id,
                "experiments_create_eligible": False,
            },
            "execution_error": (
                "experiments_create requires a valid product, fresh runs/signals/latest, "
                "runs/findings/latest/<product_id>.json, runs/decisions/latest/<product_id>.json, "
                "runs/experiments/proposals/latest/<product_id>.json, "
                "runs/experiments/prioritization/latest/<product_id>.json with at least one ranked proposal, "
                "and experiment create quota available (see experiments_create_eligible)"
            ),
        }
    from argus.autonomy.controller import load_state
    from argus.autonomy.quotas import check_experiment_create_allowed, record_experiment_created
    from argus.experiments.models import Experiment, ExperimentStatus
    from argus.experiments.prioritize import (
        load_prioritization_run_latest,
        prioritization_latest_path,
    )
    from argus.experiments.store import (
        experiment_path,
        list_experiments,
        new_experiment_id,
        save_experiment,
    )

    q_ok, q_msg = check_experiment_create_allowed(root)
    if not q_ok:
        return {
            "action_status": ACTION_STATUS_FAILED,
            "execution_detail": {
                "product_id": product_id,
                "experiments_create_eligible": True,
                "quota_allowed": False,
            },
            "execution_error": q_msg,
        }
    try:
        pri_run = load_prioritization_run_latest(root, product_id)
    except FileNotFoundError:
        return {
            "action_status": ACTION_STATUS_FAILED,
            "execution_detail": {
                "product_id": product_id,
                "experiments_create_eligible": False,
            },
            "execution_error": (
                "experiments_create requires runs/experiments/prioritization/latest/<product_id>.json"
            ),
        }
    except Exception as e:
        return {
            "action_status": ACTION_STATUS_FAILED,
            "execution_detail": {"exception_type": type(e).__name__},
            "execution_error": str(e),
        }
    rows = list(pri_run.by_product.get(product_id) or [])
    if not rows:
        return {
            "action_status": ACTION_STATUS_FAILED,
            "execution_detail": {
                "product_id": product_id,
                "ranked_proposals_count": 0,
            },
            "execution_error": (
                "experiments_create requires at least one ranked proposal in the prioritization artifact "
                "(by_product/<product_id> non-empty)"
            ),
        }
    top = sorted(rows, key=lambda r: (r.rank, r.proposal.proposal_id))[0]
    prop = top.proposal
    for existing in list_experiments(root, product_id=product_id):
        if existing.source_proposal_id == prop.proposal_id:
            try:
                rel_pri = str(prioritization_latest_path(root, product_id).relative_to(root))
            except ValueError:
                rel_pri = str(prioritization_latest_path(root, product_id))
            ep = experiment_path(root, existing.id)
            try:
                rel_exp = str(ep.relative_to(root))
            except ValueError:
                rel_exp = str(ep)
            try:
                apply_execution_outcomes(root)
            except Exception as e:
                return {
                    "action_status": ACTION_STATUS_FAILED,
                    "execution_detail": {"exception_type": type(e).__name__},
                    "execution_error": str(e),
                }
            return {
                "action_status": ACTION_STATUS_EXECUTED,
                "execution_detail": {
                    "schema": "argus.experiment.v1",
                    "experiment_id": existing.id,
                    "experiment_path": rel_exp,
                    "source_prioritization_path": rel_pri,
                    "source_proposal_id": prop.proposal_id,
                    "source_rank": top.rank,
                    "dedupe_skipped": True,
                    "authority_note": (
                        "materialized experiment JSON is an in-repo tracked work object only; "
                        "it does not grant external execution, deployment, or shipping authority"
                    ),
                    "quota": {"dedupe": True, "note": "no new experiment record; quota unchanged"},
                },
                "execution_error": None,
            }

    now = datetime.now(timezone.utc).isoformat()
    exp = Experiment(
        id=new_experiment_id(),
        product_id=product_id,
        hypothesis=prop.hypothesis,
        type=prop.type,
        description=prop.description,
        expected_outcome=prop.expected_outcome,
        success_metrics=list(prop.success_metrics),
        start_at=now,
        end_at=None,
        status=ExperimentStatus.PROPOSED,
        confidence=prop.confidence,
        created_at=now,
        source_proposal_id=prop.proposal_id,
    )
    try:
        out_path = save_experiment(root, exp)
        record_experiment_created(root)
        apply_execution_outcomes(root)
    except Exception as e:
        return {
            "action_status": ACTION_STATUS_FAILED,
            "execution_detail": {"exception_type": type(e).__name__},
            "execution_error": str(e),
        }
    try:
        rel_exp = str(out_path.relative_to(root))
    except ValueError:
        rel_exp = str(out_path)
    try:
        rel_pri = str(prioritization_latest_path(root, product_id).relative_to(root))
    except ValueError:
        rel_pri = str(prioritization_latest_path(root, product_id))
    state = load_state(root)
    n_after = int(state.get("experiments_created_today", 0))
    lim_msg = ""
    try:
        from argus.autonomy.operator_policy import effective_policy

        _path, policy, _tier = effective_policy(root)
        lim_msg = str(policy.max_experiments_per_utc_day)
    except Exception:
        pass
    return {
        "action_status": ACTION_STATUS_EXECUTED,
        "execution_detail": {
            "schema": "argus.experiment.v1",
            "experiment_id": exp.id,
            "experiment_path": rel_exp,
            "source_prioritization_path": rel_pri,
            "source_proposal_id": prop.proposal_id,
            "source_rank": top.rank,
            "authority_note": (
                "materialized experiment JSON is an in-repo tracked work object only; "
                "it does not grant external execution, deployment, or shipping authority"
            ),
            "quota": {
                "allowed": True,
                "experiments_created_today_after": n_after,
                **({"max_experiments_per_utc_day": lim_msg} if lim_msg != "" else {}),
            },
        },
        "execution_error": None,
    }


def _select_proposed_experiment_for_activation(
    root: Path,
    product_id: str,
    proposed: list,
) -> tuple[object, dict[str, Any]]:
    """Deterministic pick among ``proposed`` experiments: prioritization rank when ``source_proposal_id`` matches else created_at/id."""
    if len(proposed) == 1:
        return proposed[0], {"selection_rule": "only_proposed_experiment"}

    rank_by_pid: dict[str, int] = {}
    try:
        from argus.experiments.prioritize import load_prioritization_run_latest

        run = load_prioritization_run_latest(root, product_id)
        for row in run.by_product.get(product_id) or []:
            rank_by_pid[row.proposal.proposal_id] = row.rank
    except (FileNotFoundError, OSError, ValueError, KeyError, TypeError):
        pass

    ranked_candidates: list[tuple[Any, int, str]] = []
    for e in proposed:
        sp = e.source_proposal_id
        if sp and sp in rank_by_pid:
            ranked_candidates.append((e, rank_by_pid[sp], sp))
    if ranked_candidates:
        ranked_candidates.sort(key=lambda t: (t[1], t[2], t[0].id))
        chosen, rk, sp = ranked_candidates[0]
        return chosen, {
            "selection_rule": "prioritization_rank_then_proposal_id_then_experiment_id",
            "source_rank": rk,
            "source_proposal_id": sp,
        }

    ordered = sorted(proposed, key=lambda e: (e.created_at, e.id))
    return ordered[0], {"selection_rule": "created_at_then_experiment_id"}


def _execute_experiments_activate(root: Path, product_id: str) -> dict[str, Any]:
    """Transition one ``proposed`` experiment to ``active`` (in-repo lifecycle; not external execution)."""
    inv = build_inventory(root)
    if product_id not in inv.valid:
        return _invalid_product_failure(product_id)
    facts = evaluate_product_orchestration(root, product_id).get("eligibility_facts") or {}
    if not facts.get("experiments_activate_eligible"):
        return {
            "action_status": ACTION_STATUS_FAILED,
            "execution_detail": {
                "product_id": product_id,
                "experiments_activate_eligible": False,
            },
            "execution_error": (
                "experiments_activate requires a valid product, fresh runs/signals/latest, "
                "runs/findings/latest/<product_id>.json, runs/decisions/latest/<product_id>.json, "
                "and at least one proposed experiment (see experiments_activate_eligible)"
            ),
        }
    from argus.experiments.models import ExperimentStatus
    from argus.experiments.registry import can_transition
    from argus.experiments.store import list_experiments, save_experiment

    exps = list_experiments(root, product_id=product_id)
    proposed = [e for e in exps if e.status == ExperimentStatus.PROPOSED]
    if not exps:
        return {
            "action_status": ACTION_STATUS_FAILED,
            "execution_detail": {"product_id": product_id, "experiment_count": 0},
            "execution_error": "experiments_activate requires at least one persisted experiment for the product",
        }
    if not proposed:
        return {
            "action_status": ACTION_STATUS_FAILED,
            "execution_detail": {"product_id": product_id, "proposed_count": 0},
            "execution_error": "experiments_activate requires at least one experiment in proposed status",
        }
    chosen, sel_meta = _select_proposed_experiment_for_activation(root, product_id, proposed)
    if not can_transition(chosen.status, ExperimentStatus.ACTIVE):
        return {
            "action_status": ACTION_STATUS_FAILED,
            "execution_detail": {"experiment_id": chosen.id, "status": chosen.status.value},
            "execution_error": "registry disallows proposed -> active for the selected experiment",
        }
    prev = chosen.status.value
    chosen.status = ExperimentStatus.ACTIVE
    try:
        out_path = save_experiment(root, chosen)
        apply_execution_outcomes(root)
    except Exception as e:
        return {
            "action_status": ACTION_STATUS_FAILED,
            "execution_detail": {"exception_type": type(e).__name__},
            "execution_error": str(e),
        }
    try:
        rel = str(out_path.relative_to(root))
    except ValueError:
        rel = str(out_path)
    detail: dict[str, Any] = {
        "schema": "argus.experiment.v1",
        "experiment_id": chosen.id,
        "experiment_path": rel,
        "previous_status": prev,
        "new_status": chosen.status.value,
        "authority_note": (
            "activation updates in-repo experiment status only; "
            "it does not claim external execution, rollout, spend authority, or shipping approval"
        ),
        **sel_meta,
    }
    return {
        "action_status": ACTION_STATUS_EXECUTED,
        "execution_detail": detail,
        "execution_error": None,
    }


def _execute_experiments_evaluate(root: Path, product_id: str) -> dict[str, Any]:
    """Run deterministic experiment evaluation for persisted experiments (updates ``runs/experiments/*.json``)."""
    inv = build_inventory(root)
    if product_id not in inv.valid:
        return _invalid_product_failure(product_id)
    facts = evaluate_product_orchestration(root, product_id).get("eligibility_facts") or {}
    if not facts.get("experiments_evaluate_eligible"):
        return {
            "action_status": ACTION_STATUS_FAILED,
            "execution_detail": {
                "product_id": product_id,
                "experiments_evaluate_eligible": False,
            },
            "execution_error": (
                "experiments_evaluate requires a valid product, fresh runs/signals/latest, "
                "runs/findings/latest/<product_id>.json, runs/decisions/latest/<product_id>.json, "
                "and at least one non-terminal experiment under runs/experiments/ "
                "(see experiments_evaluate_eligible)"
            ),
        }
    from argus.experiments.evaluate import run_evaluations
    from argus.experiments.models import ExperimentStatus
    from argus.experiments.store import experiment_path, list_experiments

    exps = list_experiments(root, product_id=product_id)
    if not exps:
        return {
            "action_status": ACTION_STATUS_FAILED,
            "execution_detail": {"product_id": product_id, "experiment_count": 0},
            "execution_error": "experiments_evaluate requires at least one persisted experiment for the product",
        }
    non_terminal = [
        e for e in exps if e.status not in (ExperimentStatus.COMPLETED, ExperimentStatus.FAILED)
    ]
    if not non_terminal:
        return {
            "action_status": ACTION_STATUS_FAILED,
            "execution_detail": {
                "product_id": product_id,
                "non_terminal_count": 0,
            },
            "execution_error": (
                "experiments_evaluate requires at least one non-terminal experiment "
                "(status not completed or failed)"
            ),
        }
    try:
        results = run_evaluations(root, product_id=product_id, apply_updates=True)
        apply_execution_outcomes(root)
    except Exception as e:
        return {
            "action_status": ACTION_STATUS_FAILED,
            "execution_detail": {"exception_type": type(e).__name__},
            "execution_error": str(e),
        }
    paths: list[str] = []
    for ev in results:
        p = experiment_path(root, ev.experiment_id)
        try:
            paths.append(str(p.relative_to(root)))
        except ValueError:
            paths.append(str(p))
    paths = sorted(set(paths))
    top = max(results, key=lambda x: (x.composite_score, x.experiment_id)) if results else None
    return {
        "action_status": ACTION_STATUS_EXECUTED,
        "execution_detail": {
            "schema": "argus.experiment_evaluation.v1",
            "evaluation_artifact_paths": paths,
            "evaluated_count": len(results),
            "top_experiment_id": top.experiment_id if top else None,
            "top_composite_score": top.composite_score if top else None,
            "authority_note": (
                "experiment evaluation is deterministic analysis and feedback on persisted metrics and trends; "
                "it is not execution authority, deployment approval, or shipping authority"
            ),
        },
        "execution_error": None,
    }


def _execute_experiments_close_stale(root: Path, product_id: str) -> dict[str, Any]:
    """Mark stale non-terminal experiments failed (in-repo hygiene; not external shutdown authority)."""
    inv = build_inventory(root)
    if product_id not in inv.valid:
        return _invalid_product_failure(product_id)
    facts = evaluate_product_orchestration(root, product_id).get("eligibility_facts") or {}
    if not facts.get("experiments_close_stale_eligible"):
        return {
            "action_status": ACTION_STATUS_FAILED,
            "execution_detail": {
                "product_id": product_id,
                "experiments_close_stale_eligible": False,
            },
            "execution_error": (
                "experiments_close_stale requires a valid product, fresh Phase-2 spine "
                "(signals, findings, decisions), at least one experiment on disk, "
                "and at least one non-terminal experiment satisfying the stale-close age rule "
                "(see experiments_close_stale_eligible)"
            ),
        }
    from argus.experiments.models import ExperimentStatus
    from argus.experiments.stale_close import (
        STALE_CLOSE_MIN_AGE_DAYS,
        STALE_CLOSE_RULE_ID,
        experiment_eligible_for_stale_close,
    )
    from argus.experiments.store import list_experiments, save_experiment

    now = datetime.now(timezone.utc)
    exps = list_experiments(root, product_id=product_id)
    if not exps:
        return {
            "action_status": ACTION_STATUS_FAILED,
            "execution_detail": {"product_id": product_id, "experiment_count": 0},
            "execution_error": "experiments_close_stale requires at least one persisted experiment for the product",
        }
    stale = [e for e in exps if experiment_eligible_for_stale_close(e, now)]
    if not stale:
        return {
            "action_status": ACTION_STATUS_FAILED,
            "execution_detail": {
                "product_id": product_id,
                "stale_candidate_count": 0,
                "stale_close_rule": STALE_CLOSE_RULE_ID,
            },
            "execution_error": "experiments_close_stale: no experiments matched stale-close predicates",
        }
    stale_sorted = sorted(stale, key=lambda e: (e.created_at, e.id))
    transitions: list[dict[str, str]] = []
    paths: list[str] = []
    ids: list[str] = []
    for e in stale_sorted:
        prev = e.status.value
        e.status = ExperimentStatus.FAILED
        if not e.end_at or not str(e.end_at).strip():
            e.end_at = now.isoformat()
        try:
            out_path = save_experiment(root, e)
        except Exception as ex:
            return {
                "action_status": ACTION_STATUS_FAILED,
                "execution_detail": {"exception_type": type(ex).__name__},
                "execution_error": str(ex),
            }
        try:
            rel = str(out_path.relative_to(root))
        except ValueError:
            rel = str(out_path)
        paths.append(rel)
        ids.append(e.id)
        transitions.append(
            {
                "experiment_id": e.id,
                "previous_status": prev,
                "new_status": e.status.value,
            }
        )
    try:
        apply_execution_outcomes(root)
    except Exception as ex:
        return {
            "action_status": ACTION_STATUS_FAILED,
            "execution_detail": {"exception_type": type(ex).__name__},
            "execution_error": str(ex),
        }
    return {
        "action_status": ACTION_STATUS_EXECUTED,
        "execution_detail": {
            "schema": "argus.experiment.v1",
            "affected_experiment_ids": ids,
            "experiment_paths": paths,
            "status_transitions": transitions,
            "stale_close_rule": STALE_CLOSE_RULE_ID,
            "stale_close_min_age_days": STALE_CLOSE_MIN_AGE_DAYS,
            "authority_note": (
                "stale close updates in-repo experiment lifecycle state only; "
                "it does not claim external shutdown, rollout control, spend authority, or shipping approval"
            ),
        },
        "execution_error": None,
    }


def _execute_experiments_surface_findings(root: Path, product_id: str) -> dict[str, Any]:
    """Write experiment-outcome surfaced findings sidecar (derived evidence for decision generation)."""
    inv = build_inventory(root)
    if product_id not in inv.valid:
        return _invalid_product_failure(product_id)
    facts = evaluate_product_orchestration(root, product_id).get("eligibility_facts") or {}
    if not facts.get("experiments_surface_findings_eligible"):
        return {
            "action_status": ACTION_STATUS_FAILED,
            "execution_detail": {
                "product_id": product_id,
                "experiments_surface_findings_eligible": False,
            },
            "execution_error": (
                "experiments_surface_findings requires a valid product, fresh Phase-2 spine "
                "(signals, findings, decisions), at least one persisted experiment, "
                "and at least one experiment with terminal outcome or evaluation verdict "
                "(see experiments_surface_findings_eligible)"
            ),
        }
    from argus.experiments.store import list_experiments
    from argus.findings.experiment_surfaced import (
        EXPERIMENT_SURFACED_SCHEMA,
        EXPERIMENT_SURFACING_RULE_ID,
        build_surfaced_findings,
        has_usable_experiment_outcome,
        save_experiment_surfaced_bundle,
    )

    exps = list_experiments(root, product_id=product_id)
    if not exps:
        return {
            "action_status": ACTION_STATUS_FAILED,
            "execution_detail": {"product_id": product_id, "experiment_count": 0},
            "execution_error": "experiments_surface_findings requires at least one persisted experiment for the product",
        }
    usable = [e for e in exps if has_usable_experiment_outcome(e)]
    if not usable:
        return {
            "action_status": ACTION_STATUS_FAILED,
            "execution_detail": {"product_id": product_id, "usable_outcome_count": 0},
            "execution_error": (
                "experiments_surface_findings: no experiments with usable outcome evidence "
                "(terminal completed/failed status and/or last_evaluation_verdict)"
            ),
        }
    try:
        findings = build_surfaced_findings(exps, product_id)
        out_path = save_experiment_surfaced_bundle(root, product_id, findings)
        apply_execution_outcomes(root)
    except Exception as ex:
        return {
            "action_status": ACTION_STATUS_FAILED,
            "execution_detail": {"exception_type": type(ex).__name__},
            "execution_error": str(ex),
        }
    try:
        rel = str(out_path.relative_to(root))
    except ValueError:
        rel = str(out_path)
    affected = sorted({e.id for e in usable})
    return {
        "action_status": ACTION_STATUS_EXECUTED,
        "execution_detail": {
            "schema": EXPERIMENT_SURFACED_SCHEMA,
            "surfacing_rule": EXPERIMENT_SURFACING_RULE_ID,
            "experiment_surfaced_latest_path": rel,
            "surfaced_finding_count": len(findings),
            "affected_experiment_ids": affected,
            "authority_note": (
                "surfaced findings are deterministic projections from persisted experiment records; "
                "they are not independent measurements, external causal proof, or execution authority"
            ),
        },
        "execution_error": None,
    }


def _execute_strategy_refresh_from_decision_evolution(root: Path, product_id: str) -> dict[str, Any]:
    """Derive ``runs/strategy/latest/<product_id>.json`` from decisions evolution + surfaced evidence."""
    inv = build_inventory(root)
    if product_id not in inv.valid:
        return _invalid_product_failure(product_id)
    facts = evaluate_product_orchestration(root, product_id).get("eligibility_facts") or {}
    if not facts.get("strategy_refresh_from_decision_evolution_eligible"):
        return {
            "action_status": ACTION_STATUS_FAILED,
            "execution_detail": {
                "product_id": product_id,
                "strategy_refresh_from_decision_evolution_eligible": False,
            },
            "execution_error": (
                "strategy_refresh_from_decision_evolution requires loadable runs/decisions/latest/<product_id>.json "
                "and strategy snapshot missing or older than max(decisions, experiment-surfaced) timestamps "
                "(see strategy_refresh_from_decision_evolution_eligible)"
            ),
        }
    from argus.strategy.snapshot import (
        STRATEGY_SNAPSHOT_SCHEMA,
        build_strategy_snapshot,
        save_strategy_snapshot,
        strategy_latest_path,
    )

    try:
        snap = build_strategy_snapshot(root, product_id)
        gen_path = save_strategy_snapshot(root, product_id, snap)
        apply_execution_outcomes(root)
    except Exception as e:
        return {
            "action_status": ACTION_STATUS_FAILED,
            "execution_detail": {"exception_type": type(e).__name__},
            "execution_error": str(e),
        }
    try:
        rel_gen = str(gen_path.relative_to(root))
    except ValueError:
        rel_gen = str(gen_path)
    lp = strategy_latest_path(root, product_id)
    try:
        rel_latest = str(lp.relative_to(root))
    except ValueError:
        rel_latest = str(lp)
    ev = snap.get("evidence") if isinstance(snap.get("evidence"), dict) else {}
    phist = snap.get("posture_history")
    history_used = len(phist) if isinstance(phist, list) else 0
    return {
        "action_status": ACTION_STATUS_EXECUTED,
        "execution_detail": {
            "schema": STRATEGY_SNAPSHOT_SCHEMA,
            "generation_path": rel_gen,
            "strategy_latest_path": rel_latest,
            "posture": snap.get("posture"),
            "posture_raw": snap.get("posture_raw"),
            "posture_final": snap.get("posture"),
            "posture_changed": bool(snap.get("posture_changed")),
            "history_used_count": history_used,
            "source_decisions_generated_at_utc": snap.get("source_decisions_generated_at_utc"),
            "source_experiment_surfaced_generated_at_utc": snap.get("source_experiment_surfaced_generated_at_utc"),
            "evidence": ev,
        },
        "execution_error": None,
    }


def _execute_planning_refresh_from_strategy(root: Path, product_id: str) -> dict[str, Any]:
    """Derive ``runs/planning/latest/<product_id>.json`` from strategy + decisions + surfaced."""
    inv = build_inventory(root)
    if product_id not in inv.valid:
        return _invalid_product_failure(product_id)
    facts = evaluate_product_orchestration(root, product_id).get("eligibility_facts") or {}
    if not facts.get("planning_refresh_from_strategy_eligible"):
        return {
            "action_status": ACTION_STATUS_FAILED,
            "execution_detail": {
                "product_id": product_id,
                "planning_refresh_from_strategy_eligible": False,
            },
            "execution_error": (
                "planning_refresh_from_strategy requires loadable runs/strategy/latest/<product_id>.json "
                "and planning snapshot missing or older than max(strategy, decisions, experiment-surfaced) "
                "(see planning_refresh_from_strategy_eligible)"
            ),
        }
    from argus.planning.snapshot import (
        PLANNING_SNAPSHOT_SCHEMA,
        build_planning_snapshot,
        planning_latest_path,
        save_planning_snapshot,
    )

    try:
        snap = build_planning_snapshot(root, product_id)
        gen_path = save_planning_snapshot(root, product_id, snap)
        apply_execution_outcomes(root)
    except Exception as e:
        return {
            "action_status": ACTION_STATUS_FAILED,
            "execution_detail": {"exception_type": type(e).__name__},
            "execution_error": str(e),
        }
    try:
        rel_gen = str(gen_path.relative_to(root))
    except ValueError:
        rel_gen = str(gen_path)
    lp = planning_latest_path(root, product_id)
    try:
        rel_latest = str(lp.relative_to(root))
    except ValueError:
        rel_latest = str(lp)
    ev = snap.get("evidence") if isinstance(snap.get("evidence"), dict) else {}
    rec = snap.get("recommended_actions") if isinstance(snap.get("recommended_actions"), list) else []
    return {
        "action_status": ACTION_STATUS_EXECUTED,
        "execution_detail": {
            "schema": PLANNING_SNAPSHOT_SCHEMA,
            "generation_path": rel_gen,
            "planning_latest_path": rel_latest,
            "posture": snap.get("posture"),
            "planning_mode": snap.get("planning_mode"),
            "source_strategy_generated_at_utc": snap.get("source_strategy_generated_at_utc"),
            "source_decisions_generated_at_utc": snap.get("source_decisions_generated_at_utc"),
            "source_experiment_surfaced_generated_at_utc": snap.get("source_experiment_surfaced_generated_at_utc"),
            "evidence": ev,
            "recommended_action_count": len(rec),
        },
        "execution_error": None,
    }


def _execute_audit_run(root: Path, product_id: str) -> dict[str, Any]:
    try:
        summary = run_audit(root, product_id)
    except Exception as e:
        return {
            "action_status": ACTION_STATUS_FAILED,
            "execution_detail": {"exception_type": type(e).__name__},
            "execution_error": str(e),
        }
    return {
        "action_status": ACTION_STATUS_EXECUTED,
        "execution_detail": {
            "audit_summary_schema": getattr(summary, "schema", None) or "argus.audit_summary.v1",
        },
        "execution_error": None,
    }


def _deterministic_reviews_in_placeholder_payload(stakeholder_keys: list[str]) -> dict[str, Any]:
    """Minimal CURSOR-shaped review objects (see ``load_cursor_review_for_stakeholder``)."""
    inner: dict[str, Any] = {
        "verdict": "concern",
        "blocking": False,
        "confidence_score": 0.5,
        "objection_categories": [],
        "objections": [],
        "suggestions": [],
        "rationale": (
            "argus orchestration step_executor: deterministic placeholder reviews_in; "
            "edit this file with real review content before `argus refine run` if required."
        ),
    }
    return {k: dict(inner) for k in stakeholder_keys}


def _execute_refinement_submit_reviews_in(root: Path, product_id: str) -> dict[str, Any]:
    """
    Write ``reviews_in/round_<n>.json`` for the first session (lexicographic session_id) where
    :func:`refinement_reviews_in_missing` holds. Does not run a refinement cycle.
    """
    inv = build_inventory(root)
    if product_id not in inv.valid:
        return _invalid_product_failure(product_id)

    sessions = list_refinement_sessions_for_product(root, product_id)
    target = None
    for s in sorted(sessions, key=lambda x: x.session_id):
        if refinement_reviews_in_missing(root, s):
            target = s
            break

    if target is None:
        return {
            "action_status": ACTION_STATUS_FAILED,
            "execution_detail": {"reason": "no in_review session with draft and missing reviews_in"},
            "execution_error": (
                "refinement_submit_reviews_in: no session needs reviews_in "
                "(expect in_review, draft present, reviews and reviews_in absent)"
            ),
        }

    sj = read_json(session_json_path(root, target.session_id))
    if not sj:
        return {
            "action_status": ACTION_STATUS_FAILED,
            "execution_detail": {"session_id": target.session_id, "reason": "session.json unreadable"},
            "execution_error": f"cannot read session.json for {target.session_id!r}",
        }

    req = [str(x).strip() for x in (sj.get("required_stakeholders") or []) if str(x).strip()]
    if not req:
        return {
            "action_status": ACTION_STATUS_FAILED,
            "execution_detail": {"session_id": target.session_id, "reason": "required_stakeholders empty"},
            "execution_error": "session has no required_stakeholders — cannot build reviews_in mapping",
        }

    r = int(target.current_round)
    out_path = reviews_in_round_path(root, target.session_id, r)
    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            dumps_json(_deterministic_reviews_in_placeholder_payload(req)) + "\n",
            encoding="utf-8",
        )
    except OSError as e:
        return {
            "action_status": ACTION_STATUS_FAILED,
            "execution_detail": {"session_id": target.session_id, "exception_type": type(e).__name__},
            "execution_error": str(e),
        }

    try:
        rel = str(out_path.relative_to(root))
    except ValueError:
        rel = str(out_path)
    return {
        "action_status": ACTION_STATUS_EXECUTED,
        "execution_detail": {
            "session_id": target.session_id,
            "round_number": r,
            "reviews_in_path": rel,
            "stakeholder_keys_written": list(req),
        },
        "execution_error": None,
    }


def _execute_escalation_consider(root: Path, product_id: str) -> dict[str, Any]:
    """
    Record a durable snapshot of escalation-relevant orchestration posture for operator review.

    Writes ``runs/orchestration/escalation_consider/<product_id>/latest.json``.
    """
    inv = build_inventory(root)
    if product_id not in inv.valid:
        return _invalid_product_failure(product_id)

    state = evaluate_product_orchestration(root, product_id)
    eligible_ids = {
        str(x.get("action_id")).strip()
        for x in (state.get("eligible_actions") or [])
        if isinstance(x, dict) and str(x.get("action_id") or "").strip()
    }
    if ACTION_ESCALATION_CONSIDER not in eligible_ids:
        return {
            "action_status": ACTION_STATUS_FAILED,
            "execution_detail": {
                "reason": "escalation_consider not in eligible_actions",
                "eligible_action_ids": sorted(eligible_ids),
            },
            "execution_error": (
                "escalation_consider is not an eligible action in the current orchestration state"
            ),
        }

    recorded_at = datetime.now(timezone.utc).isoformat()
    eligible_rows: list[dict[str, Any]] = []
    for x in state.get("eligible_actions") or []:
        if not isinstance(x, dict):
            continue
        aid = str(x.get("action_id") or "").strip()
        if not aid:
            continue
        rc = x.get("reason_codes")
        eligible_rows.append(
            {
                "action_id": aid,
                "reason_codes": list(rc) if isinstance(rc, list) else [],
            }
        )

    body: dict[str, Any] = {
        "schema": ORCHESTRATION_ESCALATION_CONSIDER_ARTIFACT_SCHEMA,
        "product_id": product_id,
        "recorded_at_utc": recorded_at,
        "source_evaluated_at_utc": state.get("evaluated_at_utc"),
        "orchestration_status": state.get("orchestration_status"),
        "orchestration_status_reason": state.get("orchestration_status_reason"),
        "escalation_eligible": state.get("escalation_eligible"),
        "escalation_triggers": state.get("escalation_triggers") if isinstance(state.get("escalation_triggers"), list) else [],
        "next_action": state.get("next_action"),
        "eligible_actions": eligible_rows,
    }

    out_path = root / "runs" / "orchestration" / "escalation_consider" / product_id / "latest.json"
    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(dumps_json(body) + "\n", encoding="utf-8")
    except OSError as e:
        return {
            "action_status": ACTION_STATUS_FAILED,
            "execution_detail": {"exception_type": type(e).__name__},
            "execution_error": str(e),
        }

    try:
        rel = str(out_path.relative_to(root))
    except ValueError:
        rel = str(out_path)
    return {
        "action_status": ACTION_STATUS_EXECUTED,
        "execution_detail": {
            "escalation_consider_artifact_path": rel,
            "schema": ORCHESTRATION_ESCALATION_CONSIDER_ARTIFACT_SCHEMA,
        },
        "execution_error": None,
    }


def _execute_escalation_packet_generate(root: Path, product_id: str) -> dict[str, Any]:
    """Materialize an escalation packet (same core path as ``argus escalation generate``)."""
    inv = build_inventory(root)
    if product_id not in inv.valid:
        return _invalid_product_failure(product_id)
    facts = evaluate_product_orchestration(root, product_id).get("eligibility_facts") or {}
    if not facts.get("escalation_packet_generate_eligible"):
        return {
            "action_status": ACTION_STATUS_FAILED,
            "execution_detail": {
                "product_id": product_id,
                "escalation_packet_generate_eligible": False,
            },
            "execution_error": (
                "escalation_packet_generate requires runs/findings/latest and structural escalation posture"
            ),
        }
    from argus.escalation.dedupe import find_recent_duplicate_packet
    from argus.escalation.models import PACKET_SCHEMA
    from argus.escalation.packet import generate_packet_for_product, save_packet
    from argus.findings.persistence import load_latest_findings

    fb = load_latest_findings(root, product_id)
    if fb is None:
        return {
            "action_status": ACTION_STATUS_FAILED,
            "execution_detail": {"product_id": product_id},
            "execution_error": "escalation_packet_generate requires runs/findings/latest/<product_id>.json",
        }
    node = inv.valid[product_id].node
    try:
        pkt, matches = generate_packet_for_product(root, node, fb.findings)
    except Exception as e:
        return {
            "action_status": ACTION_STATUS_FAILED,
            "execution_detail": {"exception_type": type(e).__name__},
            "execution_error": str(e),
        }
    if pkt is None:
        return {
            "action_status": ACTION_STATUS_FAILED,
            "execution_detail": {"escalation_needed": False, "trigger_match_count": 0},
            "execution_error": "escalation_packet_generate: no escalation triggers met under current policy",
        }
    rule_ids = [m.rule_id for m in matches]
    dup = find_recent_duplicate_packet(root, product_id, rule_ids, hours=24.0)
    if dup:
        return {
            "action_status": ACTION_STATUS_EXECUTED,
            "execution_detail": {
                "schema": PACKET_SCHEMA,
                "written": False,
                "dedupe_skipped": True,
                "duplicate_of": dup,
                "packet_id": pkt.packet_id,
                "triggering_rule_count": len(rule_ids),
            },
            "execution_error": None,
        }
    try:
        out_path = save_packet(root, pkt)
        apply_execution_outcomes(root)
    except Exception as e:
        return {
            "action_status": ACTION_STATUS_FAILED,
            "execution_detail": {"exception_type": type(e).__name__},
            "execution_error": str(e),
        }
    try:
        rel = str(out_path.relative_to(root))
    except ValueError:
        rel = str(out_path)
    rl = pkt.risk_level
    risk_s = rl.value if hasattr(rl, "value") else str(rl)
    return {
        "action_status": ACTION_STATUS_EXECUTED,
        "execution_detail": {
            "schema": PACKET_SCHEMA,
            "packet_id": pkt.packet_id,
            "escalation_latest_path": rel,
            "triggering_rule_count": len(rule_ids),
            "risk_level": risk_s,
        },
        "execution_error": None,
    }


def _execute_refinement_run(root: Path, product_id: str) -> dict[str, Any]:
    """One ``argus refine run`` cycle for the session tied to current ``refinement_run`` eligibility."""
    inv = build_inventory(root)
    if product_id not in inv.valid:
        return _invalid_product_failure(product_id)
    state = evaluate_product_orchestration(root, product_id)
    sid = _session_id_for_refinement_run(state)
    if not sid:
        return {
            "action_status": ACTION_STATUS_FAILED,
            "execution_detail": {"reason": "refinement_run session not resolved from eligibility"},
            "execution_error": "refinement_run not in eligible_actions or eligible row has no session id",
        }
    try:
        sess, conv = run_refinement_cycle(root, sid)
    except Exception as e:
        return {
            "action_status": ACTION_STATUS_FAILED,
            "execution_detail": {"session_id": sid, "exception_type": type(e).__name__},
            "execution_error": str(e),
        }
    st = sess.status.value if hasattr(sess.status, "value") else str(sess.status)
    return {
        "action_status": ACTION_STATUS_EXECUTED,
        "execution_detail": {
            "session_id": sid,
            "converged": bool(conv.converged),
            "session_status": st,
            "current_round": sess.current_round,
        },
        "execution_error": None,
    }


def _execute_implementation_plan_generate(root: Path, product_id: str) -> dict[str, Any]:
    """
    Open an implementation_plan refinement session (same as ``argus refine start`` for that type).

    Does not generate a draft or run a cycle — eligibility gates match orchestration's
    ``implementation_plan_generate`` action.
    """
    inv = build_inventory(root)
    if product_id not in inv.valid:
        return _invalid_product_failure(product_id)
    sessions = list_refinement_sessions_for_product(root, product_id)
    existing = pick_latest_session(sessions, ArtifactType.IMPLEMENTATION_PLAN)
    if existing is not None:
        return {
            "action_status": ACTION_STATUS_FAILED,
            "execution_detail": {
                "reason": "implementation_plan refinement session already exists",
                "session_id": existing.session_id,
            },
            "execution_error": (
                f"refinement session already exists for implementation_plan: {existing.session_id}"
            ),
        }
    state = evaluate_product_orchestration(root, product_id)
    facts = state.get("eligibility_facts") if isinstance(state.get("eligibility_facts"), dict) else {}
    if not facts.get("implementation_plan_generate_eligible"):
        prog = (state.get("progression") or {}).get("implementation_plan") or {}
        codes = prog.get("blocked_reason_codes") if isinstance(prog, dict) else []
        if not isinstance(codes, list):
            codes = []
        return {
            "action_status": ACTION_STATUS_FAILED,
            "execution_detail": {
                "reason": "implementation_plan_generate prerequisites not met",
                "blocked_reason_codes": codes,
                "product_spec_finalized": facts.get("product_spec_finalized"),
                "implementation_plan_session_absent": facts.get("implementation_plan_session_absent"),
            },
            "execution_error": (
                "implementation_plan_generate is not eligible "
                f"(blocked_reason_codes={codes!r})"
            ),
        }
    try:
        sess = create_session(
            root,
            ArtifactType.IMPLEMENTATION_PLAN,
            product_id,
            product_id=product_id,
            max_rounds=4,
        )
    except Exception as e:
        return {
            "action_status": ACTION_STATUS_FAILED,
            "execution_detail": {"exception_type": type(e).__name__},
            "execution_error": str(e),
        }
    try:
        rel = str((root / "runs" / "refinement" / sess.session_id / "session.json").relative_to(root))
    except ValueError:
        rel = str(root / "runs" / "refinement" / sess.session_id / "session.json")
    return {
        "action_status": ACTION_STATUS_EXECUTED,
        "execution_detail": {
            "session_id": sess.session_id,
            "artifact_type": ArtifactType.IMPLEMENTATION_PLAN.value,
            "source_id": product_id,
            "session_json_path": rel,
        },
        "execution_error": None,
    }


def _execute_refinement_start_product_spec(root: Path, product_id: str) -> dict[str, Any]:
    """Create a product_spec refinement session (same durable layout as ``argus refine start``)."""
    inv = build_inventory(root)
    if product_id not in inv.valid:
        return _invalid_product_failure(product_id)
    sessions = list_refinement_sessions_for_product(root, product_id)
    existing = pick_latest_session(sessions, ArtifactType.PRODUCT_SPEC)
    if existing is not None:
        return {
            "action_status": ACTION_STATUS_FAILED,
            "execution_detail": {
                "reason": "product_spec refinement session already exists",
                "session_id": existing.session_id,
            },
            "execution_error": (
                f"refinement session already exists for product_spec: {existing.session_id}"
            ),
        }
    try:
        sess = create_session(
            root,
            ArtifactType.PRODUCT_SPEC,
            product_id,
            product_id=product_id,
            max_rounds=4,
        )
    except Exception as e:
        return {
            "action_status": ACTION_STATUS_FAILED,
            "execution_detail": {"exception_type": type(e).__name__},
            "execution_error": str(e),
        }
    try:
        rel = str((root / "runs" / "refinement" / sess.session_id / "session.json").relative_to(root))
    except ValueError:
        rel = str(root / "runs" / "refinement" / sess.session_id / "session.json")
    return {
        "action_status": ACTION_STATUS_EXECUTED,
        "execution_detail": {
            "session_id": sess.session_id,
            "artifact_type": ArtifactType.PRODUCT_SPEC.value,
            "session_json_path": rel,
        },
        "execution_error": None,
    }


def _execute_refinement_start_idea(root: Path, product_id: str) -> dict[str, Any]:
    """
    Create an idea refinement session (``ArtifactType.IDEA``), same durable layout as
    ``argus refine start --type idea --source <idea_id>``.

    Does not run ``generate_initial_draft`` — first draft is created on ``refine run`` (optional LLM there).
    """
    inv = build_inventory(root)
    if product_id not in inv.valid:
        return _invalid_product_failure(product_id)
    facts = evaluate_product_orchestration(root, product_id).get("eligibility_facts") or {}
    if not facts.get("refinement_start_idea_eligible"):
        return {
            "action_status": ACTION_STATUS_FAILED,
            "execution_detail": {
                "product_id": product_id,
                "refinement_start_idea_eligible": False,
            },
            "execution_error": (
                "refinement_start_idea requires a product-scoped runs/ideas/latest.json, "
                "a deterministic idea pick, and no non-terminal refinement session for that idea"
            ),
        }
    from argus.orchestrator.refinement_idea_pick import (
        ORCHESTRATION_IDEA_SELECTION_RULE,
        deterministic_idea_id_for_refinement_orchestration,
        has_non_terminal_idea_refinement_for_source,
    )

    idea_id = deterministic_idea_id_for_refinement_orchestration(root, product_id)
    if not idea_id:
        return {
            "action_status": ACTION_STATUS_FAILED,
            "execution_detail": {"product_id": product_id, "reason": "no_deterministic_idea_id"},
            "execution_error": (
                "refinement_start_idea: no resolvable idea_id "
                "(ideas latest missing, wrong product_id, or no eligible ideas)"
            ),
        }
    if has_non_terminal_idea_refinement_for_source(root, product_id, idea_id):
        return {
            "action_status": ACTION_STATUS_FAILED,
            "execution_detail": {
                "product_id": product_id,
                "source_idea_id": idea_id,
                "reason": "active_idea_refinement_session_exists",
            },
            "execution_error": (
                f"refinement_start_idea: active idea refinement already exists for source {idea_id!r}"
            ),
        }
    try:
        sess = create_session(
            root,
            ArtifactType.IDEA,
            idea_id,
            product_id=product_id,
            max_rounds=4,
        )
        apply_execution_outcomes(root)
    except Exception as e:
        return {
            "action_status": ACTION_STATUS_FAILED,
            "execution_detail": {"exception_type": type(e).__name__},
            "execution_error": str(e),
        }
    try:
        rel = str((root / "runs" / "refinement" / sess.session_id / "session.json").relative_to(root))
    except ValueError:
        rel = str(root / "runs" / "refinement" / sess.session_id / "session.json")
    return {
        "action_status": ACTION_STATUS_EXECUTED,
        "execution_detail": {
            "session_id": sess.session_id,
            "artifact_type": ArtifactType.IDEA.value,
            "source_idea_id": idea_id,
            "idea_selection_rule": ORCHESTRATION_IDEA_SELECTION_RULE,
            "initial_draft": "deferred_to_refine_run",
            "draft_generation_in_this_action": "none",
            "refinement_authority_note": "draft_review_loop_only_not_approval_or_execution",
            "session_json_path": rel,
        },
        "execution_error": None,
    }


def _execute_execution_outcomes_apply(root: Path, product_id: str) -> dict[str, Any]:
    """
    Apply execution JSON under ``runs/execution/<product_id>/`` to experiments when pending per
    :func:`pending_unapplied_execution_outcomes_for_product` (does not consume other products' files).
    """
    if not pending_unapplied_execution_outcomes_for_product(root, product_id):
        return {
            "action_status": ACTION_STATUS_FAILED,
            "execution_detail": {"reason": "no_unapplied_execution_json_for_product"},
            "execution_error": (
                "execution_outcomes_apply: no pending execution JSON under runs/execution/<product_id>/ "
                "relative to apply state (experiments/_execution_apply_state.json)"
            ),
        }
    try:
        report = apply_execution_outcomes(root, product_id=product_id)
    except Exception as e:
        return {
            "action_status": ACTION_STATUS_FAILED,
            "execution_detail": {"exception_type": type(e).__name__},
            "execution_error": str(e),
        }
    return {
        "action_status": ACTION_STATUS_EXECUTED,
        "execution_detail": {
            "schema": report.schema,
            "product_id": report.product_id,
            "files_seen": report.files_seen,
            "files_applied": report.files_applied,
            "experiments_updated": list(report.experiments_updated),
            "skipped_no_link": report.skipped_no_link,
        },
        "execution_error": None,
    }


def _execute_orchestration_state_refresh(root: Path, product_id: str) -> dict[str, Any]:
    try:
        path = write_orchestration_state(root, product_id)
    except Exception as e:
        return {
            "action_status": ACTION_STATUS_FAILED,
            "execution_detail": {"exception_type": type(e).__name__},
            "execution_error": str(e),
        }
    try:
        rel_state = str(path.relative_to(root))
    except ValueError:
        rel_state = str(path)
    return {
        "action_status": ACTION_STATUS_EXECUTED,
        "execution_detail": {"orchestration_state_path": rel_state},
        "execution_error": None,
    }


# In-process handlers: ``action_id`` -> ``(root, product_id) -> result``.
STEP_EXECUTION_REGISTRY: dict[str, Callable[[Path, str], dict[str, Any]]] = {
    ACTION_SIGNALS_COLLECT: _execute_signals_collect,
    ACTION_AUDIT_RUN: _execute_audit_run,
    ACTION_ORCHESTRATION_STATE_REFRESH: _execute_orchestration_state_refresh,
    ACTION_REFINEMENT_START_PRODUCT_SPEC: _execute_refinement_start_product_spec,
    ACTION_REFINEMENT_START_IDEA: _execute_refinement_start_idea,
    ACTION_IMPLEMENTATION_PLAN_GENERATE: _execute_implementation_plan_generate,
    ACTION_REFINEMENT_RUN: _execute_refinement_run,
    ACTION_REFINEMENT_SUBMIT_REVIEWS_IN: _execute_refinement_submit_reviews_in,
    ACTION_ESCALATION_CONSIDER: _execute_escalation_consider,
    ACTION_ESCALATION_PACKET_GENERATE: _execute_escalation_packet_generate,
    ACTION_EXECUTION_OUTCOMES_APPLY: _execute_execution_outcomes_apply,
    ACTION_TEMPORAL_REFRESH: _execute_temporal_refresh,
    ACTION_FINDINGS_GENERATE: _execute_findings_generate,
    ACTION_DECISIONS_GENERATE: _execute_decisions_generate,
    ACTION_DECISIONS_REFRESH_FROM_SURFACED_FINDINGS: _execute_decisions_refresh_from_surfaced_findings,
    ACTION_IDEAS_GENERATE: _execute_ideas_generate,
    ACTION_IDEAS_REFRESH_FROM_SURFACED_FINDINGS: _execute_ideas_refresh_from_surfaced_findings,
    ACTION_EXPERIMENTS_PROPOSE: _execute_experiments_propose,
    ACTION_EXPERIMENTS_PRIORITIZE: _execute_experiments_prioritize,
    ACTION_EXPERIMENTS_CREATE: _execute_experiments_create,
    ACTION_EXPERIMENTS_ACTIVATE: _execute_experiments_activate,
    ACTION_EXPERIMENTS_EVALUATE: _execute_experiments_evaluate,
    ACTION_EXPERIMENTS_CLOSE_STALE: _execute_experiments_close_stale,
    ACTION_EXPERIMENTS_SURFACE_FINDINGS: _execute_experiments_surface_findings,
    ACTION_STRATEGY_REFRESH_FROM_DECISION_EVOLUTION: _execute_strategy_refresh_from_decision_evolution,
    ACTION_PLANNING_REFRESH_FROM_STRATEGY: _execute_planning_refresh_from_strategy,
}

validate_orchestration_registry_phase1_mapping(STEP_EXECUTION_REGISTRY.keys())
