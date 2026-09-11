"""
Load ``config/importer_regression.yaml``, run importer per repo, write measurement report.

Does not change importer behavior — orchestration and analysis only.
"""

from __future__ import annotations

import io
import json
import shlex
import sys
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from argus.importer.cli import import_main
from argus.importer.discover import scan_repo
from argus.importer.import_state import extract_import_state
from argus.products.loader import load_yaml_file


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def load_regression_config(path: Path) -> dict[str, Any]:
    raw = path.read_text(encoding="utf-8")
    data = yaml.safe_load(raw)
    if not isinstance(data, dict):
        raise ValueError(f"{path}: top level must be a mapping")
    return data


def merge_entry(defaults: dict[str, Any], repo: dict[str, Any]) -> dict[str, Any]:
    out = dict(defaults)
    flags = repo.get("flags")
    if isinstance(flags, dict):
        out.update(flags)
    for k, v in repo.items():
        if k in ("flags", "expect"):
            continue
        out[k] = v
    if "expect" in repo:
        out["expect"] = repo["expect"]
    return out


def build_argv(merged: dict[str, Any]) -> list[str]:
    """Construct ``import_main`` argv from merged defaults + repo entry."""
    pid = merged.get("product_id")
    if not pid:
        raise ValueError("product_id required")
    url = merged.get("github_url")
    if not url:
        raise ValueError("github_url required")

    argv: list[str] = [
        "--repo-url",
        str(url).strip(),
        "--product-id",
        str(pid).strip(),
        "--product-type",
        str(merged.get("product_type", "application")),
        "--operator-team",
        str(merged.get("operator_team", "argus")),
        "--operator-name",
        str(merged.get("operator_name", "")),
    ]
    if merged.get("include_cursor"):
        argv.append("--include-cursor")
    if merged.get("include_local_db_artifacts"):
        argv.append("--include-local-db-artifacts")
    # defaults exclude_node_artifacts true => CLI default; false => --no-exclude-node-artifacts
    if merged.get("exclude_node_artifacts", True) is False:
        argv.append("--no-exclude-node-artifacts")
    for ex in merged.get("extra_excludes") or []:
        argv.extend(["--extra-exclude", str(ex)])
    if merged.get("skip_first_pass"):
        argv.append("--skip-first-pass")
    if merged.get("no_uv"):
        argv.append("--no-uv")
    return argv


@dataclass
class RepoRunResult:
    repo_key: str
    product_id: str
    github_url: str
    notes: str
    argv: list[str]
    import_exit_code: int
    import_stderr: str = ""
    first_pass_status: str | None = None
    import_state: dict[str, Any] | None = None
    classification: str = "UNKNOWN"
    key_files_check: str = ""
    key_path_inventory: str = ""
    exclusion_notes: str = ""
    discovery_notes: str = ""
    summary_insight_notes: str = ""
    issues: list[str] = field(default_factory=list)
    error_message: str | None = None


def _read_text(path: Path, limit: int = 80_000) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")[:limit]
    except OSError:
        return ""


def _analyze_summary_noise(summary_text: str) -> str:
    """Short heuristic description — measurement only, not a product judgment."""
    if not summary_text.strip():
        return "No summary content."
    lower = summary_text.lower()
    noise_markers = ("manifest_declaration", "1970", "placeholder", "epoch")
    insight_markers = ("record_count", "finding", "decision", "orchestration", "metrics")
    n_noise = sum(1 for m in noise_markers if m in lower)
    n_insight = sum(1 for m in insight_markers if m in lower)
    if n_insight >= 3 and n_noise <= 2:
        return "Summary mixes pipeline metrics and checklist language; appears to contain actionable pipeline context (heuristic)."
    if n_noise >= 3:
        return "Summary mentions manifest/placeholder patterns heavily — may read as noisy vs repo-specific insights (heuristic)."
    return "Summary present; classify manually for insight vs boilerplate."


def _key_path_inventory(product_root: Path) -> str:
    """Grounded presence/absence of common roots (measurement, not requirements)."""
    names = [
        "README.md",
        "pyproject.toml",
        "package.json",
        "setup.py",
        "src",
        "app",
        "docs",
        "tests",
    ]
    parts: list[str] = []
    for name in names:
        ok = (product_root / name).exists()
        parts.append(f"{name}:{'yes' if ok else 'no'}")
    return "; ".join(parts)


def _check_expected_paths(product_root: Path, expect: Any) -> tuple[bool, str]:
    if not isinstance(expect, dict):
        return True, "no `expect` block — skipped path assertions"
    paths_all = expect.get("paths_all")
    if not paths_all:
        return True, "expect.paths_all not set — skipped"
    missing = [p for p in paths_all if not (product_root / p).exists()]
    if missing:
        return False, f"missing expected paths: {missing}"
    return True, f"all expect.paths_all present ({len(paths_all)} paths)"


def _exclusion_risk_notes(merged: dict[str, Any], product_root: Path) -> str:
    parts: list[str] = []
    if not merged.get("include_local_db_artifacts", False):
        dbs = list(product_root.glob("data/**/*.db")) + list(product_root.glob("**/*.sqlite*"))
        if not dbs and (product_root / "data").is_dir():
            parts.append(
                "include_local_db_artifacts=false: no *.db under product tree — "
                "if upstream uses SQLite in data/, it may have been excluded by sync rules."
            )
    if merged.get("exclude_node_artifacts", True) and (product_root / "package.json").is_file():
        parts.append(
            "node_modules excluded by default — OK unless you need vendored JS under products/ for signals."
        )
    return " ".join(parts) if parts else "No obvious exclusion red flags from heuristics."


def _discovery_notes(product_root: Path, product_id: str, github_url: str) -> str:
    try:
        facts = scan_repo(product_root, product_id=product_id, github_url=github_url)
    except Exception as e:  # noqa: BLE001
        return f"scan_repo failed: {e}"
    n_manifest = 0
    sig_path = product_root / "signals.yaml"
    raw, _ = load_yaml_file(sig_path)
    if isinstance(raw, dict) and isinstance(raw.get("signals"), list):
        n_manifest = len(raw["signals"])
    return (
        f"signals.yaml entries: {n_manifest}; "
        f"test_path={facts.test_path!r}; doc_path={facts.doc_path!r}; "
        f"js_static_paths={len(facts.grounded_js_static_paths)}"
    )


def classify(
    *,
    import_exit: int,
    import_state: dict[str, Any] | None,
    first_pass_status: str | None,
    expect_ok: bool,
    summary_len: int,
) -> str:
    """GOOD / PARTIAL / BROKEN — falsifiable coarse labels for the report."""
    if import_exit != 0 or import_state is None:
        return "BROKEN"
    if first_pass_status is None:
        return "BROKEN"
    if first_pass_status == "failed":
        return "BROKEN"
    if first_pass_status == "pending":
        return "PARTIAL"
    if first_pass_status == "skipped":
        return "PARTIAL"
    if first_pass_status == "partial":
        return "PARTIAL"
    if not expect_ok:
        return "PARTIAL"
    if first_pass_status == "success" and summary_len > 200:
        return "GOOD"
    return "PARTIAL"


def run_one(
    merged: dict[str, Any],
    *,
    repo_key: str,
) -> RepoRunResult:
    repo_root = _repo_root()
    product_id = str(merged["product_id"])
    product_root = repo_root / "products" / product_id
    url = str(merged.get("github_url", ""))
    notes = str(merged.get("notes", ""))

    argv = build_argv(merged)
    stderr_buf = io.StringIO()
    stdout_buf = io.StringIO()
    try:
        with redirect_stdout(stdout_buf), redirect_stderr(stderr_buf):
            code = import_main(argv)
    except Exception as e:  # noqa: BLE001
        return RepoRunResult(
            repo_key=repo_key,
            product_id=product_id,
            github_url=url,
            notes=notes,
            argv=argv,
            import_exit_code=1,
            import_stderr=stderr_buf.getvalue() + f"\nexception: {e}",
            classification="BROKEN",
            issues=[f"exception during import_main: {e}"],
            key_path_inventory="",
            error_message=str(e),
        )

    err_s = stderr_buf.getvalue()
    raw_py, _ = load_yaml_file(product_root / "product.yaml")
    ist = extract_import_state(raw_py or {}) if raw_py else None
    fps = ist.get("first_pass_status") if ist else None

    expect_ok, expect_msg = _check_expected_paths(product_root, merged.get("expect"))
    inventory = _key_path_inventory(product_root)
    summary_text = _read_text(product_root / "first_pass_argus_summary.md")
    summary_len = len(summary_text)

    issues: list[str] = []
    if code != 0:
        issues.append(f"import_main exit code {code}")
    if ist is None:
        issues.append("raw_extensions.import_state missing after run")
    if fps not in ("success", "partial", "skipped") and fps != "failed":
        if fps == "pending":
            issues.append("import_state still pending (unexpected after completed run)")
    if not expect_ok:
        issues.append(expect_msg)
    if fps == "partial":
        issues.append("first_pass_status=partial (some Argus commands failed)")
    if fps == "failed":
        issues.append("first_pass_status=failed")
    ev = ist.get("evaluation_error") if ist else None
    if ev:
        issues.append(f"evaluation_error: {ev}")
    ferr = ist.get("first_pass_command_errors") if ist else None
    if isinstance(ferr, list) and ferr:
        issues.append(f"first_pass_command_errors: {len(ferr)} recorded")

    cls = classify(
        import_exit=code,
        import_state=ist,
        first_pass_status=fps if isinstance(fps, str) else None,
        expect_ok=expect_ok,
        summary_len=summary_len,
    )
    if cls == "GOOD" and summary_len < 400:
        cls = "PARTIAL"
        issues.append("first_pass summary shorter than heuristic threshold — thin documentation")

    top3 = issues[:3]

    return RepoRunResult(
        repo_key=repo_key,
        product_id=product_id,
        github_url=url,
        notes=notes,
        argv=argv,
        import_exit_code=code,
        import_stderr=err_s,
        first_pass_status=fps if isinstance(fps, str) else None,
        import_state=ist,
        classification=cls,
        key_files_check=expect_msg,
        key_path_inventory=inventory,
        exclusion_notes=_exclusion_risk_notes(merged, product_root),
        discovery_notes=_discovery_notes(product_root, product_id, url),
        summary_insight_notes=_analyze_summary_noise(summary_text),
        issues=top3,
        error_message=None,
    )


def run_all(config_path: Path) -> list[RepoRunResult]:
    cfg = load_regression_config(config_path)
    defaults = cfg.get("defaults") or {}
    if not isinstance(defaults, dict):
        defaults = {}
    repos = cfg.get("repos") or []
    if not isinstance(repos, list):
        raise ValueError("repos must be a list")
    results: list[RepoRunResult] = []
    for entry in repos:
        if not isinstance(entry, dict):
            continue
        key = str(entry.get("id") or entry.get("product_id") or "unknown")
        merged = merge_entry(defaults, entry)
        results.append(run_one(merged, repo_key=key))
    return results


def write_report(
    results: list[RepoRunResult],
    out_path: Path,
    *,
    config_path: Path,
) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    lines = [
        "# Importer regression report",
        "",
        f"- **Generated (UTC):** {now}",
        f"- **Config:** `{config_path}`",
        "- **Scope:** measurement only — importer code unchanged.",
        "- **Warning:** each entry runs a **full import** and re-syncs `products/<product_id>/` from the clone cache.",
        "",
        "## Summary",
        "",
        "| Repo key | Product ID | Classification | Import exit | `first_pass_status` |",
        "|----------|------------|----------------|-------------|---------------------|",
    ]
    for r in results:
        fps = r.first_pass_status or "—"
        lines.append(
            f"| {r.repo_key} | {r.product_id} | {r.classification} | {r.import_exit_code} | {fps} |"
        )
    lines.extend(["", "## Patterns across repos", ""])

    broken = [r for r in results if r.classification == "BROKEN"]
    partial = [r for r in results if r.classification == "PARTIAL"]
    good = [r for r in results if r.classification == "GOOD"]
    lines.append(f"- **GOOD:** {len(good)} — {', '.join(r.repo_key for r in good) or 'none'}")
    lines.append(f"- **PARTIAL:** {len(partial)} — {', '.join(r.repo_key for r in partial) or 'none'}")
    lines.append(f"- **BROKEN:** {len(broken)} — {', '.join(r.repo_key for r in broken) or 'none'}")

    exit_fail = [r for r in results if r.import_exit_code != 0]
    if exit_fail:
        lines.append(f"- **Non-zero import exit:** {', '.join(r.repo_key for r in exit_fail)}")
    fps_partial = [r for r in results if r.first_pass_status == "partial"]
    if fps_partial:
        lines.append(f"- **first_pass partial (command failures):** {', '.join(r.repo_key for r in fps_partial)}")
    missing_ist = [r for r in results if r.import_state is None and r.import_exit_code == 0]
    if missing_ist:
        lines.append(f"- **Missing import_state despite exit 0:** {', '.join(r.repo_key for r in missing_ist)}")

    lines.extend(["", "---", ""])

    for r in results:
        cmd = "uv run python tools/import_product.py " + " ".join(shlex.quote(a) for a in r.argv)
        lines.extend(
            [
                f"## {r.repo_key} (`{r.product_id}`)",
                "",
                f"- **Classification:** {r.classification}",
                f"- **GitHub URL:** `{r.github_url}`",
                f"- **Config notes:** {r.notes or '—'}",
                f"- **Import exit code:** {r.import_exit_code}",
                f"- **first_pass_status:** {r.first_pass_status or '—'}",
                "",
                "### Command (equivalent CLI)",
                "",
                "```text",
                cmd,
                "```",
                "",
            ]
        )

        if r.import_stderr.strip():
            lines.extend(["### Importer stderr (truncated)", "", "```text", r.import_stderr[:8000], "```", ""])

        lines.extend(
            [
                "### `import_state` (subset)",
                "",
                "```json",
            ]
        )
        if r.import_state:
            ist = r.import_state
            subset = {
                k: ist.get(k)
                for k in (
                    "schema",
                    "import_mode",
                    "source_repo_url",
                    "cache_slug",
                    "imported_from_commit",
                    "imported_from_branch",
                    "imported_at_utc",
                    "first_pass_ran",
                    "first_pass_status",
                    "first_pass_summary_path",
                    "evaluation_error",
                )
                if k in ist
            }
            se = ist.get("sync_excludes")
            if isinstance(se, list):
                subset["sync_excludes_count"] = len(se)
            lines.append(json.dumps(subset, indent=2))
        else:
            lines.append("(missing)")
        lines.extend(["```", ""])

        lines.extend(
            [
                "### Validation checks",
                "",
                f"- **Expected key files (config `expect.paths_all`):** {r.key_files_check}",
                f"- **Key path inventory (heuristic):** {r.key_path_inventory}",
                f"- **Exclusion / risk notes:** {r.exclusion_notes}",
                f"- **Discovery / signals:** {r.discovery_notes}",
                f"- **first_pass_argus_summary.md (insight vs noise, heuristic):** {r.summary_insight_notes}",
                "",
                "### Top 3 issues",
                "",
            ]
        )
        if r.issues:
            for i, issue in enumerate(r.issues[:3], 1):
                lines.append(f"{i}. {issue}")
        else:
            lines.append("— none flagged by heuristics —")
        lines.append("")

    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    import argparse

    p = argparse.ArgumentParser(description="Run importer regression from config/importer_regression.yaml")
    p.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Path to regression YAML (default: <repo>/config/importer_regression.yaml)",
    )
    p.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Directory for report (default: <repo>/runs/importer_regression)",
    )
    args = p.parse_args(argv)

    root = _repo_root()
    config_path = args.config if args.config else root / "config" / "importer_regression.yaml"
    if not config_path.is_file():
        print(f"error: config not found: {config_path}", file=sys.stderr)
        return 2

    out_dir = args.output_dir if args.output_dir else root / "runs" / "importer_regression"
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = out_dir / f"{ts}.md"

    print(f"Loading {config_path}", file=sys.stderr)
    print(
        "NOTE: Each repo entry runs full import_main — product trees under products/ may be overwritten.",
        file=sys.stderr,
    )
    results = run_all(config_path)
    write_report(results, out_path, config_path=config_path)
    print(f"Wrote {out_path}", file=sys.stderr)
    return 0 if all(r.import_exit_code == 0 for r in results) else 1
