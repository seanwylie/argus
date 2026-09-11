"""CLI for GitHub → product tree import."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from argus.importer.cache import ensure_git_repo_cache
from argus.importer.classification import classify_product_shape
from argus.importer.constants import build_exclude_list
from argus.importer.discover import RepoFacts, display_name_from_facts, scan_repo
from argus.importer.evaluate import (
    FirstPassMetrics,
    classify_first_pass_status,
    run_first_pass_evaluation,
    write_first_pass_summary,
)
from argus.importer.git_info import read_git_branch, read_git_commit
from argus.importer.import_state import (
    build_import_state,
    extract_import_state,
    patch_product_yaml_import_state,
    repo_relative_summary_path,
    utc_now_iso,
)
from argus.importer.replay import inspect_import_drift, run_import_replay
from argus.importer.scaffold import (
    build_product_yaml,
    write_import_notes,
    write_minimal_doctrine,
    write_placeholder_scripts,
    write_product_yaml_file,
    write_signals_yaml,
)
from argus.importer.status_cmd import run_status
from argus.importer.sync import sync_tree
from argus.importer.url import parse_github_repo
from argus.products.git_lifecycle import write_non_git_import_marker
from argus.products.loader import load_yaml_file
from argus.products.validate import validate_manifest

STATIC_SITE_EXTENSION_NOTES = """\
For HTML/CSS/JS-heavy repositories (no Python packaging):

- **Shape label:** see `raw_extensions.argus_onboarding.product_shape` and **C.0** in `import_notes.md` (heuristic: `js_frontend`, `static_site`, etc.). Details: `docs/importer-discovery-classification.md`.
- **Detection:** absence of `pyproject.toml` / `setup.py` with presence of `package.json`, `index.html`, or static `public/` / `dist/`.
- **Likely gaps:** filesystem manifest anchors use observed config filenames only — not framework or runtime claims.
- **Doctrine / signals:** keep `signals.yaml` minimal; prefer explicit paths to files that exist.
- **Extension points:** `argus/importer/discover.py` (`scan_repo`), `argus/importer/classification.py` (`classify_product_shape`), `scaffold.py` (`build_signals_yaml_entries`).
"""


def _repo_root_from_argv() -> Path:
    here = Path(__file__).resolve()
    return here.parents[2]


def _build_import_report(
    facts: RepoFacts,
    excludes: list[str],
    assumptions: list[str],
    uncertainties: list[str],
    weak_areas: list[str],
    domain_hints: list[str],
    validation_errors: list[str],
    validation_warnings: list[str],
    first_pass: FirstPassMetrics | None,
    *,
    product_shape: dict[str, object] | None = None,
    product_type: str = "",
) -> dict[str, object]:
    out: dict[str, object] = {
        "product_id": facts.product_id,
        "github_url": facts.github_url,
        "product_type": product_type,
        "repo_snapshot": {
            "test_path": facts.test_path,
            "doc_path": facts.doc_path,
            "has_security_md": facts.has_security_md,
            "notable_layout_dirs": facts.notable_layout_dirs[:12],
        },
        "excludes": excludes,
        "assumptions": assumptions,
        "uncertainties": uncertainties,
        "weak_areas": weak_areas,
        "domain_signal_hints": domain_hints,
        "top_level": facts.top_level[:50],
        "validation_errors": validation_errors,
        "validation_warnings": validation_warnings,
    }
    if product_shape is not None:
        out["product_shape"] = product_shape
    if first_pass is not None:
        out["first_pass"] = {
            "signals_record_count": first_pass.signals_record_count,
            "manifest_declaration_rows": first_pass.manifest_declaration_rows,
            "non_manifest_rows": first_pass.non_manifest_rows,
            "findings_count": first_pass.findings_count,
            "decisions_candidates": first_pass.decisions_candidates,
            "ideas_count": first_pass.ideas_count,
            "command_errors": first_pass.errors,
            "evaluation_error": first_pass.evaluation_error,
            "first_pass_status": classify_first_pass_status(
                skipped=False,
                metrics=first_pass,
            ),
        }
    return out


def _status_subcommand(argv: list[str]) -> int:
    p = argparse.ArgumentParser(prog="import_product.py status")
    p.add_argument("--product-id", required=True)
    p.add_argument("--json", action="store_true", help="Emit JSON instead of text.")
    sa = p.parse_args(argv)
    return run_status(_repo_root_from_argv(), sa.product_id, json_out=sa.json)


def _replay_subcommand(argv: list[str]) -> int:
    p = argparse.ArgumentParser(
        prog="import_product.py replay",
        description="Re-sync products/<id>/ from cached GitHub clone using raw_extensions.import_state.",
    )
    p.add_argument("--product-id", required=True)
    p.add_argument(
        "--cache-dir",
        default=None,
        help="Clone cache root (default: <argus_repo>/.import_cache)",
    )
    p.add_argument(
        "--no-delete",
        action="store_true",
        help="Do not delete files in product tree missing from source (rsync --delete off)",
    )
    p.add_argument(
        "--skip-first-pass",
        action="store_true",
        help="Sync only; do not run argus first-pass evaluation",
    )
    p.add_argument("--no-uv", action="store_true", help="Invoke `argus` on PATH instead of `uv run argus`")
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Print reconstructed argv and exit without syncing",
    )
    p.add_argument("--json", action="store_true", help="Emit machine-readable JSON (stderr on errors)")
    sa = p.parse_args(argv)
    root = _repo_root_from_argv()
    cache_path = Path(sa.cache_dir) if sa.cache_dir else None
    code, report = run_import_replay(
        root,
        sa.product_id,
        cache_dir=cache_path,
        no_delete=sa.no_delete,
        skip_first_pass=sa.skip_first_pass,
        no_uv=sa.no_uv,
        dry_run=sa.dry_run,
    )
    if sa.json:
        print(json.dumps({"exit_code": code, **report}, indent=2, default=str))
    else:
        if code != 0:
            err = report.get("error", "replay failed")
            print(f"error: {err}", file=sys.stderr)
            if report.get("import_state_errors"):
                for e in report["import_state_errors"]:
                    print(f"  - {e}", file=sys.stderr)
        elif sa.dry_run:
            print("dry_run reconstructed argv:", " ".join(report.get("reconstructed_argv", [])))
        else:
            print(f"replay ok: product_root={report.get('product_root')}")
    return code


def _drift_subcommand(argv: list[str]) -> int:
    p = argparse.ArgumentParser(
        prog="import_product.py drift",
        description="Read-only: compare import_state to local import cache HEAD (no sync).",
    )
    p.add_argument("--product-id", required=True)
    p.add_argument(
        "--fetch",
        action="store_true",
        help="Run git fetch in cache before reading HEAD (network; optional)",
    )
    p.add_argument("--json", action="store_true", help="Emit JSON instead of text")
    sa = p.parse_args(argv)
    root = _repo_root_from_argv()
    payload = inspect_import_drift(root, sa.product_id, fetch_cache=sa.fetch)
    if sa.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0
    lines = [
        f"product_id: {payload['product_id']}",
        f"import_state_present: {payload['import_state_present']}",
        f"import_state_complete: {payload['import_state_complete']}",
    ]
    if payload.get("import_state_errors"):
        lines.append("import_state_errors:")
        for e in payload["import_state_errors"]:
            lines.append(f"  - {e}")
    lines.extend(
        [
            f"source_repo_url: {payload.get('source_repo_url')}",
            f"imported_from_commit (stored): {payload.get('imported_from_commit_stored')!r}",
            f"imported_from_branch (stored): {payload.get('imported_from_branch_stored')!r}",
            f"imported_at_utc: {payload.get('imported_at_utc')}",
            f"cache_path: {payload.get('cache_path')}",
            f"cache_exists: {payload.get('cache_exists')}",
            f"cache_fetch_ran: {payload.get('cache_fetch_ran')}",
            f"cache_head_commit: {payload.get('cache_head_commit')!r}",
            f"cache_head_matches_stored_commit: {payload.get('cache_head_matches_stored_commit')}",
            f"cache_has_stored_commit: {payload.get('cache_has_stored_commit')}",
            "",
            f"drift_summary: {payload.get('drift_summary')}",
        ]
    )
    print("\n".join(lines))
    return 0


def import_main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description="Import a GitHub repository into products/<product_id>/ and scaffold Argus onboarding.",
        prog="import_product.py",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="Show importer metadata for an existing product (no import). Use with --product-id.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="With --status: emit JSON status (same shape as status subcommand).",
    )
    parser.add_argument("--repo-url", default=None, help="HTTPS GitHub URL (required unless --status)")
    parser.add_argument("--product-id", required=True, help="Directory name under products/")
    parser.add_argument(
        "--cache-dir",
        default=None,
        help="Clone cache directory (default: <argus_repo>/.import_cache)",
    )
    parser.add_argument(
        "--no-delete",
        action="store_true",
        help="Do not delete destination files missing from source during sync (not recommended)",
    )
    parser.add_argument(
        "--include-cursor",
        action="store_true",
        help="Copy .cursor/ (default: excluded)",
    )
    parser.add_argument(
        "--include-local-db-artifacts",
        action="store_true",
        help="Do not exclude *.db / *.sqlite* (default: excluded)",
    )
    parser.add_argument(
        "--no-exclude-node-artifacts",
        action="store_true",
        help="Skip excluding node_modules/.next/, etc. (default: exclude them)",
    )
    parser.add_argument(
        "--extra-exclude",
        action="append",
        default=[],
        help="Additional rsync exclude pattern (repeatable)",
    )
    parser.add_argument(
        "--product-type",
        default="application",
        help="Loose product.type string (default: application)",
    )
    parser.add_argument("--operator-team", default="argus")
    parser.add_argument("--operator-name", default="")
    parser.add_argument(
        "--write-doctrine",
        action="store_true",
        help="Write minimal doctrine.yaml (explicitly non-speculative; policy mirrors cost cap only)",
    )
    parser.add_argument(
        "--skip-first-pass",
        action="store_true",
        help="Do not run argus products/signals/findings/decisions/ideas/orchestration",
    )
    parser.add_argument(
        "--no-uv",
        action="store_true",
        help="Invoke `argus` on PATH instead of `uv run argus`",
    )
    parser.add_argument(
        "--no-preserve-product-git",
        action="store_true",
        help="Exclude .git/ when syncing into products/<id>/ (legacy flat copy without repo metadata)",
    )
    parser.add_argument(
        "--full-git-history",
        action="store_true",
        help="Use a non-shallow clone in the import cache (slower; deeper git history in mirrored .git/)",
    )
    args = parser.parse_args(argv)

    repo_root = _repo_root_from_argv()
    if args.status:
        return run_status(repo_root, args.product_id, json_out=args.json)

    if not args.repo_url:
        parser.error("--repo-url is required unless --status is set")

    try:
        gh = parse_github_repo(args.repo_url)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    cache_root = Path(args.cache_dir) if args.cache_dir else repo_root / ".import_cache"
    product_root = (repo_root / "products" / args.product_id).resolve()

    preserve_product_git = not args.no_preserve_product_git
    excludes = build_exclude_list(
        include_cursor=args.include_cursor,
        include_local_db=args.include_local_db_artifacts,
        exclude_node_artifacts=not args.no_exclude_node_artifacts,
        extra=list(args.extra_exclude),
        preserve_product_git=preserve_product_git,
    )
    exclude_node_resolved = not args.no_exclude_node_artifacts
    imported_at = utc_now_iso()

    clone_path = ensure_git_repo_cache(
        cache_root, gh, shallow=not bool(args.full_git_history)
    )
    sync_tree(
        clone_path,
        product_root,
        excludes=excludes,
        delete=not args.no_delete,
    )
    if not preserve_product_git:
        write_non_git_import_marker(
            product_root,
            reason="Importer invoked with --no-preserve-product-git (rsync excluded `.git/`).",
        )

    branch = read_git_branch(clone_path)
    commit = read_git_commit(clone_path)

    facts = scan_repo(product_root, product_id=args.product_id, github_url=gh.normalized_url)
    shape = classify_product_shape(facts)

    assumptions = [
        f"product.type is set to {args.product_type!r} — not inferred from repository semantics alone.",
        "lifecycle.stage is validate (first-pass posture), not a market or readiness claim.",
        "actions.* point to Argus placeholder scripts unless you replace them later.",
        (
            f"raw_extensions.argus_onboarding.product_shape label is `{shape.label}` — heuristic from files "
            "on disk; it does not override `--product-type` and is not a runtime claim."
        ),
    ]
    uncertainties: list[str] = []
    if not facts.project_description and not facts.readme_first_line:
        uncertainties.append(
            "No project description or README first line — product.yaml description may be generic; not a substitute for README."
        )
    if not facts.test_path:
        uncertainties.append(
            "No pytest-style test file discovered — test_health signal may use fallbacks; JS test runners are not inferred here."
        )
    if facts.is_python_project and not facts.requires_python:
        uncertainties.append("requires-python not found in pyproject — left empty in onboarding metadata.")
    if facts.tech_hints:
        uncertainties.append(
            "tech_hints_grounded / observed_js_static_paths list files only — they are not framework or production claims."
        )
    weak_areas: list[str] = [
        "Manifest paths are heuristic (first test file, first doc) — may not match operator intent.",
        "Excluded DB files may drop local `data/*.db` — use --include-local-db-artifacts when needed.",
    ]
    domain_hints: list[str] = []
    for h in facts.tech_hints[:8]:
        domain_hints.append(h)
    if (product_root / "package.json").is_file() and not facts.is_python_project:
        domain_hints.append(
            "package.json without pyproject — review signals.yaml for additional anchors after import."
        )
    if "data" in facts.data_like_dirs or (product_root / "data").is_dir():
        domain_hints.append("data/ directory present — metrics.local_paths may need tuning; DB files may be excluded.")

    if args.skip_first_pass:
        ist = build_import_state(
            source_repo_url=gh.normalized_url,
            cache_slug=gh.slug,
            sync_excludes=excludes,
            include_cursor=args.include_cursor,
            include_local_db_artifacts=args.include_local_db_artifacts,
            exclude_node_artifacts=exclude_node_resolved,
            extra_excludes=list(args.extra_exclude),
            imported_from_branch=branch,
            imported_from_commit=commit,
            imported_at_utc=imported_at,
            first_pass_ran=False,
            first_pass_status="skipped",
            first_pass_summary_path=None,
            preserve_product_git=preserve_product_git,
        )
    else:
        ist = build_import_state(
            source_repo_url=gh.normalized_url,
            cache_slug=gh.slug,
            sync_excludes=excludes,
            include_cursor=args.include_cursor,
            include_local_db_artifacts=args.include_local_db_artifacts,
            exclude_node_artifacts=exclude_node_resolved,
            extra_excludes=list(args.extra_exclude),
            imported_from_branch=branch,
            imported_from_commit=commit,
            imported_at_utc=imported_at,
            first_pass_ran=False,
            first_pass_status="pending",
            first_pass_summary_path=None,
            preserve_product_git=preserve_product_git,
        )

    py = build_product_yaml(
        facts,
        operator_team=args.operator_team,
        operator_name=args.operator_name,
        product_type=args.product_type,
        import_state=ist,
        shape=shape,
    )
    write_product_yaml_file(product_root, py)
    write_placeholder_scripts(product_root, args.product_id)
    write_signals_yaml(product_root, facts, shape=shape)
    if args.write_doctrine:
        cap = py.get("constraints", {}).get("max_monthly_cost_usd")
        write_minimal_doctrine(product_root, facts, float(cap) if cap is not None else 10_000.0)
    write_import_notes(
        product_root,
        facts,
        excludes=excludes,
        assumptions=assumptions,
        uncertainties=uncertainties,
        weak_areas=weak_areas,
        domain_signal_hints=domain_hints,
        static_site_notes=STATIC_SITE_EXTENSION_NOTES,
        product_shape=shape,
    )

    raw, err = load_yaml_file(product_root / "product.yaml")
    if err or raw is None:
        print(f"error: failed to load product.yaml: {err}", file=sys.stderr)
        return 3
    val = validate_manifest(
        raw,
        repo_root=repo_root,
        product_root=product_root,
        config_path=product_root / "product.yaml",
    )
    if val.errors:
        for e in val.errors:
            print(f"validation error: {e}", file=sys.stderr)
        return 3
    for w in val.warnings:
        print(f"validation warning: {w}", file=sys.stderr)

    first_pass: FirstPassMetrics | None = None
    if not args.skip_first_pass:
        first_pass = run_first_pass_evaluation(
            repo_root,
            args.product_id,
            use_uv=not args.no_uv,
        )
        fp_status = classify_first_pass_status(skipped=False, metrics=first_pass)
        instrumentation = _build_import_report(
            facts,
            excludes,
            assumptions,
            uncertainties,
            weak_areas,
            domain_hints,
            val.errors,
            val.warnings,
            first_pass,
            product_shape=shape.to_onboarding_dict(),
            product_type=args.product_type,
        )
        write_first_pass_summary(
            product_root,
            repo_root,
            args.product_id,
            first_pass,
            import_instrumentation=instrumentation,
            use_uv=not args.no_uv,
        )
        summary_rel = repo_relative_summary_path(args.product_id)
        final_ist = build_import_state(
            source_repo_url=gh.normalized_url,
            cache_slug=gh.slug,
            sync_excludes=excludes,
            include_cursor=args.include_cursor,
            include_local_db_artifacts=args.include_local_db_artifacts,
            exclude_node_artifacts=exclude_node_resolved,
            extra_excludes=list(args.extra_exclude),
            imported_from_branch=branch,
            imported_from_commit=commit,
            imported_at_utc=imported_at,
            first_pass_ran=True,
            first_pass_status=fp_status,
            first_pass_summary_path=summary_rel,
            first_pass_command_errors=first_pass.errors if first_pass.errors else None,
            evaluation_error=first_pass.evaluation_error,
            preserve_product_git=preserve_product_git,
        )
        try:
            patch_product_yaml_import_state(product_root, final_ist)
        except ValueError as e:
            print(f"error: could not update import_state: {e}", file=sys.stderr)
            return 3
    else:
        instrumentation = _build_import_report(
            facts,
            excludes,
            assumptions,
            uncertainties,
            weak_areas,
            domain_hints,
            val.errors,
            val.warnings,
            None,
            product_shape=shape.to_onboarding_dict(),
            product_type=args.product_type,
        )

    report = dict(instrumentation)
    report["product_path"] = str(product_root)
    report["clone_cache"] = str(clone_path)
    report["display_name"] = display_name_from_facts(facts, args.product_id)
    raw_final, _ = load_yaml_file(product_root / "product.yaml")
    ist_out = extract_import_state(raw_final or {})
    report["import_state"] = ist_out if ist_out is not None else {}
    print(json.dumps(report, indent=2, default=str))
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "status":
        return _status_subcommand(argv[1:])
    if argv and argv[0] == "replay":
        return _replay_subcommand(argv[1:])
    if argv and argv[0] == "drift":
        return _drift_subcommand(argv[1:])
    return import_main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
