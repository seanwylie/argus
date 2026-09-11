"""CLI: ``argus doctor`` — local health checks for operator workflows."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from argus.cli.repo import repo_root
from argus.core.serialize import dumps_json
from argus.dashboard.strategy_planning_read import build_doctor_strategy_planning_section
from argus.decision.persistence import latest_portfolio_path
from argus.disk_budget import collect_doctor_disk_section
from argus.experiments.store import list_experiments
from argus.orchestrator.portfolio_priorities import read_portfolio_priorities_json
from argus.orchestrator.portfolio_priority_trends import read_portfolio_priority_trends_json
from argus.products.inventory import build_inventory
from argus.strategy.apply import load_strategy_record
from argus.temporal.visibility import build_doctor_temporal_report


def _products_dir(repo: Path, override: Path | None) -> Path | None:
    if override is None:
        return None
    return override.resolve()


def _mtime(path: Path) -> float | None:
    try:
        return path.stat().st_mtime
    except OSError:
        return None


def _collect_spine(repo: Path) -> dict[str, Any]:
    """
    Structured checks for adapter config, light JSON parse on ``runs/signals/latest/*.json``,
    autonomy ``blocked_actions.json``, and the latest loop harness manifest.

    ``runs/ideas/latest.json`` is validated elsewhere in doctor; ``validate_repo_artifacts`` does not
    cover signals/latest, so this always performs a light parse there without duplicating deep checks.
    """
    adapter_warnings: list[str] = []
    loop_harness_notes: list[str] = []

    try:
        from argus.adapters.loader import load_adapter_config
        from argus.adapters.registry import registered_adapters

        cfg = load_adapter_config(repo)
        reg = registered_adapters()
        raw_ids = cfg.get("enabled_ids")
        if isinstance(raw_ids, list):
            for eid in raw_ids:
                s = str(eid).strip()
                if s and s not in reg:
                    adapter_warnings.append(
                        f"config/adapters.json enabled_ids references unknown adapter id {s!r} "
                        "(not registered; check `argus adapters list`)"
                    )
    except Exception as e:  # pragma: no cover
        adapter_warnings.append(f"adapter registry check skipped: {e}")

    blk = repo / "runs" / "autonomy" / "blocked_actions.json"
    if blk.is_file():
        try:
            raw = json.loads(blk.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                loop_harness_notes.append(
                    "runs/autonomy/blocked_actions.json: expected a JSON object after parse"
                )
        except (OSError, json.JSONDecodeError) as e:
            loop_harness_notes.append(f"runs/autonomy/blocked_actions.json: not parseable ({e})")

    loop_base = repo / "runs" / "loop"
    if loop_base.is_dir():
        subs = [d for d in loop_base.iterdir() if d.is_dir() and not d.name.startswith(".")]
        if subs:
            latest = max(subs, key=lambda p: p.name)
            man = latest / "manifest.json"
            if man.is_file():
                try:
                    mraw = json.loads(man.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError) as e:
                    loop_harness_notes.append(f"{man.relative_to(repo)}: not parseable ({e})")
                else:
                    if isinstance(mraw, dict):
                        stages = mraw.get("stages")
                        if isinstance(stages, list):
                            for st in stages:
                                if not isinstance(st, dict):
                                    continue
                                if st.get("ok") is False:
                                    stage_name = st.get("stage", "?")
                                    err = st.get("error")
                                    err_bit = f" — {err}" if err else ""
                                    loop_harness_notes.append(
                                        f"Latest loop run {latest.name}: stage {stage_name!r} failed{err_bit}"
                                    )

    sig_latest = repo / "runs" / "signals" / "latest"
    if sig_latest.is_dir():
        for p in sorted(sig_latest.glob("*.json")):
            try:
                json.loads(p.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as e:
                loop_harness_notes.append(f"{p.relative_to(repo)}: not parseable ({e})")

    chain_hint = (
        "`argus loop full` does not run escalation — use `argus escalation generate <product_id>` "
        "when you need escalation packets."
    )

    return {
        "adapter_warnings": adapter_warnings,
        "loop_harness_notes": loop_harness_notes,
        "chain_hint": chain_hint,
    }


def _collect_bounded_autonomy(repo: Path) -> dict[str, Any]:
    """Structured checks for tier vs policy caps (JSON-only detail; human lines go to `info`)."""
    notes: list[str] = []
    tier4_capped = False
    try:
        from argus.autonomy.operator_policy import effective_policy, load_autonomy_config
        from argus.autonomy.tiers import AutonomyTier, infer_tier, tier4_enabled

        mode, pol, _tier_eff = effective_policy(repo)
        _, _ov, tier_raw = load_autonomy_config(repo)
        stored = infer_tier(tier_raw, mode)
        if stored == AutonomyTier.FULL_AUTONOMY and not tier4_enabled():
            tier4_capped = True
            notes.append(
                "tier 4 stored in autonomy.json but ARGUS_ENABLE_TIER4 not set — effective execution tier is capped at 3"
            )
        if pol.min_confidence_autonomous > 0.85:
            notes.append(
                "min_confidence_autonomous>0.85 — expect most autonomous execution paths to be blocked without strong assessments"
            )
        if pol.max_actions_per_run > 5000:
            notes.append("max_actions_per_run is very high — confirm policy_overrides are intentional")
        if pol.max_shutdowns_per_utc_day > 30:
            notes.append("max_shutdowns_per_utc_day is high — confirm policy_overrides are intentional")
        if mode.value == "off" and tier_raw is not None and int(tier_raw) >= 2:
            notes.append("mode is OFF but tier>=2 in config — tier applies when you switch to a non-off mode")
    except Exception as e:  # pragma: no cover
        notes.append(f"check skipped: {e}")
    return {"schema": "argus.doctor_bounded_autonomy.v1", "tier4_capped": tier4_capped, "notes": notes}


def _print_disk_cli(disk: dict[str, Any]) -> None:
    approx = int(disk.get("runs_dir_approx_bytes") or 0)
    files_n = int(disk.get("runs_dir_files_counted") or 0)
    capped = bool(disk.get("runs_dir_tree_capped"))
    free_b = disk.get("volume_free_bytes")
    total_b = disk.get("volume_total_bytes")
    need = disk.get("min_free_bytes_required")

    def mib(x: int) -> str:
        return f"{x / (1024 * 1024):.1f}"

    print("\nDisk / runs footprint")
    print("---------------------")
    cap_note = " (sample capped; tree large)" if capped else ""
    print(f"  runs/ (approx): {mib(approx)} MiB ({files_n} files){cap_note}")
    if isinstance(free_b, int) and isinstance(total_b, int):
        print(f"  Volume free / total: {mib(free_b)} / {mib(total_b)} MiB")
    elif isinstance(free_b, int):
        print(f"  Volume free: {mib(free_b)} MiB")
    else:
        print("  Volume usage: (unavailable)")
    if isinstance(need, int) and need > 0:
        print(f"  Headroom hint: warn when free < {mib(need)} MiB (ARGUS_MIN_FREE_DISK_MB)")
    else:
        print("  Headroom hint: disabled (ARGUS_MIN_FREE_DISK_MB<=0)")


def _print_spine_cli(spine: dict[str, Any]) -> None:
    """Human-readable spine section (structured ``spine`` mirrors JSON)."""
    aw = spine.get("adapter_warnings") or []
    lh = spine.get("loop_harness_notes") or []
    if not aw and not lh:
        return
    print("\nSpine checks")
    print("-----------")
    if aw:
        print("\nAdapters:")
        for line in aw:
            print(f"  - {line}")
    if lh:
        print("\nHarness / artifacts (light):")
        for line in lh:
            print(f"  - {line}")


def _print_temporal_cli(temporal_report: dict[str, Any]) -> None:
    """Human-readable temporal section (categories map to doctor JSON)."""
    bc = temporal_report.get("by_category") or {}
    blocks = (
        ("Missing current data", "missing_current"),
        ("Stale current data", "stale_current"),
        ("Malformed artifacts", "malformed"),
        ("Integrity / timestamps", "integrity"),
    )
    any_block = False
    for title, key in blocks:
        rows = bc.get(key) or []
        if not rows:
            continue
        if not any_block:
            print("\nTemporal pipeline")
            print("----------------")
            any_block = True
        print(f"\n{title}:")
        for line in rows:
            print(f"  - {line}")
    nr = bc.get("not_required") or []
    if nr:
        print("\nNo temporal requirement")
        print("------------------------")
        for line in nr:
            print(f"  - {line}")


def cmd_doctor(repo: Path, args: Any) -> int:
    strict = bool(getattr(args, "strict", False))
    errors: list[str] = []
    warnings: list[str] = []
    info: list[str] = []

    products_dir = (repo / "products") if args.products_dir is None else args.products_dir.resolve()
    if not products_dir.is_dir():
        errors.append(f"products directory missing or not a directory: {products_dir}")

    runs = repo / "runs"
    if not runs.is_dir():
        warnings.append(f"runs/ not present yet ({runs}); artifact commands will create it.")

    inv = build_inventory(repo, products_dir=_products_dir(repo, args.products_dir))
    for x in inv.invalid:
        label = x.product_id or "(unknown id)"
        for e in x.errors:
            errors.append(f"invalid product {label}: {e}")

    # Referenced scripts / YAML shape: covered by inventory invalid entries above.

    for pid, rec in inv.valid.items():
        cfg = (repo / rec.node.config_path).resolve()
        cm = _mtime(cfg)
        if cm is None:
            warnings.append(f"{pid}: product.yaml not readable at {cfg}")
            continue

    temporal_report = build_doctor_temporal_report(repo, inv.valid)

    strategy_planning = build_doctor_strategy_planning_section(repo, sorted(inv.valid.keys()))
    for n in strategy_planning.get("notes") or []:
        info.append(n)

    for c in temporal_report["checks"]:
        if c["severity"] == "warning":
            warnings.append(c["message"])
        elif c["severity"] == "info":
            # Downstream status is listed under Temporal pipeline; avoid duplicating in Info.
            if c.get("code") == "pipeline_incomplete_while_signals_missing":
                continue
            info.append(c["message"])

    pp = latest_portfolio_path(repo)

    pp_orch_path = repo / "runs" / "orchestration" / "latest" / "portfolio_priorities.json"
    pp_payload, pp_err = read_portfolio_priorities_json(repo)
    r1_pid: str | None = None
    r1_reasons: list[str] | None = None
    if pp_payload:
        prows = pp_payload.get("products") or []
        r1_row = None
        if isinstance(prows, list):
            r1_row = next(
                (p for p in prows if isinstance(p, dict) and int(p.get("rank") or 0) == 1),
                prows[0] if prows and isinstance(prows[0], dict) else None,
            )
        if isinstance(r1_row, dict):
            r1_pid = str(r1_row.get("product_id") or "").strip() or None
            pr = r1_row.get("priority_reasons")
            r1_reasons = [str(x) for x in pr] if isinstance(pr, list) else None
    orchestration_portfolio_priorities_doctor: dict[str, Any] = {
        "artifact_path_repo": "runs/orchestration/latest/portfolio_priorities.json",
        "present": pp_payload is not None,
        "readable": pp_err is None and pp_payload is not None,
        "error": pp_err,
        "recommended_product_id": pp_payload.get("recommended_product_id") if pp_payload else None,
        "recommended_next_action": pp_payload.get("recommended_next_action") if pp_payload else None,
        "product_count": len(pp_payload.get("products") or []) if pp_payload else 0,
        "rank_1_product_id": r1_pid,
        "rank_1_priority_reasons": r1_reasons,
    }
    if pp_err:
        warnings.append(f"{pp_orch_path.relative_to(repo)}: {pp_err}")
    elif pp_payload is not None:
        rid = pp_payload.get("recommended_product_id")
        na = pp_payload.get("recommended_next_action")
        info.append(
            f"Orchestration portfolio priorities: recommended {rid!r} → {na!r} "
            f"({orchestration_portfolio_priorities_doctor['artifact_path_repo']})"
        )

    pt_trends_path = repo / "runs" / "orchestration" / "latest" / "portfolio_priority_trends.json"
    tr_payload, tr_err = read_portfolio_priority_trends_json(repo)
    top_inspect = tr_payload.get("top_products_to_inspect") if tr_payload else None
    top_first = None
    if isinstance(top_inspect, list) and top_inspect:
        top_first = str(top_inspect[0]).strip() or None
    ore = tr_payload.get("operator_recommendations") if tr_payload else None
    portfolio_trends_doctor: dict[str, Any] = {
        "artifact_path_repo": "runs/orchestration/latest/portfolio_priority_trends.json",
        "present": tr_payload is not None,
        "readable": tr_err is None and tr_payload is not None,
        "error": tr_err,
        "window_size": tr_payload.get("window_size") if tr_payload else None,
        "generations_considered": tr_payload.get("generations_considered") if tr_payload else None,
        "churn_summary": tr_payload.get("churn_summary") if tr_payload else None,
        "portfolio_stability": tr_payload.get("portfolio_stability") if tr_payload else None,
        "portfolio_stability_score": tr_payload.get("portfolio_stability_score") if tr_payload else None,
        "top_products_to_inspect": list(top_inspect) if isinstance(top_inspect, list) else None,
        "operator_recommendations": list(ore) if isinstance(ore, list) else None,
        "top_inspect_first_product_id": top_first,
    }
    if tr_err:
        warnings.append(f"{pt_trends_path.relative_to(repo)}: {tr_err}")
    elif tr_payload is not None:
        stab = tr_payload.get("portfolio_stability")
        ngen = tr_payload.get("generations_considered")
        info.append(
            f"Portfolio priority trends: stability={stab!r} "
            f"({ngen} generation(s) in window) — {portfolio_trends_doctor['artifact_path_repo']}"
        )

    strat = load_strategy_record(repo)
    if strat is not None:
        mode = strat.get("mode")
        if isinstance(mode, str):
            info.append(f"Active strategy mode: {mode} (`argus strategy show`)")

    dash = repo / "runs" / "dashboard" / "index.html"
    if pp.is_file() and not dash.is_file():
        info.append("Portfolio report exists but no runs/dashboard/index.html — run `argus dashboard`")

    hist_snapshots = repo / "runs" / "history" / "snapshots"
    if hist_snapshots.is_dir() and any(hist_snapshots.glob("*.json")):
        tr_latest = repo / "runs" / "trends" / "latest.json"
        if not tr_latest.is_file():
            warnings.append(
                "History snapshots exist but no runs/trends/latest.json; run `argus trends analyze` or `argus trends summary`"
            )

    # Decisions without persisted assessment (operator visibility / explainability)
    dec_latest = repo / "runs" / "decisions" / "latest"
    if dec_latest.is_dir():
        for pid in sorted(inv.valid.keys()):
            dpath = dec_latest / f"{pid}.json"
            apath = repo / "runs" / "decision_assessment" / "latest" / f"{pid}.json"
            if dpath.is_file() and not apath.is_file():
                info.append(
                    f"{pid}: decisions present without runs/decision_assessment/latest/{pid}.json — "
                    f"run `argus confidence assess {pid}`"
                )

    assess_dir = repo / "runs" / "decision_assessment" / "latest"
    if assess_dir.is_dir():
        for ap in sorted(assess_dir.glob("*.json")):
            try:
                raw_as = json.loads(ap.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                warnings.append(f"{ap.relative_to(repo)}: not valid JSON")
                continue
            if not isinstance(raw_as, dict):
                continue
            try:
                ep = float(raw_as.get("escalation_pressure", 0.0))
            except (TypeError, ValueError):
                continue
            if ep >= 0.85:
                pid = str(raw_as.get("product_id") or ap.stem)
                warnings.append(
                    f"{pid}: high escalation_pressure ({ep:.2f}) — review `argus confidence explain {pid}`"
                )

    try:
        from argus.refinement.doctor import check_refinement_health

        ref_err, ref_warn = check_refinement_health(repo)
        errors.extend(ref_err)
        warnings.extend(ref_warn)
    except Exception as e:  # pragma: no cover
        warnings.append(f"refinement doctor check skipped: {e}")

    try:
        from argus.audit.doctor import check_audit_artifacts

        ae, aw, ai_audit = check_audit_artifacts(repo)
        errors.extend(ae)
        warnings.extend(aw)
        info.extend(ai_audit)
    except Exception as e:  # pragma: no cover
        warnings.append(f"audit doctor check skipped: {e}")

    try:
        from argus.council.doctor import check_council_profiles

        ce, cw = check_council_profiles()
        errors.extend(ce)
        warnings.extend(cw)
    except Exception as e:  # pragma: no cover
        warnings.append(f"council doctor check skipped: {e}")

    ideas_latest = repo / "runs" / "ideas" / "latest.json"
    if ideas_latest.is_file():
        try:
            raw_ideas = json.loads(ideas_latest.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            warnings.append("runs/ideas/latest.json exists but is not valid JSON")
        else:
            if not isinstance(raw_ideas, dict):
                warnings.append("runs/ideas/latest.json must be a JSON object")
            elif raw_ideas.get("schema") not in (None, "argus.ideas_bundle.v1"):
                info.append(
                    f"runs/ideas/latest.json: schema is {raw_ideas.get('schema')!r} "
                    "(expected argus.ideas_bundle.v1 or absent)"
                )

    # Experiments: orphan records pointing at unknown products
    try:
        for exp in list_experiments(repo):
            if exp.product_id not in inv.valid:
                warnings.append(
                    f"experiment {exp.id} references product {exp.product_id!r} not in valid inventory"
                )
    except (OSError, ValueError, TypeError):
        warnings.append("could not enumerate experiments under runs/experiments/")

    # Advisor per-product overrides: invalid JSON
    for pid, rec in inv.valid.items():
        adv_p = (repo / "products" / pid / "advisors.json").resolve()
        if adv_p.is_file():
            try:
                raw = json.loads(adv_p.read_text(encoding="utf-8"))
                if not isinstance(raw, dict):
                    warnings.append(f"{pid}: advisors.json must be a JSON object")
            except (OSError, json.JSONDecodeError):
                warnings.append(f"{pid}: advisors.json exists but is not valid JSON")

    # Portfolio attention allocation is on-demand; nudge if refresh exists but operator never ran allocate
    port_latest = repo / "runs" / "portfolio" / "latest"
    if port_latest.is_dir() and (port_latest / "refresh.json").is_file():
        info.append(
            "Portfolio refresh present — run `argus portfolio allocate` for attention % when planning focus"
        )

    # Capabilities evaluation artifact
    cap_latest = repo / "runs" / "capabilities" / "latest.json"
    if inv.summary.valid_count and not cap_latest.is_file():
        info.append(
            "No runs/capabilities/latest.json — run `argus capabilities evaluate` to snapshot gap analysis"
        )

    # Economics: resource registry + cost ingest → resources_latest.json (dashboard + orphan/HCLV signals)
    cfg_econ = repo / "config" / "economics" / "resources.json"
    ingest_econ = repo / "runs" / "economics" / "cost_ingest.json"
    reg_econ = repo / "runs" / "economics" / "resource_registry.json"
    latest_econ = repo / "runs" / "economics" / "resources_latest.json"
    if cfg_econ.is_file():
        try:
            raw_cfg = json.loads(cfg_econ.read_text(encoding="utf-8"))
            if raw_cfg is not None and not isinstance(raw_cfg, (dict, list)):
                warnings.append("config/economics/resources.json must be a JSON object or array")
        except (OSError, json.JSONDecodeError):
            warnings.append("config/economics/resources.json exists but is not valid JSON")
    if not latest_econ.is_file() and (ingest_econ.is_file() or reg_econ.is_file()):
        info.append(
            "Economics resource linkage not materialized — run `argus economics resources` "
            "(writes runs/economics/resources_latest.json for the dashboard)"
        )
    econ_sources = [p for p in (cfg_econ, ingest_econ, reg_econ) if p.is_file()]
    max_econ_src: float | None = None
    for p in econ_sources:
        m = _mtime(p)
        if m is not None and (max_econ_src is None or m > max_econ_src):
            max_econ_src = m
    lm = _mtime(latest_econ)
    if max_econ_src is not None and lm is not None and lm < max_econ_src:
        warnings.append(
            "runs/economics/resources_latest.json is older than economics config/ingest/registry — "
            "run `argus economics resources`"
        )
    if latest_econ.is_file():
        try:
            er = json.loads(latest_econ.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            warnings.append("runs/economics/resources_latest.json exists but is not valid JSON")
        else:
            if isinstance(er, dict):
                try:
                    toc_f = float(er["total_orphan_cost_usd"])
                except (KeyError, TypeError, ValueError):
                    toc_f = 0.0
                orphans = er.get("orphan_resources") if isinstance(er.get("orphan_resources"), list) else []
                if toc_f > 0.01 and orphans:
                    warnings.append(
                        f"Economics: ${toc_f:.2f}/mo unmapped resource cost ({len(orphans)} line item(s)) — "
                        "review `argus economics resources`"
                    )
                hclv = er.get("high_cost_low_value") if isinstance(er.get("high_cost_low_value"), list) else []
                if hclv:
                    warnings.append(
                        f"Economics: {len(hclv)} product(s) flagged high-cost/low-value — see `argus economics resources`"
                    )

    # Optional doctrine.yaml per product (machine-readable policy)
    try:
        from argus.doctrine.load import load_doctrine_yaml

        for pid, rec in inv.valid.items():
            dpath = (repo / rec.node.product_root.strip().lstrip("/") / "doctrine.yaml").resolve()
            if not dpath.is_file():
                continue
            _doc, err = load_doctrine_yaml(dpath)
            if err:
                errors.append(f"{pid}: doctrine.yaml invalid ({dpath.relative_to(repo)}): {err}")
    except Exception as e:  # pragma: no cover
        warnings.append(f"doctrine validation skipped: {e}")

    # Autonomy operator config + state (execution safety caps)
    apath = repo / "runs" / "autonomy" / "autonomy.json"
    if apath.is_file():
        try:
            raw_am = json.loads(apath.read_text(encoding="utf-8"))
            if not isinstance(raw_am, dict):
                warnings.append("runs/autonomy/autonomy.json must be a JSON object")
            else:
                from argus.autonomy.models import AutonomyMode

                m = str(raw_am.get("mode", "")).strip().lower()
                allowed = {x.value for x in AutonomyMode}
                if m and m not in allowed:
                    warnings.append(
                        f"runs/autonomy/autonomy.json: unknown mode {raw_am.get('mode')!r} "
                        f"(expected one of {', '.join(sorted(allowed))})"
                    )
                if raw_am.get("tier") is not None:
                    try:
                        tr = int(raw_am["tier"])
                        if tr < 0 or tr > 4:
                            warnings.append(
                                f"runs/autonomy/autonomy.json: tier must be 0–4, got {raw_am.get('tier')!r}"
                            )
                    except (TypeError, ValueError):
                        warnings.append("runs/autonomy/autonomy.json: tier must be an integer 0–4")
        except json.JSONDecodeError:
            warnings.append("runs/autonomy/autonomy.json is not valid JSON")
    else:
        info.append(
            "First-run: no runs/autonomy/autonomy.json — defaults are permissive; "
            "for supervised Tier 1 (suggest-only) run: `uv run argus run safe-profile` (see docs/first-run.md)"
        )

    try:
        from argus.autonomy.operator_policy import load_autonomy_config
        from argus.autonomy.tiers import infer_tier

        mode, _ov, tier_raw = load_autonomy_config(repo)
        eff_tier = int(infer_tier(tier_raw, mode))
        if eff_tier > 2 and (repo / "runs" / "autonomy" / "autonomy.json").is_file():
            info.append(
                f"Supervised first-run: effective autonomy tier is {eff_tier} — "
                "for suggest-only analysis use Tier 1: `uv run argus run safe-profile`"
            )
    except Exception:
        pass

    st_aut = repo / "runs" / "autonomy" / "state.json"
    if st_aut.is_file():
        try:
            st_raw = json.loads(st_aut.read_text(encoding="utf-8"))
            if isinstance(st_raw, dict):
                bs = int(float(st_raw.get("block_streak", 0)))
                if bs >= 3:
                    warnings.append(
                        f"autonomy block_streak={bs} — repeated policy blocks; "
                        "review `argus capabilities request list` and autonomy mode (`argus autonomy show`)"
                    )
        except (OSError, json.JSONDecodeError, ValueError, TypeError):
            warnings.append("runs/autonomy/state.json exists but is not readable JSON")

    # Pending human approvals (action execution gate)
    try:
        from argus.approval.models import ApprovalStatus
        from argus.approval.store import list_records

        pending_ap = sum(1 for r in list_records(repo) if r.status == ApprovalStatus.PENDING)
        if pending_ap > 0:
            warnings.append(
                f"Pending approval record(s): {pending_ap} — `argus approval list` / approve or reject"
            )
    except Exception as e:  # pragma: no cover
        warnings.append(f"could not scan approval records: {e}")

    # Open capability requests (blocked features / policy)
    try:
        from argus.capabilities.requests.models import CapabilityRequestStatus
        from argus.capabilities.requests.store import list_requests

        open_cr = sum(1 for r in list_requests(repo) if r.status == CapabilityRequestStatus.OPEN)
        if open_cr > 0:
            info.append(
                f"Open capability request(s): {open_cr} — `argus capabilities request list`"
            )
    except Exception as e:  # pragma: no cover
        warnings.append(f"could not enumerate capability requests: {e}")

    # Execution lock (crash mid-run)
    ex_lock = repo / "runs" / "execution" / ".lock"
    if ex_lock.is_file():
        warnings.append(
            "runs/execution/.lock present — verify no stuck execution process before re-running `argus execution run`"
        )

    # Simulation: no persistent failure state; ensure experiments dir readable (covered above)

    if getattr(args, "validate_artifacts", False):
        try:
            from argus.validation.validate import validate_repo_artifacts

            rep = validate_repo_artifacts(repo)
            if not rep.ok:
                for i in rep.issues:
                    if i.severity == "error":
                        warnings.append(f"artifact validation: {i.path}: {i.message}")
            else:
                info.append("Artifact validation: no structural issues in runs/ artifacts")
        except Exception as e:  # pragma: no cover
            warnings.append(f"artifact validation skipped: {e}")

    bounded_autonomy = _collect_bounded_autonomy(repo)
    for n in bounded_autonomy.get("notes") or []:
        info.append(f"Bounded autonomy: {n}")

    spine = _collect_spine(repo)
    spine_warn_keys: set[str] = set()
    for w in spine.get("adapter_warnings") or []:
        warnings.append(w)
        spine_warn_keys.add(w)
    for w in spine.get("loop_harness_notes") or []:
        warnings.append(w)
        spine_warn_keys.add(w)
    info.append(str(spine["chain_hint"]))

    disk_report = collect_doctor_disk_section(repo)
    ldw = disk_report.get("low_disk_warning")
    if isinstance(ldw, str) and ldw.strip():
        warnings.append(ldw)

    out: dict[str, Any] = {
        "ok": not errors and (not strict or not warnings),
        "errors": errors,
        "warnings": warnings,
        "info": info,
        "inventory": {
            "valid_count": inv.summary.valid_count,
            "invalid_count": inv.summary.invalid_count,
        },
        "temporal": temporal_report,
        "spine": spine,
        "disk": disk_report,
        "bounded_autonomy": bounded_autonomy,
        "orchestration_portfolio_priorities": orchestration_portfolio_priorities_doctor,
        "portfolio_priority_trends": portfolio_trends_doctor,
        "strategy_planning": strategy_planning,
    }

    if args.json:
        print(dumps_json(out))
    else:
        print("Argus doctor")
        print("===========")
        if errors:
            print("\nErrors:")
            for e in errors:
                print(f"  - {e}")
        temporal_warn_msgs = {c["message"] for c in temporal_report["checks"] if c["severity"] == "warning"}
        _print_temporal_cli(temporal_report)
        other_warnings = [
            w for w in warnings if w not in temporal_warn_msgs and w not in spine_warn_keys
        ]
        if other_warnings:
            print("\nOther warnings:")
            for w in other_warnings:
                print(f"  - {w}")
        _print_spine_cli(spine)
        _print_disk_cli(disk_report)
        if info:
            print("\nInfo:")
            for i in info:
                print(f"  - {i}")
        if not errors and not warnings:
            print("\nNo issues detected.")
        elif not errors:
            print("\n(no blocking errors)")
        if strict and warnings:
            print("\nStrict mode: warnings count as failure.", file=sys.stderr)

    exit_if_bad = bool(errors) or (strict and bool(warnings))
    return 1 if exit_if_bad else 0


def run_doctor_command(args: Any) -> int:
    return cmd_doctor(repo_root(), args)
