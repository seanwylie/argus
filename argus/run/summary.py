"""Human-readable summaries for ``runs/loop/<run_id>/`` harness outputs."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from argus.decision.persistence import load_latest_portfolio, load_latest_product_decisions
from argus.decision_assessment.models import DecisionContextAssessment
from argus.decision_assessment.persistence import load_latest_assessment
from argus.escalation.dedupe import summarize_escalation_groups
from argus.escalation.packet import list_packets
from argus.findings.persistence import load_latest_findings


def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return raw if isinstance(raw, dict) else None


def iter_loop_run_dirs(repo_root: Path) -> list[Path]:
    base = repo_root.resolve() / "runs" / "loop"
    if not base.is_dir():
        return []
    dirs = [d for d in base.iterdir() if d.is_dir() and not d.name.startswith(".")]
    return sorted(dirs, key=lambda p: p.stat().st_mtime, reverse=True)


def latest_loop_run_id(repo_root: Path) -> str | None:
    dirs = iter_loop_run_dirs(repo_root)
    return dirs[0].name if dirs else None


def _stub_and_freshness_for_product(repo_root: Path, product_id: str) -> tuple[str | None, int]:
    dec_raw = load_latest_product_decisions(repo_root, product_id)
    if not dec_raw:
        return None, 0
    cands = dec_raw.get("candidates") or []
    if not cands or not isinstance(cands[0], dict):
        return None, 0
    md = cands[0].get("metadata") if isinstance(cands[0].get("metadata"), dict) else {}
    sa = md.get("stub_awareness")
    stub_s = str(sa).strip() if sa is not None else None
    fw = md.get("freshness_warnings") or []
    nfw = len(fw) if isinstance(fw, list) else 0
    return (stub_s or None), nfw


def _format_assessment_explain(ass: DecisionContextAssessment | None, stub: str | None, n_fw: int) -> str:
    if ass is None:
        base = "conf=? unc=? risk=? esc_pressure=? (no assessment)"
    else:
        base = (
            f"conf={ass.confidence_score:.2f} unc={ass.uncertainty_score:.2f} "
            f"risk={ass.risk_score:.2f} esc_pressure={ass.escalation_pressure:.2f}"
        )
    stub_part = f" stub_impact={stub[:120]}" if stub else " stub_impact=(none)"
    fresh_part = f" temporal_freshness_warnings={n_fw}"
    return base + stub_part + fresh_part


def build_human_run_summary(repo_root: Path, run_id: str) -> str:
    """Multi-section plain-text report for operators."""
    root = repo_root.resolve()
    run_dir = root / "runs" / "loop" / run_id
    lines: list[str] = []
    summ = _load_json(run_dir / "summary.json")
    if not summ:
        return f"No summary.json found for run_id={run_id!r} (expected {run_dir / 'summary.json'})\n"

    lines.append("=" * 72)
    lines.append(f"Argus loop run summary  run_id={run_id}")
    lines.append("=" * 72)
    ok = summ.get("ok")
    lines.append(f"Status: {'ok' if ok else 'FAILED (see stages)'}  exit_code={summ.get('exit_code', '?')}")
    lines.append(f"Dry-run execution flag: {summ.get('dry_run_execution', True)}")
    lines.append(f"Started: {summ.get('started_at_utc', '?')}  Finished: {summ.get('finished_at_utc', '?')}")
    targets: list[str] = list(summ.get("target_product_ids") or [])
    lines.append(f"Products analyzed: {len(targets)}  {', '.join(targets[:12])}{' …' if len(targets) > 12 else ''}")
    lines.append("")

    lines.append("--- Stages ---")
    for row in summ.get("stages") or []:
        st = row.get("stage", "?")
        good = row.get("ok")
        ms = row.get("duration_ms", 0)
        err = row.get("error")
        mark = "ok" if good else "FAIL"
        line = f"  [{mark}] {st}  ({ms:.1f} ms)"
        if err:
            line += f"  — {err}"
        lines.append(line)
    lines.append("")

    cap_st = _load_json(run_dir / "stages" / "capability_load" / "output.json")
    lines.append("--- Capability load (this run) ---")
    if cap_st:
        lines.append(
            f"  missing_capabilities={cap_st.get('missing_count', '?')}  "
            f"artifact={cap_st.get('evaluation_path', '?')}"
        )
    else:
        lines.append("  (no capability_load/output.json for this run)")
    lines.append("")

    # Findings counts
    lines.append("--- Findings (latest bundles) ---")
    for pid in targets[:32]:
        fb = load_latest_findings(root, pid)
        n = len(fb.findings) if fb else 0
        lines.append(f"  {pid}: {n} finding(s)")
    if not targets:
        lines.append("  (none)")
    lines.append("")

    # Portfolio / top decisions
    lines.append("--- Top decisions (latest portfolio) ---")
    port = load_latest_portfolio(root)
    ranked = port.get("ranked") if isinstance(port, dict) else None
    if isinstance(ranked, list) and ranked:
        for row in ranked[:12]:
            if not isinstance(row, dict):
                continue
            pid = row.get("product_id", "?")
            intent = row.get("top_intent", "")
            ps = row.get("priority_score")
            summ_line = str(row.get("summary", ""))[:120]
            lines.append(f"  {pid}: intent={intent}  score={ps}  {summ_line}")
    else:
        lines.append("  (no portfolio snapshot — run portfolio refresh or ensure decisions exist)")
    lines.append("")

    # Confidence distribution
    lines.append("--- Decision context (confidence / uncertainty / risk) ---")
    conf_vals: list[float] = []
    for pid in targets:
        ass = load_latest_assessment(root, pid)
        if ass is None:
            lines.append(f"  {pid}: (no assessment artifact)")
        else:
            conf_vals.append(float(ass.confidence_score))
            lines.append(
                f"  {pid}: conf={ass.confidence_score:.2f} unc={ass.uncertainty_score:.2f} "
                f"risk={ass.risk_score:.2f} esc_pressure={ass.escalation_pressure:.2f}"
            )
        dec_raw = load_latest_product_decisions(root, pid)
        if dec_raw:
            cands = dec_raw.get("candidates") or []
            if cands and isinstance(cands[0], dict):
                md = cands[0].get("metadata") if isinstance(cands[0].get("metadata"), dict) else {}
                sa = md.get("stub_awareness")
                if sa:
                    lines.append(f"    stub (top candidate): {sa}")
                fw = md.get("freshness_warnings") or []
                if isinstance(fw, list) and fw:
                    lines.append(f"    temporal freshness warnings (top candidate): {len(fw)}")
    if conf_vals:
        lines.append(
            f"  Distribution: min={min(conf_vals):.2f} max={max(conf_vals):.2f} mean={sum(conf_vals)/len(conf_vals):.2f}"
        )
    lines.append("")

    # Ideas
    lines.append("--- Ideas (runs/ideas/latest.json) ---")
    ideas_path = root / "runs" / "ideas" / "latest.json"
    iraw = _load_json(ideas_path)
    if iraw and isinstance(iraw.get("ideas"), list):
        ideas = iraw["ideas"]
        types = Counter(str(i.get("type", "?")) for i in ideas if isinstance(i, dict))
        lines.append(f"  Total: {len(ideas)}  by type: {dict(types)}")
        need = {"exploit", "explore", "invent"}
        have = {k for k in types if k in need}
        if have >= need:
            lines.append("  Mix: exploit / explore / invent all present.")
        elif types:
            lines.append(f"  Mix note: missing types vs policy target {need - have}")
        meta = iraw.get("meta") if isinstance(iraw.get("meta"), dict) else {}
        div = meta.get("portfolio_diversity") if meta else None
        if isinstance(div, dict):
            lines.append(f"  diversity meta: {json.dumps(div, sort_keys=True)[:200]}…")
        doctrine_i = meta.get("doctrine_ideas") if meta else None
        if doctrine_i:
            lines.append(f"  doctrine_ideas: {doctrine_i}")
    else:
        lines.append("  (missing or empty ideas bundle)")
    lines.append("")

    # Experiments stage
    st_ex = _load_json(run_dir / "stages" / "experiments" / "output.json")
    if st_ex:
        lines.append("--- Experiments stage ---")
        lines.append(f"  {json.dumps(st_ex, indent=2)[:1200]}")
        lines.append("")

    # Planning actions + per-action explainability (joins assessment + decision metadata)
    assess_cache: dict[str, DecisionContextAssessment | None] = {
        pid: load_latest_assessment(root, pid) for pid in targets
    }
    stub_cache: dict[str, tuple[str | None, int]] = {
        pid: _stub_and_freshness_for_product(root, pid) for pid in targets
    }

    act_path = root / "runs" / "planning" / "actions.json"
    araw = _load_json(act_path)
    if araw and isinstance(araw.get("actions"), list):
        acts = araw["actions"]
        lines.append(f"--- Proposed actions ({len(acts)} in runs/planning/actions.json) ---")
        lines.append("  (each line: contract; next line: explainability for the action's product)")
        for a in acts[:12]:
            if not isinstance(a, dict):
                continue
            aid = a.get("action_id", "?")
            pid = str(a.get("product_id") or "?")
            cmd = str(a.get("command", ""))[:72]
            lines.append(f"  {aid} [{pid}] {cmd}")
            ass = assess_cache.get(pid)
            st, nfw = stub_cache.get(pid, (None, 0))
            lines.append(f"    {_format_assessment_explain(ass, st, nfw)}")
        if len(acts) > 12:
            lines.append(f"  … +{len(acts) - 12} more (see actions.json)")
    else:
        lines.append("--- Proposed actions ---")
        lines.append("  (no actions.json)")
    lines.append("")

    # Execution dry-run + blocked actions
    dryp = run_dir / "stages" / "execution" / "dry_run_results.json"
    drew_raw: list[Any] | None = None
    dry_unreadable = False
    if dryp.is_file():
        try:
            raw_list = json.loads(dryp.read_text(encoding="utf-8"))
            drew_raw = raw_list if isinstance(raw_list, list) else None
        except (OSError, json.JSONDecodeError):
            drew_raw = None
            dry_unreadable = True
    if drew_raw is not None:
        bad = [x for x in drew_raw if isinstance(x, dict) and not x.get("ok")]
        lines.append("--- Blocked actions (execution dry-run validation failed) ---")
        if bad:
            for x in bad[:16]:
                lines.append(
                    f"  {x.get('action_id')}: errors={x.get('validation_errors')} "
                    f"flags={x.get('dangerous_flags')}"
                )
            if len(bad) > 16:
                lines.append(f"  … +{len(bad) - 16} more")
        else:
            lines.append("  (none — all sampled contracts passed static checks)")
        lines.append("")
        lines.append("--- Execution stage (static dry-run checks) ---")
        lines.append(f"  Contracts checked: {len(drew_raw)}  validation failures: {len(bad)}")
    else:
        lines.append("--- Blocked actions ---")
        if dry_unreadable:
            lines.append("  (dry_run_results.json present but invalid JSON)")
        else:
            lines.append("  (no dry_run_results.json in this run dir)")
        lines.append("")
        lines.append("--- Execution stage ---")
        if dry_unreadable:
            lines.append("  (dry_run_results.json present but invalid JSON)")
        else:
            lines.append("  (no dry_run_results.json in this run dir)")
    lines.append("")

    # Escalations
    lines.append("--- Escalations (repo) ---")
    pk = list_packets(root, limit=24)
    lines.append(f"  Recent packets (up to 24): {len(pk)}")
    try:
        ded = summarize_escalation_groups(root)
        lines.append(f"  Dedupe groups: {len(ded.get('groups', [])) if isinstance(ded, dict) else 'n/a'}")
    except Exception as e:
        lines.append(f"  (dedupe summary unavailable: {e})")
    lines.append("")

    chain = summ.get("chain") or {}
    nxt = chain.get("next_commands") or []
    if nxt:
        lines.append("--- Next commands (from summary chain) ---")
        for c in nxt[:8]:
            lines.append(f"  {c}")
        lines.append("")

    lines.append(f"Artifacts: {summ.get('run_dir_repo_relative', 'runs/loop/' + run_id)}/")
    lines.append("  summary.json  summary.txt (if generated)  manifest.json  stages/*/")
    return "\n".join(lines) + "\n"


def write_run_summary_artifacts(repo_root: Path, run_id: str) -> Path | None:
    """Write ``summary.txt`` next to ``summary.json``. Returns path or None if missing."""
    root = repo_root.resolve()
    run_dir = root / "runs" / "loop" / run_id
    if not (run_dir / "summary.json").is_file():
        return None
    text = build_human_run_summary(root, run_id)
    out = run_dir / "summary.txt"
    out.write_text(text, encoding="utf-8")
    return out
