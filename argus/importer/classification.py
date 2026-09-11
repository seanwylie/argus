"""
Evidence-backed product *shape* labels for importer onboarding (no runtime or scale claims).

Shapes are heuristics over files and paths that exist under the synced product tree.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from argus.importer.discover import RepoFacts

ProductShapeLabel = Literal[
    "python_service",
    "python_cli",
    "js_frontend",
    "static_site",
    "mixed_app",
    "unknown",
]


@dataclass
class ProductShapeResult:
    """Hedged classification with inspectable evidence lines."""

    label: ProductShapeLabel
    evidence: list[str] = field(default_factory=list)
    hedged_summary: str = ""

    def to_onboarding_dict(self) -> dict[str, object]:
        return {
            "label": self.label,
            "evidence": list(self.evidence),
            "hedged_summary": self.hedged_summary,
            "disclaimer": (
                "Heuristic only — based on observed files, not runtime behavior, traffic, or architecture."
            ),
        }


def _has_docker_compose(root: Path) -> bool:
    return any((root / n).is_file() for n in ("Dockerfile", "docker-compose.yml", "docker-compose.yaml"))


def _package_json_script_keys(root: Path) -> list[str]:
    p = root / "package.json"
    if not p.is_file():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8", errors="replace"))
    except json.JSONDecodeError:
        return []
    if not isinstance(data, dict):
        return []
    scr = data.get("scripts")
    if not isinstance(scr, dict):
        return []
    keys = [str(k) for k in scr if str(k).strip()]
    return keys[:24]


def _js_tooling_markers(facts: RepoFacts) -> list[str]:
    """Filenames only (already grounded in scan_repo)."""
    markers: list[str] = []
    for rel in facts.grounded_js_static_paths:
        low = rel.lower()
        if any(
            x in low
            for x in (
                "vite.config",
                "next.config",
                "nuxt.config",
                "astro.config",
                "svelte.config",
                "angular.json",
            )
        ):
            markers.append(rel)
    return markers


def classify_product_shape(facts: RepoFacts) -> ProductShapeResult:
    """
    Assign a single shape label using deterministic rules and file presence only.

    Precedence: ``mixed_app`` → ``js_frontend`` / ``static_site`` → ``python_cli`` /
    ``python_service`` → ``unknown``.
    """
    root = facts.repo_root
    evidence: list[str] = []

    py = facts.is_python_project
    pj = facts.has_package_json
    scripts_py = list(facts.script_entries)
    npm_scripts = _package_json_script_keys(root)
    tooling = _js_tooling_markers(facts)
    has_root_index_html = (root / "index.html").is_file()
    dockerish = _has_docker_compose(root)
    src_dir = (root / "src").is_dir()
    app_dir = (root / "app").is_dir()

    # 1) Monorepo / full-stack: Python packaging + Node manifest
    if py and pj:
        evidence.append("Python project markers present (e.g. pyproject.toml / setup.py).")
        evidence.append("package.json present at repository root.")
        summary = (
            "Likely **mixed_app** — both Python packaging and `package.json` were observed; "
            "treat as multi-language tree until you confirm a single deployable surface."
        )
        return ProductShapeResult(label="mixed_app", evidence=evidence, hedged_summary=summary)

    # 2) JavaScript-first (no Python packaging)
    if pj and not py:
        for m in tooling[:6]:
            evidence.append(f"Observed tooling/config file: `{m}`.")
        if npm_scripts:
            evidence.append(
                "package.json `scripts` keys (sample): " + ", ".join(f"`{k}`" for k in npm_scripts[:8]) + "."
            )
        if facts.notable_layout_dirs:
            evidence.append(f"Notable layout dirs: {', '.join(facts.notable_layout_dirs)}.")
        if src_dir:
            evidence.append("`src/` directory present.")
        if tooling:
            summary = (
                "Likely **js_frontend** (or JS tooling monorepo) based on `package.json` + "
                + ", ".join(f"`{t}`" for t in tooling[:3])
                + (" + `src/`" if src_dir else "")
                + " — no Python packaging detected at repo root."
            )
        elif not tooling and not src_dir:
            summary = (
                "Tentatively **js_frontend** — `package.json` present without Python packaging; "
                "limited config markers — review README for actual entrypoints."
            )
        else:
            summary = (
                "Likely **js_frontend** based on `package.json`"
                + (" + `src/`" if src_dir else "")
                + " — no Python packaging detected at repo root."
            )
        return ProductShapeResult(label="js_frontend", evidence=evidence, hedged_summary=summary)

    # 3) Static HTML site without Node at root
    if not py and not pj and has_root_index_html:
        evidence.append("`index.html` present at repository root.")
        summary = (
            "Likely **static_site** — `index.html` at root without `package.json` or Python packaging "
            "at repo root (may still embed scripts or use a subdirectory build)."
        )
        return ProductShapeResult(label="static_site", evidence=evidence, hedged_summary=summary)

    # 4) Python projects without package.json
    if py and not pj:
        if scripts_py:
            for s in scripts_py[:8]:
                evidence.append(
                    f"Console script `{s.get('name')}` → `{s.get('target')}` ({s.get('source', '')})."
                )
            summary = (
                "Likely **python_cli** — `pyproject.toml` (or equivalent) exposes named entrypoints "
                "via `[project.scripts]`; not a claim about how operators run the app in production."
            )
            return ProductShapeResult(label="python_cli", evidence=evidence, hedged_summary=summary)

        if dockerish:
            evidence.append(
                "Container/deployment file present (`Dockerfile` and/or `docker-compose.yml`)."
            )
        if src_dir:
            evidence.append("`src/` directory present.")
        if app_dir:
            evidence.append("`app/` directory present.")
        if facts.has_pyproject:
            evidence.append("`pyproject.toml` present (no `[project.scripts]` surfaced by scan).")
        if dockerish or src_dir or app_dir:
            summary = (
                "Likely **python_service** or library — Python packaging without importer-detected CLI scripts; "
                + ("container files suggest deployable service — " if dockerish else "")
                + "verify entrypoint in README."
            )
            return ProductShapeResult(label="python_service", evidence=evidence, hedged_summary=summary)

        evidence.append("Python project without `package.json` and without listed console scripts in scan.")
        summary = (
            "Tentatively **python_service** — Python tree without `package.json` and without "
            "`[project.scripts]` in the parsed manifest; may be a library or app with another entry mechanism."
        )
        return ProductShapeResult(label="python_service", evidence=evidence, hedged_summary=summary)

    # 5) Fallback
    evidence.append(f"top_level (sample): {', '.join(facts.top_level[:12])}.")
    summary = (
        "**unknown** — insufficient distinctive markers (e.g. only docs, or unusual layout); "
        "see grounded facts below and README."
    )
    return ProductShapeResult(label="unknown", evidence=evidence, hedged_summary=summary)


def shape_signals_yaml_preamble(shape: ProductShapeResult) -> str:
    """One-line comment for signals.yaml header (deterministic, hedged)."""
    return (
        f"# Heuristic product shape: {shape.label} — {shape.hedged_summary.replace(chr(10), ' ')[:220]}"
    )


def shape_dependency_description(facts: RepoFacts, shape: ProductShapeResult) -> str:
    """Tune default dependency manifest row description without claiming runtime."""
    if shape.label == "mixed_app":
        return (
            "Python + JS manifests observed — dependency anchor (pyproject and/or package.json); "
            "not proof of a single deploy unit."
        )
    if shape.label == "js_frontend":
        return "package.json dependency/manifest anchor (JS tree; heuristic shape: js_frontend)."
    if shape.label == "static_site":
        return "Static site heuristic — see index.html / root layout; filesystem anchor only."
    if shape.label == "python_cli":
        return "Python project manifest (console scripts may exist — see onboarding entry_points)."
    if shape.label == "python_service":
        return "Python project manifest (no package.json; service/library heuristic — see README)."
    return "Project manifest / dependencies (filesystem anchor)."
