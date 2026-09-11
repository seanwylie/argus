"""Run first-pass Argus commands and summarize artifacts."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_EXPECTED_ARGUS_COMMANDS = 6


@dataclass
class FirstPassMetrics:
    signals_record_count: int = 0
    manifest_declaration_rows: int = 0
    non_manifest_rows: int = 0
    findings_count: int = 0
    decisions_candidates: int = 0
    ideas_count: int = 0
    orchestration_stdout: str = ""
    command_log: list[tuple[str, int]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    #: Set when evaluation aborts before finishing the command loop (unexpected exception).
    evaluation_error: str | None = None


def classify_first_pass_status(
    *,
    skipped: bool,
    metrics: FirstPassMetrics | None,
) -> str:
    """
    Honest coarse status for ``import_state.first_pass_status``.

    * ``skipped`` — user passed ``--skip-first-pass``.
    * ``success`` — all configured Argus commands exited 0.
    * ``partial`` — every command was invoked, but at least one exited non-zero (see ``errors``).
    * ``failed`` — evaluation did not complete the full command sequence, or aborted with an exception.
    """
    if skipped:
        return "skipped"
    if metrics is None:
        return "failed"
    if metrics.evaluation_error:
        return "failed"
    if len(metrics.command_log) < _EXPECTED_ARGUS_COMMANDS:
        return "failed"
    if all(code == 0 for _, code in metrics.command_log):
        return "success"
    return "partial"


def _run(
    repo_root: Path,
    argv: list[str],
    *,
    use_uv: bool,
) -> tuple[int, str, str]:
    if use_uv:
        cmd = ["uv", "run", "argus", *argv]
    else:
        cmd = ["argus", *argv]
    proc = subprocess.run(
        cmd,
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        env=None,
    )
    return proc.returncode, proc.stdout, proc.stderr


def _count_manifest_rows(records: list[dict[str, Any]]) -> int:
    n = 0
    for r in records:
        tags = {str(t).lower() for t in (r.get("tags") or [])}
        if "manifest_declaration" in tags or r.get("source") == "manifest_declaration":
            n += 1
    return n


def load_signal_metrics(repo_root: Path, product_id: str) -> tuple[int, int]:
    path = repo_root / "runs" / "signals" / "latest" / f"{product_id}.json"
    if not path.is_file():
        return 0, 0
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 0, 0
    records = data.get("records") or []
    total = int(data.get("record_count") or len(records))
    return total, _count_manifest_rows(records)


def load_findings_count(repo_root: Path, product_id: str) -> int:
    path = repo_root / "runs" / "findings" / "latest" / f"{product_id}.json"
    if not path.is_file():
        return 0
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 0
    return int(data.get("finding_count") or len(data.get("findings") or []))


def load_decisions_count(repo_root: Path, product_id: str) -> int:
    path = repo_root / "runs" / "decisions" / "latest" / f"{product_id}.json"
    if not path.is_file():
        return 0
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 0
    cands = data.get("candidates") or []
    return len(cands) if isinstance(cands, list) else 0


def load_ideas_count_for_product(repo_root: Path, product_id: str) -> int:
    path = repo_root / "runs" / "ideas" / "latest.json"
    if not path.is_file():
        return 0
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 0
    ideas = data.get("ideas") or []
    if not isinstance(ideas, list):
        return int(data.get("idea_count") or 0)
    matched = [i for i in ideas if isinstance(i, dict) and i.get("product_id") == product_id]
    return len(matched)


def _utc_mtime_iso(path: Path) -> str | None:
    try:
        ts = path.stat().st_mtime
    except OSError:
        return None
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_first_pass_run_anchors(repo_root: Path, product_id: str) -> dict[str, Any]:
    """
    Bounded, deterministic facts from ``runs/*/latest*`` JSON for first-pass summaries.

    Omits keys when files are missing or unreadable — callers should not invent defaults.
    """
    root = repo_root.resolve()
    out: dict[str, Any] = {}
    sig = root / "runs" / "signals" / "latest" / f"{product_id}.json"
    if sig.is_file():
        try:
            raw = json.loads(sig.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            raw = {}
        out["signals_latest"] = {
            "path": str(sig.relative_to(root)),
            "file_mtime_utc": _utc_mtime_iso(sig),
            "collected_at_utc": raw.get("collected_at_utc"),
            "record_count": raw.get("record_count"),
        }
    ideas = root / "runs" / "ideas" / "latest.json"
    if ideas.is_file():
        try:
            raw = json.loads(ideas.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            raw = {}
        meta = raw.get("meta") or {}
        sel = meta.get("selection_summary") or {}
        out["ideas_latest"] = {
            "path": str(ideas.relative_to(root)),
            "file_mtime_utc": _utc_mtime_iso(ideas),
            "meta_timestamp_slug": meta.get("timestamp_slug"),
            "stopped_early_for_quality": sel.get("stopped_early_for_quality"),
            "rejected_below_quality_threshold": sel.get("rejected_below_quality_threshold"),
            "selection_summary_present": bool(sel),
        }
    orch = root / "runs" / "orchestration" / "latest" / f"{product_id}.json"
    if orch.is_file():
        try:
            raw = json.loads(orch.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            raw = {}
        ef = raw.get("eligibility_facts") or {}
        out["orchestration_latest"] = {
            "path": str(orch.relative_to(root)),
            "file_mtime_utc": _utc_mtime_iso(orch),
            "orchestration_status": raw.get("orchestration_status"),
            "temporal_worst_freshness_status": ef.get("temporal_worst_freshness_status"),
            "signals_refresh_needed": ef.get("signals_refresh_needed"),
        }
    return out


def _import_confidence_bullets(
    *,
    metrics: FirstPassMetrics,
    import_instrumentation: dict[str, Any],
    anchors: dict[str, Any],
) -> list[str]:
    """Short, checkable notes — no speculation."""
    lines: list[str] = []
    fp = import_instrumentation.get("first_pass")
    if isinstance(fp, dict) and fp.get("first_pass_status"):
        lines.append(f"First-pass CLI status: `{fp.get('first_pass_status')}` (from importer).")
    rs = import_instrumentation.get("repo_snapshot")
    if isinstance(rs, dict):
        if rs.get("test_path"):
            lines.append(f"Tests anchor (import scan): `{rs['test_path']}`.")
        if rs.get("doc_path"):
            lines.append(f"Docs anchor (import scan): `{rs['doc_path']}`.")
        lines.append(
            "SECURITY.md present (import scan): "
            f"{'yes' if rs.get('has_security_md') else 'no'}."
        )
    if metrics.signals_record_count > 0:
        frac = metrics.manifest_declaration_rows / metrics.signals_record_count
        if frac >= 0.35:
            lines.append(
                f"Manifest reconciliation: ~{frac:.0%} of signal rows tagged/named as "
                "`manifest_declaration` — treat `record_count` as partly declarative, not only repo facts."
            )
    if anchors.get("ideas_latest") and isinstance(anchors["ideas_latest"], dict):
        slug = anchors["ideas_latest"].get("meta_timestamp_slug")
        if slug:
            lines.append(
                f"Ideas bundle `meta.timestamp_slug`: `{slug}` — use with idea counts when comparing runs."
            )
        if anchors["ideas_latest"].get("stopped_early_for_quality") is True:
            lines.append("Ideas: mechanical pass reports `stopped_early_for_quality` — list may be shorter than raw synthesis.")
        rj = anchors["ideas_latest"].get("rejected_below_quality_threshold")
        if isinstance(rj, int) and rj > 0:
            lines.append(f"Ideas: `rejected_below_quality_threshold` = {rj} (from bundle meta).")
    lines.append(
        "Ideas counts here filter `runs/ideas/latest.json` by this `product_id`; the file is global — "
        "do not compare products without checking `product_id` on each row."
    )
    return lines


def _run_anchors_markdown(anchors: dict[str, Any]) -> list[str]:
    rows: list[str] = []
    if not anchors:
        return [
            "*No `runs/*/latest*.json` files matched this product — commands may have failed before writes.*",
        ]
    sl = anchors.get("signals_latest")
    if isinstance(sl, dict):
        rows.append(
            f"| Signals | `{sl.get('path', '')}` | {sl.get('file_mtime_utc') or '—'} | "
            f"collected_at_utc={sl.get('collected_at_utc') or '—'}; record_count={sl.get('record_count')!s} |"
        )
    il = anchors.get("ideas_latest")
    if isinstance(il, dict):
        slug = il.get("meta_timestamp_slug") or "—"
        se = il.get("stopped_early_for_quality")
        rj = il.get("rejected_below_quality_threshold")
        rows.append(
            f"| Ideas (global bundle) | `{il.get('path', '')}` | {il.get('file_mtime_utc') or '—'} | "
            f"meta.timestamp_slug={slug}; stopped_early_for_quality={se!s}; "
            f"rejected_below_quality_threshold={rj!s} |"
        )
    ol = anchors.get("orchestration_latest")
    if isinstance(ol, dict):
        rows.append(
            f"| Orchestration | `{ol.get('path', '')}` | {ol.get('file_mtime_utc') or '—'} | "
            f"status={ol.get('orchestration_status')!s}; "
            f"temporal_worst_freshness_status={ol.get('temporal_worst_freshness_status')!s}; "
            f"signals_refresh_needed={ol.get('signals_refresh_needed')!s} |"
        )
    if not rows:
        return [
            "*Anchors dict present but no readable bundle rows — check JSON under `runs/`.*",
        ]
    out = [
        "Machine-readable paths and mtimes for **this** evaluation (compare file mtimes across re-runs, not prose alone).",
        "",
        "| Bundle | Path | File mtime (UTC) | Fields (from JSON) |",
        "|--------|------|------------------|----------------------|",
    ]
    out.extend(rows)
    out.append("")
    out.append(
        "**Historical vs current:** Table values are whatever was on disk when this summary was written. "
        "When comparing to another machine or date, re-check the same paths."
    )
    return out


def _argv_sequence(product_id: str) -> list[list[str]]:
    return [
        ["products", "show", product_id],
        ["signals", "collect", product_id],
        ["findings", "generate", product_id],
        ["decisions", "generate", product_id],
        ["ideas", "generate", product_id],
        ["orchestration", "state", "--product-id", product_id],
    ]


def run_first_pass_evaluation(
    repo_root: Path,
    product_id: str,
    *,
    use_uv: bool,
) -> FirstPassMetrics:
    m = FirstPassMetrics()
    try:
        for argv in _argv_sequence(product_id):
            code, out, err = _run(repo_root, argv, use_uv=use_uv)
            cmd_s = " ".join(argv)
            m.command_log.append((cmd_s, code))
            if code != 0:
                m.errors.append(f"command failed ({code}): argus {' '.join(argv)}\n{err or out}")
            if argv[0] == "orchestration":
                m.orchestration_stdout = (out or "") + (err or "")

        m.signals_record_count, m.manifest_declaration_rows = load_signal_metrics(repo_root, product_id)
        m.non_manifest_rows = max(0, m.signals_record_count - m.manifest_declaration_rows)
        m.findings_count = load_findings_count(repo_root, product_id)
        m.decisions_candidates = load_decisions_count(repo_root, product_id)
        m.ideas_count = load_ideas_count_for_product(repo_root, product_id)
    except OSError as e:
        m.evaluation_error = f"os_error: {e}"
    except Exception as e:  # noqa: BLE001 — surface as failed evaluation, not crash importer
        m.evaluation_error = f"{type(e).__name__}: {e}"
    return m


def write_first_pass_summary(
    product_root: Path,
    repo_root: Path,
    product_id: str,
    metrics: FirstPassMetrics,
    *,
    import_instrumentation: dict[str, Any],
    use_uv: bool,
) -> None:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    frac = (
        metrics.manifest_declaration_rows / metrics.signals_record_count
        if metrics.signals_record_count
        else 0.0
    )
    anchors = load_first_pass_run_anchors(repo_root, product_id)
    ptype = str(import_instrumentation.get("product_type") or "").strip()
    top_level = import_instrumentation.get("top_level")
    tl_lines: list[str] = []
    if isinstance(top_level, list) and top_level:
        shown = [str(x) for x in top_level[:24]]
        more = len(top_level) - len(shown)
        tl_lines.append("Top-level names observed in the imported repo copy (import scan):")
        tl_lines.append("")
        tl_lines.append(", ".join(f"`{x}`" for x in shown) + (f", … (+{more} more)" if more > 0 else ""))
        tl_lines.append("")
    conf_bullets = _import_confidence_bullets(
        metrics=metrics, import_instrumentation=import_instrumentation, anchors=anchors
    )
    lines = [
        f"# {product_id} — First Argus evaluation summary (importer)",
        "",
        f"Evaluation time (UTC): {now}",
        f"Argus repo root: `{repo_root}`",
        f"Product tree: `{product_root.relative_to(repo_root)}`",
        "",
    ]
    if ptype:
        lines.extend([f"Product type (from import): `{ptype}`", ""])
    ps = import_instrumentation.get("product_shape")
    if isinstance(ps, dict):
        lines.extend(
            [
                "## A.0 Product shape (heuristic)",
                "",
                f"**Label:** `{ps.get('label', '?')}`",
                "",
                str(ps.get("hedged_summary", "")),
                "",
                "Evidence (filesystem only):",
                "",
            ]
        )
        for ev in ps.get("evidence") or []:
            lines.append(f"- {ev}")
        lines.extend(
            [
                "",
                str(ps.get("disclaimer", "")),
                "",
            ]
        )
    lines.extend(
        [
            "## A. Commands run",
            "",
            "Executed from the Argus repo root:",
            "",
            "| Step | Command | Exit |",
            "|------|---------|------|",
        ]
    )
    # Map log to table rows
    prefix = "uv run argus" if use_uv else "argus"
    step = 1
    for cmd, code in metrics.command_log:
        lines.append(f"| {step} | `{prefix} {cmd}` | {code} |")
        step += 1
    lines.extend(
        [
            "",
            f"**Note:** Commands use `{prefix}` (see importer `--no-uv`).",
            "",
            "## B. Artifact metrics (from `runs/` after import)",
            "",
            "| Metric | Value |",
            "|--------|-------|",
            f"| Signals `record_count` | {metrics.signals_record_count} |",
            f"| Rows tagged `manifest_declaration` (approx.) | {metrics.manifest_declaration_rows} |",
            f"| Non-manifest rows (approx.) | {metrics.non_manifest_rows} |",
            f"| Approx. manifest fraction | {frac:.3f} |",
            f"| Findings | {metrics.findings_count} |",
            f"| Decision candidates | {metrics.decisions_candidates} |",
            f"| Ideas (latest bundle, filtered by `product_id`) | {metrics.ideas_count} |",
            "",
            "### B.1 Deterministic run anchors",
            "",
        ]
    )
    lines.extend(_run_anchors_markdown(anchors))
    lines.append("")
    if tl_lines:
        lines.extend(["### B.2 Key path inventory (import-time)", ""] + tl_lines)
    lines.extend(
        [
            "### B.3 Import confidence (checklist, grounded)",
            "",
        ]
    )
    for b in conf_bullets:
        lines.append(f"- {b}")
    lines.extend(
        [
            "",
            "### Instrumentation from importer",
            "",
            "```json",
            json.dumps(import_instrumentation, indent=2, sort_keys=True),
            "```",
            "",
        ]
    )
    if metrics.evaluation_error:
        lines.extend(
            [
                "## C. Evaluation abort",
                "",
                f"The importer could not finish all configured commands: `{metrics.evaluation_error}`",
                "",
            ]
        )
    lines.extend(
        [
            "## D. Orchestration CLI snapshot",
            "",
            "```text",
            (metrics.orchestration_stdout.strip() or "(empty)"),
            "```",
            "",
            "## E. Import vs output quality (checklist)",
            "",
            "- **Config sufficiency:** Did `product.yaml` + `signals.yaml` validate and give Argus enough to run? Check `import_notes.md` assumptions.",
            "- **Grounded vs noisy:** Compare filesystem/metrics rows vs `manifest_declaration` rows — high manifest fraction often means declarative gap placeholders, not repo facts.",
            "- **Findings wording:** Reliability findings that mention sparse *pipeline* coverage are about signal economics, not a verdict on product quality unless evidence says so.",
            "- **Repo context:** If README/docs were thin, findings/ideas may be generic — improve in-repo docs before blaming Argus.",
            "- **Importer weaknesses:** Re-run from the same `--repo-url` after tuning excludes or manifest anchors.",
            "",
            "## F. Command errors (if any)",
            "",
        ]
    )
    if metrics.errors:
        for e in metrics.errors:
            lines.append(f"- {e}")
    else:
        lines.append("- None recorded.")
    lines.extend(
        [
            "",
            "## G. Next steps for the importer (developer)",
            "",
            "1. Fill section I in `import_notes.md` (what worked / what failed).",
            "2. Adjust `argus/importer/` heuristics or CLI flags.",
            "3. Re-import the same GitHub URL and diff this file against the previous run.",
            "",
        ]
    )
    out_path = product_root / "first_pass_argus_summary.md"
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
