"""
Bounded **GitHub → products/** onboarding: fetch, admit, minimal bootstrap, durable artifacts.

Boundary (inspectable stages):

1. **parse** — validate URL, derive product id, record inputs
2. **scaffold** — create ``products/<id>/`` via :func:`argus.products.scaffold.create_product_scaffold`
3. **fetch** — ``git clone`` into ``products/<id>/app/`` (replaces scaffold placeholder)
4. **admit** — merge ``raw_extensions.argus_github_onboarding`` into ``product.yaml``, re-validate
5. **bootstrap** — :func:`argus.products.creation_bootstrap.evaluate_product_creation_bootstrap` (minimal by default)
6. **orchestration_refresh** — persist ``runs/orchestration/latest/<id>.json`` via :func:`write_orchestration_state`
7. **readiness_summary** — embed :func:`build_product_readiness_payload` for operator visibility

Does **not**: portfolio cycle, autonomous runner, or importer cache-sync (separate subsystem).
"""

from __future__ import annotations

import re
import shutil
import subprocess
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from argus.core.serialize import dumps_json
from argus.orchestrator.state_pass import write_orchestration_state
from argus.portfolio.product_readiness import build_product_readiness_payload
from argus.products.creation_bootstrap import evaluate_product_creation_bootstrap
from argus.products.loader import attach_paths, load_yaml_file
from argus.products.scaffold import TEMPLATE_TYPES, create_product_scaffold, normalize_product_slug
from argus.products.validate import validate_manifest

GITHUB_ONBOARDING_PAYLOAD_SCHEMA = "argus.portfolio_github_onboarding.v1"
GITHUB_ONBOARDING_EXT_SCHEMA = "argus.github_onboarding.v1"


_GITHUB_URL = re.compile(
    r"^https://github\.com/(?P<owner>[\w.-]+)/(?P<repo>[\w.-]+?)(?:\.git)?/?(?:#.*)?$",
    re.IGNORECASE,
)

_GITHUB_SSH = re.compile(
    r"^git@github\.com:(?P<owner>[\w.-]+)/(?P<repo>[\w.-]+?)(?:\.git)?/?$",
    re.IGNORECASE,
)


def github_onboarding_runs_dir(repo_root: Path) -> Path:
    return Path(repo_root).resolve() / "runs" / "products" / "github_onboarding"


def parse_github_https_url(url: str) -> dict[str, Any]:
    """
    Parse a GitHub HTTPS URL only. Returns owner, repo, normalized HTTPS clone URL.

    Raises ``ValueError`` on unsupported input.
    """
    raw = str(url or "").strip()
    if not raw:
        raise ValueError("empty URL")
    m = _GITHUB_URL.match(raw.split("#", 1)[0].strip())
    if not m:
        raise ValueError(
            "unsupported URL: expected https://github.com/<owner>/<repo> (optional .git); "
            "enterprise hosts are not enabled in this version"
        )
    owner = m.group("owner")
    repo = m.group("repo")
    clone_url = f"https://github.com/{owner}/{repo}.git"
    slug_hint = normalize_product_slug(repo)
    return {
        "owner": owner,
        "repo": repo,
        "clone_url": clone_url,
        "default_product_id_hint": slug_hint,
    }


def parse_github_url(url: str) -> dict[str, Any]:
    """
    Parse a github.com clone URL (HTTPS or ``git@github.com:...`` SSH).

    Returns owner, repo, and ``clone_url`` suitable for ``git clone`` (HTTPS or SSH).
    """
    raw = str(url or "").strip()
    if not raw:
        raise ValueError("empty URL")
    stripped = raw.split("#", 1)[0].strip()

    m = _GITHUB_URL.match(stripped)
    if m:
        owner = m.group("owner")
        repo = m.group("repo")
        clone_url = f"https://github.com/{owner}/{repo}.git"
        slug_hint = normalize_product_slug(repo)
        return {
            "owner": owner,
            "repo": repo,
            "clone_url": clone_url,
            "default_product_id_hint": slug_hint,
        }

    m = _GITHUB_SSH.match(stripped)
    if m:
        owner = m.group("owner")
        repo = m.group("repo")
        clone_url = f"git@github.com:{owner}/{repo}.git"
        slug_hint = normalize_product_slug(repo)
        return {
            "owner": owner,
            "repo": repo,
            "clone_url": clone_url,
            "default_product_id_hint": slug_hint,
        }

    raise ValueError(
        "unsupported URL: expected https://github.com/<owner>/<repo> or "
        "git@github.com:<owner>/<repo> (optional .git); "
        "enterprise hosts are not enabled in this version"
    )


def _merge_github_extension(raw: dict[str, Any], ext: dict[str, Any]) -> dict[str, Any]:
    out = dict(raw)
    rx = out.get("raw_extensions")
    if not isinstance(rx, dict):
        rx = {}
    else:
        rx = dict(rx)
    rx["argus_github_onboarding"] = ext
    out["raw_extensions"] = rx
    return out


def _dump_yaml(data: dict[str, Any]) -> str:
    try:
        import yaml  # type: ignore[import-untyped]
    except ImportError as e:  # pragma: no cover
        raise ImportError("YAML support requires the 'pyyaml' package.") from e
    return yaml.safe_dump(
        data,
        sort_keys=True,
        default_flow_style=False,
        allow_unicode=True,
    )


def _git_rev_parse(app_dir: Path) -> str | None:
    try:
        proc = subprocess.run(
            ["git", "-C", str(app_dir), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        if proc.returncode != 0:
            return None
        return proc.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def run_github_onboarding(
    repo_root: Path,
    *,
    github_url: str,
    product_id: str | None = None,
    branch: str | None = None,
    template_type: str = "micro_saas",
    products_dir: Path | None = None,
    force: bool = False,
    dry_run: bool = False,
    run_bootstrap: bool = True,
    minimal_bootstrap: bool = True,
    write_artifacts: bool = True,
) -> dict[str, Any]:
    """
    Run the bounded onboarding pipeline. Returns a durable payload (schema ``argus.portfolio_github_onboarding.v1``).
    """
    root = repo_root.resolve()
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    evaluated_at = datetime.now(timezone.utc).isoformat()

    base: dict[str, Any] = {
        "schema": GITHUB_ONBOARDING_PAYLOAD_SCHEMA,
        "run_id": run_id,
        "evaluated_at_utc": evaluated_at,
        "ok": False,
        "dry_run": dry_run,
        "stages": {},
        "error": None,
        "product_id": None,
        "github_url_input": str(github_url).strip(),
    }

    # --- parse ---
    try:
        parsed = parse_github_url(github_url)
    except ValueError as e:
        base["error"] = str(e)
        base["stages"]["parse"] = {"status": "failed", "detail": str(e)}
        if write_artifacts:
            _write_onboarding_artifacts(root, base)
        return base

    pid = str(product_id or "").strip() or parsed["default_product_id_hint"]
    try:
        pid = normalize_product_slug(pid)
    except ValueError as e:
        base["error"] = str(e)
        base["stages"]["parse"] = {"status": "failed", "detail": str(e)}
        if write_artifacts:
            _write_onboarding_artifacts(root, base)
        return base

    base["product_id"] = pid
    base["parsed_github"] = parsed
    base["branch"] = str(branch).strip() if branch else None
    base["stages"]["parse"] = {"status": "ok", "detail": parsed}

    if template_type not in TEMPLATE_TYPES:
        base["error"] = f"unknown template {template_type!r}"
        base["stages"]["template"] = {"status": "failed", "detail": base["error"]}
        if write_artifacts:
            _write_onboarding_artifacts(root, base)
        return base

    if dry_run:
        base["ok"] = True
        base["stages"]["scaffold"] = {"status": "skipped", "reason": "dry_run"}
        base["stages"]["fetch"] = {"status": "skipped", "reason": "dry_run"}
        base["stages"]["admit"] = {"status": "skipped", "reason": "dry_run"}
        base["stages"]["bootstrap"] = {"status": "skipped", "reason": "dry_run"}
        base["stages"]["orchestration_refresh"] = {"status": "skipped", "reason": "dry_run"}
        base["stages"]["readiness"] = {"status": "skipped", "reason": "dry_run"}
        base["note"] = "Dry run — no filesystem or git mutations."
        if write_artifacts:
            _write_onboarding_artifacts(root, base)
        return base

    pdir = (root / "products") if products_dir is None else products_dir.resolve()
    product_root = pdir / pid
    app_dir = product_root / "app"

    # --- scaffold ---
    code, msg, _git = create_product_scaffold(
        root,
        pid,
        template_type=template_type,
        products_dir=pdir,
        force=force,
    )
    if code != 0:
        base["error"] = msg
        base["stages"]["scaffold"] = {"status": "failed", "detail": msg}
        if write_artifacts:
            _write_onboarding_artifacts(root, base)
        return base
    base["stages"]["scaffold"] = {"status": "ok", "detail": msg}

    # --- fetch (git clone into app/) ---
    try:
        if app_dir.exists():
            shutil.rmtree(app_dir)
        clone_cmd = ["git", "clone", "--depth", "1"]
        if branch:
            clone_cmd.extend(["--branch", branch])
        clone_cmd.extend([parsed["clone_url"], str(app_dir)])
        proc = subprocess.run(
            clone_cmd,
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=600,
            check=False,
        )
        if proc.returncode != 0:
            err = proc.stderr.strip() or proc.stdout.strip() or f"exit {proc.returncode}"
            base["error"] = f"git clone failed: {err}"
            base["stages"]["fetch"] = {"status": "failed", "detail": err, "command": clone_cmd}
            if write_artifacts:
                _write_onboarding_artifacts(root, base)
            return base
        rev = _git_rev_parse(app_dir)
        base["stages"]["fetch"] = {
            "status": "ok",
            "detail": {"clone_url": parsed["clone_url"], "branch": branch, "resolved_head": rev},
        }
    except subprocess.TimeoutExpired:
        base["error"] = "git clone timed out"
        base["stages"]["fetch"] = {"status": "failed", "detail": base["error"]}
        if write_artifacts:
            _write_onboarding_artifacts(root, base)
        return base
    except OSError as e:
        base["error"] = f"git clone: {e}"
        base["stages"]["fetch"] = {"status": "failed", "detail": str(e)}
        if write_artifacts:
            _write_onboarding_artifacts(root, base)
        return base

    # --- admit (merge raw_extensions) ---
    config_path = product_root / "product.yaml"
    raw, err = load_yaml_file(config_path)
    if err is not None or raw is None:
        base["error"] = f"cannot load product.yaml after clone: {err}"
        base["stages"]["admit"] = {"status": "failed", "detail": base["error"]}
        if write_artifacts:
            _write_onboarding_artifacts(root, base)
        return base

    ext = {
        "schema": GITHUB_ONBOARDING_EXT_SCHEMA,
        "source_url": parsed["clone_url"],
        "owner": parsed["owner"],
        "repo": parsed["repo"],
        "branch_requested": branch,
        "resolved_commit": rev,
        "onboarded_at_utc": evaluated_at,
        "onboarding_run_id": run_id,
    }
    merged_yaml = _merge_github_extension(raw, ext)
    config_path.write_text(_dump_yaml(merged_yaml), encoding="utf-8")

    merged = attach_paths(
        merged_yaml,
        repo_root=root,
        product_root=product_root,
        config_path=config_path,
    )
    vresult = validate_manifest(merged, repo_root=root, product_root=product_root, config_path=config_path)
    if vresult.errors:
        base["error"] = "validation failed after admit: " + "; ".join(vresult.errors)
        base["stages"]["admit"] = {"status": "failed", "detail": base["error"]}
        if write_artifacts:
            _write_onboarding_artifacts(root, base)
        return base

    base["stages"]["admit"] = {
        "status": "ok",
        "detail": str(config_path.relative_to(root)),
        "warnings": list(vresult.warnings),
    }

    # --- bootstrap ---
    if not run_bootstrap:
        base["stages"]["bootstrap"] = {"status": "skipped", "reason": "run_bootstrap=False"}
        base["ok"] = True
        _finish_orchestration_and_readiness(root, base, pid, products_dir, write_artifacts)
        return base

    try:
        boot = evaluate_product_creation_bootstrap(
            root,
            pid,
            minimal=minimal_bootstrap,
            dry_run=False,
            products_dir=products_dir,
        )
        base["bootstrap_payload"] = boot
        if boot.get("ok"):
            base["stages"]["bootstrap"] = {"status": "ok", "detail": boot.get("steps_executed")}
        else:
            base["error"] = boot.get("error") or "bootstrap failed"
            base["stages"]["bootstrap"] = {"status": "failed", "detail": base["error"]}
            if write_artifacts:
                _write_onboarding_artifacts(root, base)
            return base
    except Exception as e:
        base["error"] = f"{type(e).__name__}: {e}"
        base["traceback"] = traceback.format_exc()
        base["stages"]["bootstrap"] = {"status": "failed", "detail": base["error"]}
        if write_artifacts:
            _write_onboarding_artifacts(root, base)
        return base

    base["ok"] = True
    _finish_orchestration_and_readiness(root, base, pid, products_dir, write_artifacts)
    return base


def _finish_orchestration_and_readiness(
    root: Path,
    base: dict[str, Any],
    pid: str,
    products_dir: Path | None,
    write_artifacts: bool,
) -> None:
    try:
        from argus.temporal.persistence import refresh_temporal_from_signals_latest

        tr = refresh_temporal_from_signals_latest(root, pid)
        base["stages"]["temporal_refresh"] = {
            "status": "ok" if tr else "skipped",
            "detail": "refreshed from runs/signals/latest" if tr else "no signals bundle yet",
        }
    except Exception as e:
        base["stages"]["temporal_refresh"] = {"status": "failed", "detail": str(e)}

    try:
        op = write_orchestration_state(root, pid)
        base["stages"]["orchestration_refresh"] = {
            "status": "ok",
            "detail": str(op.relative_to(root)),
        }
    except Exception as e:
        base["stages"]["orchestration_refresh"] = {"status": "failed", "detail": str(e)}

    try:
        pr = build_product_readiness_payload(root, pid, products_dir=products_dir)
        base["readiness_summary"] = pr
        base["stages"]["readiness"] = {"status": "ok"}
    except Exception as e:
        base["stages"]["readiness"] = {"status": "failed", "detail": str(e)}

    if write_artifacts:
        _write_onboarding_artifacts(root, base)


def _write_onboarding_artifacts(repo_root: Path, payload: dict[str, Any]) -> None:
    d = github_onboarding_runs_dir(repo_root)
    d.mkdir(parents=True, exist_ok=True)
    rid = str(payload.get("run_id") or "latest")
    text = dumps_json(payload) + "\n"
    md = render_github_onboarding_markdown(payload)
    (d / f"{rid}.json").write_text(text, encoding="utf-8")
    (d / f"{rid}.md").write_text(md, encoding="utf-8")
    (d / "latest.json").write_text(text, encoding="utf-8")
    (d / "latest.md").write_text(md, encoding="utf-8")


def render_github_onboarding_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# GitHub onboarding",
        "",
        f"**Schema:** `{payload.get('schema')}`",
        f"**Run id:** `{payload.get('run_id')}`",
        f"**Evaluated (UTC):** {payload.get('evaluated_at_utc')}",
        f"**OK:** {payload.get('ok')}",
        "",
        "## Summary",
        "",
        f"- **product_id:** `{payload.get('product_id')}`",
        f"- **URL:** `{payload.get('github_url_input')}`",
        f"- **dry_run:** {payload.get('dry_run')}",
        f"- **error:** {payload.get('error') or '—'}",
        "",
        "## Stages",
        "",
    ]
    st = payload.get("stages") or {}
    for k, v in sorted(st.items()):
        if isinstance(v, dict):
            lines.append(f"- **{k}:** `{v.get('status')}` — {v.get('detail', v.get('reason', ''))}")
        else:
            lines.append(f"- **{k}:** {v!r}")
    rs = payload.get("readiness_summary") or {}
    if rs:
        pp = rs.get("project_permissions") or {}
        pol = pp.get("policy") or {}
        perms = pol.get("permissions") or {}
        perm_line = ", ".join(f"{k}={v}" for k, v in sorted(perms.items())[:7]) if perms else "—"
        lines.extend([
            "",
            "## Readiness hint",
            "",
            f"- **evidence_maturity_hint:** `{rs.get('evidence_maturity_hint')}`",
            f"- **thin_evidence_baseline:** `{rs.get('thin_evidence_baseline')}`",
            f"- **project permissions (Phase 1):** `{perm_line}` — edit `products/<id>/argus.policy.yaml` to revise",
            "",
        ])
    lines.append(str(payload.get("note") or ""))
    return "\n".join(lines).rstrip() + "\n"
