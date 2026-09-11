"""
Artifact coherence verification — deterministic checks that canonical ``runs/`` artifacts align
with downstream consumers (lifecycle, operator surfaces, strategy) without split-brain state.

Read-only evaluation; optional durable report under ``runs/debug/artifact_coherence/``.
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Final

from argus.core.serialize import dumps_json, to_jsonable
from argus.portfolio.cycle import PORTFOLIO_CYCLE_SCHEMA, portfolio_cycle_dir
from argus.portfolio.delta_report import PORTFOLIO_DELTA_REPORT_SCHEMA
from argus.portfolio.intervention import PORTFOLIO_INTERVENTION_SCHEMA, portfolio_intervention_dir
from argus.portfolio.intervention_inbox import INTERVENTION_INBOX_SCHEMA
from argus.portfolio.lifecycle import evaluate_portfolio_lifecycle
from argus.portfolio.operator_queue import OPERATOR_QUEUE_SCHEMA
from argus.portfolio.outcomes import PORTFOLIO_OUTCOMES_SCHEMA, portfolio_outcomes_dir
from argus.portfolio.patterns import PORTFOLIO_PATTERNS_SCHEMA
from argus.portfolio.strategy import PORTFOLIO_STRATEGY_SCHEMA, portfolio_strategy_dir
from argus.products.creation import PRODUCT_CREATION_PROPOSALS_SCHEMA, creation_proposals_dir

ARTIFACT_COHERENCE_REPORT_SCHEMA: Final = "argus.artifact_coherence_report.v1"
PORTFOLIO_AUTONOMOUS_RUNNER_SCHEMA: Final = "argus.portfolio_autonomous_runner.v1"


def portfolio_autonomous_runner_artifacts_dir(repo_root: Path) -> Path:
    return Path(repo_root).resolve() / "runs" / "portfolio" / "autonomous_runner"


def artifact_coherence_dir(repo_root: Path) -> Path:
    return Path(repo_root).resolve() / "runs" / "debug" / "artifact_coherence"


def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return raw if isinstance(raw, dict) else None


def _canonical_outcomes_present(repo_root: Path) -> tuple[bool, str | None]:
    p = portfolio_outcomes_dir(repo_root) / "latest.json"
    raw = _load_json(p)
    if not raw:
        return False, str(p)
    if str(raw.get("schema") or "") != PORTFOLIO_OUTCOMES_SCHEMA:
        return False, str(p)
    return True, str(p)


def _canonical_intervention_present(repo_root: Path) -> tuple[bool, str | None]:
    p = portfolio_intervention_dir(repo_root) / "latest.json"
    raw = _load_json(p)
    if not raw:
        return False, str(p)
    if str(raw.get("schema") or "") != PORTFOLIO_INTERVENTION_SCHEMA:
        return False, str(p)
    return True, str(p)


def _cycle_intervention_flagged_count(cycle_pl: dict[str, Any] | None) -> int:
    if not cycle_pl or str(cycle_pl.get("schema") or "") != PORTFOLIO_CYCLE_SCHEMA:
        return 0
    summ = cycle_pl.get("summary") or {}
    inv = summ.get("intervention") or {}
    fp = inv.get("flagged_products") or []
    return len(fp) if isinstance(fp, list) else 0


def _load_cycle_latest(repo_root: Path) -> dict[str, Any] | None:
    p = portfolio_cycle_dir(repo_root) / "latest.json"
    raw = _load_json(p)
    if raw and str(raw.get("schema") or "") == PORTFOLIO_CYCLE_SCHEMA:
        return raw
    return None


def _load_strategy_latest(repo_root: Path) -> dict[str, Any] | None:
    p = portfolio_strategy_dir(repo_root) / "latest.json"
    raw = _load_json(p)
    if raw and str(raw.get("schema") or "") == PORTFOLIO_STRATEGY_SCHEMA:
        return raw
    return None


def _artifact_exists_schema(
    repo_root: Path,
    rel_dir: Path,
    filename: str,
    schema: str,
) -> tuple[bool, str]:
    p = Path(repo_root).resolve() / rel_dir / filename
    raw = _load_json(p)
    ok = bool(raw and str(raw.get("schema") or "") == schema)
    return ok, str(p)


def evaluate_artifact_coherence(
    repo_root: Path,
    *,
    products_dir: Path | None = None,
) -> dict[str, Any]:
    """
    Evaluate coherence invariants from on-disk artifacts only (no hidden recompute of outcomes/intervention).
    """
    root = Path(repo_root).resolve()
    evaluated_at = datetime.now(timezone.utc).isoformat()
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    findings: list[dict[str, Any]] = []
    suggested: list[str] = []

    # --- Invariant 1: outcomes canonicality ---
    oc_present, oc_path = _canonical_outcomes_present(root)
    lc = evaluate_portfolio_lifecycle(root, products_dir=products_dir)
    lc_inputs = lc.get("inputs") or {}
    lifecycle_outcomes_flag = bool(lc_inputs.get("outcomes_artifact_present"))

    oc_status = "pass"
    oc_details: list[str] = []
    consumers_consistent = True
    if oc_present != lifecycle_outcomes_flag:
        oc_status = "fail"
        consumers_consistent = False
        findings.append(
            {
                "severity": "high",
                "code": "artifact_coherence.outcomes.lifecycle_inputs_mismatch",
                "title": "Lifecycle outcomes flag disagrees with canonical outcomes file",
                "detail": f"Canonical present={oc_present} vs lifecycle outcomes_artifact_present={lifecycle_outcomes_flag}.",
                "recommended_action": "Re-run `argus portfolio cycle` (refreshes outcomes satellite) or repair runs/portfolio/outcomes/latest.json.",
            }
        )
    if not oc_present:
        oc_status = "fail" if oc_status == "pass" else oc_status
        consumers_consistent = False
        findings.append(
            {
                "severity": "high",
                "code": "artifact_coherence.outcomes.canonical_missing",
                "title": "Canonical portfolio outcomes artifact absent or wrong schema",
                "detail": f"Expected {oc_path} with schema {PORTFOLIO_OUTCOMES_SCHEMA}.",
                "recommended_action": "Run `argus portfolio outcomes` or a full portfolio cycle with stage writes enabled.",
            }
        )
        suggested.append("Materialize runs/portfolio/outcomes/latest.json before trusting operator surfaces.")

    # --- Invariant 2: intervention durability ---
    int_present, int_path = _canonical_intervention_present(root)
    cycle_pl = _load_cycle_latest(root)
    c_flagged = _cycle_intervention_flagged_count(cycle_pl)

    int_status = "pass"
    int_details: list[str] = []
    counts_consistent = True
    if c_flagged > 0 and not int_present:
        int_status = "fail"
        counts_consistent = False
        findings.append(
            {
                "severity": "high",
                "code": "artifact_coherence.intervention.flagged_without_canonical_report",
                "title": "Cycle reports intervention flags but canonical intervention artifact missing",
                "detail": f"portfolio_cycle latest shows {c_flagged} flagged product(s); {int_path} missing or invalid schema.",
                "recommended_action": "Run `argus portfolio intervention` or `argus portfolio cycle` with stage persistence.",
            }
        )
        suggested.append("Ensure runs/portfolio/intervention/latest.json exists whenever cycle summary lists flagged products.")

    if cycle_pl and not int_present and c_flagged == 0:
        int_details.append("Cycle present; intervention report absent but zero flagged — acceptable.")

    # --- Autonomous session vs canonical intervention (ghost corridor) ---
    ar_status = "pass"
    ar_details: list[str] = []
    session_max_flagged = 0
    session_last_cycle_run_id: str | None = None
    ar_path = portfolio_autonomous_runner_artifacts_dir(root) / "latest.json"
    ar_sess = _load_json(ar_path)
    session_pressure_without_canonical = False
    stale_autonomous_vs_cycle = False
    last_fc = 0
    if ar_sess and str(ar_sess.get("schema") or "") == PORTFOLIO_AUTONOMOUS_RUNNER_SCHEMA:
        pcs = [x for x in (ar_sess.get("per_cycle_outcomes") or []) if isinstance(x, dict)]
        for row in reversed(pcs):
            pc = row.get("portfolio_cycle")
            if isinstance(pc, dict):
                last_fc = int(pc.get("intervention_flagged_count") or 0)
                cr = pc.get("cycle_run_id")
                session_last_cycle_run_id = str(cr).strip() if cr else None
                break
        for row in pcs:
            pc = row.get("portfolio_cycle")
            if not isinstance(pc, dict):
                continue
            fc = int(pc.get("intervention_flagged_count") or 0)
            if fc > session_max_flagged:
                session_max_flagged = fc
        if session_max_flagged > 0 and not int_present:
            session_pressure_without_canonical = True
            ar_status = "fail"
            findings.append(
                {
                    "severity": "high",
                    "code": "artifact_coherence.intervention.autonomous_session_pressure_without_canonical",
                    "title": "Autonomous session records intervention pressure without canonical intervention report",
                    "detail": (
                        f"{ar_path} shows per-cycle intervention_flagged_count up to {session_max_flagged} "
                        f"but {int_path} is missing or not schema {PORTFOLIO_INTERVENTION_SCHEMA}."
                    ),
                    "recommended_action": "Run `argus portfolio intervention` or a portfolio cycle with stage writes.",
                }
            )
            suggested.append(
                "Align autonomous session history with durable intervention artifacts before trusting intervention-heavy stops."
            )
        cyc_rid = str((cycle_pl or {}).get("run_id") or "")
        if session_last_cycle_run_id and cyc_rid and session_last_cycle_run_id != cyc_rid:
            stale_autonomous_vs_cycle = True
            ar_status = "warn" if ar_status == "pass" else ar_status
            ar_details.append(
                f"Autonomous session last cycle_run_id ({session_last_cycle_run_id}) "
                f"≠ portfolio cycle latest run_id ({cyc_rid}) — session may be stale."
            )
            findings.append(
                {
                    "severity": "medium",
                    "code": "artifact_coherence.autonomous_session.stale_vs_cycle_latest",
                    "title": "Autonomous runner session does not match latest portfolio cycle",
                    "detail": "Compare stamped autonomous session JSON with runs/portfolio/cycle/latest.json run_id.",
                    "recommended_action": "Re-run `argus portfolio run-autonomous` or ignore stale session when auditing.",
                }
            )
        if (
            session_last_cycle_run_id
            and cyc_rid
            and session_last_cycle_run_id == cyc_rid
            and last_fc != c_flagged
        ):
            ar_details.append(
                f"Same cycle run id but autonomous session last intervention_flagged_count={last_fc} "
                f"vs cycle summary flagged count={c_flagged}."
            )
            findings.append(
                {
                    "severity": "medium",
                    "code": "artifact_coherence.intervention.autonomous_vs_cycle_flag_mismatch",
                    "title": "Autonomous session intervention count disagrees with portfolio cycle summary",
                    "detail": (
                        f"cycle_run_id={cyc_rid}: per_cycle portfolio_cycle.intervention_flagged_count={last_fc}, "
                        f"cycle summary={c_flagged}."
                    ),
                    "recommended_action": "Re-read artifacts from disk; avoid trusting session-only intervention metrics.",
                }
            )
    else:
        ar_details.append("No argus.portfolio_autonomous_runner.v1 latest.json — autonomous alignment skipped.")

    # --- Invariant 3: strategy input integrity ---
    strat = _load_strategy_latest(root)
    st_status = "pass"
    required: dict[str, bool] = {}
    present: dict[str, bool] = {}
    st_details: list[str] = []

    def _mark(key: str, req: bool, ok: bool) -> None:
        required[key] = req
        present[key] = ok

    oq_ok, _ = _artifact_exists_schema(
        root, Path("runs/portfolio/operator_queue"), "latest.json", OPERATOR_QUEUE_SCHEMA
    )
    pat_ok, _ = _artifact_exists_schema(
        root, Path("runs/portfolio/patterns"), "latest.json", PORTFOLIO_PATTERNS_SCHEMA
    )
    delta_ok, _ = _artifact_exists_schema(
        root, Path("runs/portfolio/delta_report"), "latest.json", PORTFOLIO_DELTA_REPORT_SCHEMA
    )
    cp_path = creation_proposals_dir(root) / "latest.json"
    cp_raw = _load_json(cp_path)
    cp_ok = bool(cp_raw and str(cp_raw.get("schema") or "") == PRODUCT_CREATION_PROPOSALS_SCHEMA)

    _mark("operator_queue", True, oq_ok)
    _mark("portfolio_patterns", True, pat_ok)
    _mark("portfolio_outcomes_canonical", True, oc_present)
    _mark("portfolio_intervention_canonical", True, int_present)
    inb_ok, _ = _artifact_exists_schema(
        root, Path("runs/portfolio/intervention_inbox"), "latest.json", INTERVENTION_INBOX_SCHEMA
    )
    _mark("intervention_inbox", True, inb_ok)
    _mark("portfolio_delta_report", False, delta_ok)
    _mark("creation_proposals", False, cp_ok)

    posture = str((strat or {}).get("strategic_posture") or "").strip().lower()
    if strat:
        inp = strat.get("inputs") or {}
        cr_loaded = bool(inp.get("creation_proposals_loaded"))
        if posture == "create" and not cr_loaded and not cp_ok:
            st_status = "warn"
            st_details.append("Posture is `create` but creation proposals artifact not loaded/present.")
            findings.append(
                {
                    "severity": "medium",
                    "code": "artifact_coherence.strategy.create_without_creation_proposals",
                    "title": "Strategy posture `create` without creation proposals artifact",
                    "detail": "inputs.creation_proposals_loaded is false and runs/products/creation/latest.json missing or invalid.",
                    "recommended_action": "Run creation proposals pipeline or treat strategy posture as low-confidence.",
                }
            )
        if not oc_present:
            st_status = "warn" if st_status == "pass" else st_status
            st_details.append("Strategy on disk may have been built without canonical outcomes.")
        if not inb_ok:
            st_status = "warn" if st_status == "pass" else st_status
            st_details.append("intervention_inbox latest.json missing or invalid schema.")
        if not oq_ok or not pat_ok:
            st_status = "warn" if st_status == "pass" else st_status
            st_details.append(
                "operator_queue or portfolio_patterns latest missing/invalid (strategy inputs incomplete)."
            )
        ca = (inp or {}).get("canonical_artifacts")
        if isinstance(ca, dict):
            st_details.append(f"strategy.inputs.canonical_artifacts snapshot: {ca}")

    else:
        st_status = "warn"
        st_details.append("No portfolio strategy latest.json — posture/integrity unknown.")
        findings.append(
            {
                "severity": "medium",
                "code": "artifact_coherence.strategy.missing",
                "title": "Portfolio strategy artifact absent",
                "detail": "runs/portfolio/strategy/latest.json missing or wrong schema.",
                "recommended_action": "Run `argus portfolio strategy` or a portfolio cycle with satellite refresh.",
            }
        )

    # --- Invariant 4: hidden recomputation (static / policy) ---
    hr_status = "pass"
    hr_details = [
        "Operator summary uses _load_canonical_portfolio_outcomes; narrative uses outcomes.load_canonical_portfolio_outcomes (no silent evaluate).",
        "Intervention inbox view is built from canonical intervention report + actions.",
    ]
    hr_suspect: list[str] = []

    high_n = sum(1 for f in findings if f.get("severity") == "high")
    med_n = sum(1 for f in findings if f.get("severity") == "medium")
    if (
        high_n
        or not oc_present
        or not consumers_consistent
        or (c_flagged > 0 and not int_present)
        or session_pressure_without_canonical
    ):
        overall_status = "invalid"
    elif med_n >= 2 or st_status == "warn":
        overall_status = "degraded"
    elif med_n == 1:
        overall_status = "warning"
    else:
        overall_status = "valid"

    summary_parts = [f"Overall: **{overall_status}**."]
    if oc_present:
        summary_parts.append("Canonical outcomes: present.")
    else:
        summary_parts.append("Canonical outcomes: MISSING.")
    if int_present:
        summary_parts.append("Canonical intervention report: present.")
    else:
        summary_parts.append("Canonical intervention report: missing or invalid.")
    if high_n:
        summary_parts.append(f"{high_n} high-severity finding(s).")
    summary = " ".join(summary_parts)

    payload: dict[str, Any] = {
        "schema": ARTIFACT_COHERENCE_REPORT_SCHEMA,
        "run_id": run_id,
        "evaluated_at_utc": evaluated_at,
        "repo_root": str(root),
        "overall_status": overall_status,
        "summary": summary,
        "checks": {
            "outcomes_canonicality": {
                "status": oc_status,
                "artifact_present": oc_present,
                "consumers_consistent": consumers_consistent,
                "details": oc_details,
            },
            "intervention_durability": {
                "status": int_status,
                "artifact_present": int_present,
                "counts_consistent": counts_consistent,
                "cycle_flagged_count": c_flagged,
                "details": int_details,
            },
            "strategy_input_integrity": {
                "status": st_status,
                "required_inputs": required,
                "present_inputs": present,
                "strategic_posture": posture or None,
                "details": st_details,
            },
            "hidden_recomputation": {
                "status": hr_status,
                "suspect_code_paths": hr_suspect,
                "details": hr_details,
            },
            "autonomous_runner_alignment": {
                "status": ar_status,
                "session_artifact_present": bool(
                    ar_sess and str(ar_sess.get("schema") or "") == PORTFOLIO_AUTONOMOUS_RUNNER_SCHEMA
                ),
                "session_max_intervention_flagged_count": session_max_flagged,
                "session_last_intervention_flagged_count": last_fc,
                "session_last_cycle_run_id": session_last_cycle_run_id,
                "session_pressure_without_canonical": session_pressure_without_canonical,
                "stale_autonomous_vs_cycle_latest": stale_autonomous_vs_cycle,
                "details": ar_details,
            },
        },
        "findings": findings,
        "suggested_next_actions": suggested + [
            "Run `argus portfolio artifact-coherence --json` after each cycle during stabilization.",
        ],
        "artifact_paths_checked": {
            "portfolio_outcomes_latest": oc_path,
            "portfolio_intervention_latest": int_path,
            "portfolio_cycle_latest": str(portfolio_cycle_dir(root) / "latest.json"),
            "portfolio_strategy_latest": str(portfolio_strategy_dir(root) / "latest.json"),
            "portfolio_autonomous_runner_latest": str(ar_path),
        },
    }
    return payload


def format_artifact_coherence_cli_summary(payload: dict[str, Any]) -> str:
    """Concise text for terminal use (not markdown tables)."""
    status = str(payload.get("overall_status") or "unknown")
    lines: list[str] = [
        f"Artifact coherence: {status}",
        "",
        "Checks:",
    ]
    for name, block in sorted((payload.get("checks") or {}).items()):
        if isinstance(block, dict):
            lines.append(f"  - {name}: {block.get('status')}")
    findings = [f for f in (payload.get("findings") or []) if isinstance(f, dict)]
    if findings:
        lines.extend(["", "Top findings:"])
        for f in findings[:12]:
            sev = f.get("severity")
            title = f.get("title")
            lines.append(f"  [{sev}] {title}")
    sug = [s for s in (payload.get("suggested_next_actions") or []) if s]
    if sug:
        lines.extend(["", "Suggested actions:"])
        for a in sug[:8]:
            lines.append(f"  - {a}")
    summ = str(payload.get("summary") or "").strip()
    if summ:
        lines.extend(["", summ])
    return "\n".join(lines).rstrip() + "\n"


def render_artifact_coherence_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Artifact coherence",
        "",
        f"**Overall:** `{payload.get('overall_status')}`",
        f"**Evaluated (UTC):** {payload.get('evaluated_at_utc')}",
        f"**Run id:** `{payload.get('run_id')}`",
        "",
        "## Summary",
        "",
        str(payload.get("summary") or "—"),
        "",
        "## Checks",
        "",
    ]
    for name, block in sorted((payload.get("checks") or {}).items()):
        if not isinstance(block, dict):
            continue
        lines.append(f"### {name}")
        lines.append("")
        lines.append(f"- **status:** `{block.get('status')}`")
        for k, v in sorted(block.items()):
            if k == "status" or k == "details":
                continue
            lines.append(f"- **{k}:** {v}")
        for d in block.get("details") or []:
            lines.append(f"- {d}")
        lines.append("")
    lines.extend(["## Findings", ""])
    for f in payload.get("findings") or []:
        if not isinstance(f, dict):
            continue
        lines.append(
            f"- **[{f.get('severity')}]** `{f.get('code')}` — {f.get('title')}: {f.get('detail')}"
        )
    lines.extend(["", "## Suggested next actions", ""])
    for a in payload.get("suggested_next_actions") or []:
        lines.append(f"- {a}")
    lines.append("")
    return "\n".join(lines)


def write_artifact_coherence_artifacts(
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
    d = artifact_coherence_dir(root)
    d.mkdir(parents=True, exist_ok=True)
    stamped_json = d / f"{rid}.json"
    stamped_md = d / f"{rid}.md"
    latest_json = d / "latest.json"
    latest_md = d / "latest.md"
    body = dumps_json(to_jsonable(pl)) + "\n"
    stamped_json.write_text(body, encoding="utf-8")
    stamped_md.write_text(render_artifact_coherence_markdown(pl), encoding="utf-8")
    shutil.copyfile(stamped_json, latest_json)
    shutil.copyfile(stamped_md, latest_md)
    return stamped_json, stamped_md, latest_json, latest_md


def run_artifact_coherence(
    repo_root: Path,
    *,
    products_dir: Path | None = None,
    write_artifacts: bool = True,
) -> dict[str, Any]:
    payload = evaluate_artifact_coherence(repo_root, products_dir=products_dir)
    if write_artifacts:
        write_artifact_coherence_artifacts(repo_root, payload)
    return payload


def load_artifact_coherence_operational_snapshot(repo_root: Path) -> dict[str, Any]:
    """
    Read-only view of ``runs/debug/artifact_coherence/latest.json`` for dashboards and heartbeats.

    Does not evaluate coherence; returns ``{"present": false}`` when missing or wrong schema.
    """
    root = Path(repo_root).resolve()
    p = artifact_coherence_dir(root) / "latest.json"
    raw = _load_json(p)
    if not raw or str(raw.get("schema") or "") != ARTIFACT_COHERENCE_REPORT_SCHEMA:
        return {"present": False}
    checks = raw.get("checks") or {}
    preview: list[dict[str, Any]] = []
    for name, block in sorted(checks.items()):
        if isinstance(block, dict):
            st = str(block.get("status") or "")
            if st in ("fail", "warn"):
                preview.append({"check": name, "status": st})
    return {
        "present": True,
        "overall_status": raw.get("overall_status"),
        "run_id": raw.get("run_id"),
        "evaluated_at_utc": raw.get("evaluated_at_utc"),
        "summary": raw.get("summary"),
        "checks_preview": preview[:16],
    }


def coherence_strict_should_fail(
    overall_status: str | None,
    *,
    strict_mode: str,
) -> bool:
    """Return whether a process should exit non-zero under the given strict policy."""
    s = str(overall_status or "").strip().lower()
    if strict_mode == "invalid-only":
        return s == "invalid"
    if strict_mode == "all":
        return s != "valid"
    return False


__all__ = [
    "ARTIFACT_COHERENCE_REPORT_SCHEMA",
    "artifact_coherence_dir",
    "coherence_strict_should_fail",
    "evaluate_artifact_coherence",
    "format_artifact_coherence_cli_summary",
    "load_artifact_coherence_operational_snapshot",
    "portfolio_autonomous_runner_artifacts_dir",
    "render_artifact_coherence_markdown",
    "run_artifact_coherence",
    "write_artifact_coherence_artifacts",
]
