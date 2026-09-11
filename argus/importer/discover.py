"""Inspect a cloned repository and extract grounded facts for scaffolding."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class RepoFacts:
    """Facts inferred from files that exist on disk (no LLM)."""

    product_id: str
    repo_root: Path
    github_url: str
    top_level: list[str] = field(default_factory=list)
    has_readme: bool = False
    readme_first_line: str = ""
    pyproject_path: Path | None = None
    project_name: str = ""
    project_description: str = ""
    requires_python: str = ""
    script_entries: list[dict[str, str]] = field(default_factory=list)
    package_manager: str = ""
    test_path: str | None = None
    doc_path: str | None = None
    has_security_md: bool = False
    has_pyproject: bool = False
    has_setup_py: bool = False
    has_package_json: bool = False
    is_python_project: bool = False
    data_like_dirs: list[str] = field(default_factory=list)
    #: Repo-relative paths to config/entry files observed (JS/static/tooling); no framework inference.
    grounded_js_static_paths: list[str] = field(default_factory=list)
    #: Notable top-level dirs when present (e.g. ``public/``, ``src/``, ``app/``).
    notable_layout_dirs: list[str] = field(default_factory=list)
    #: Short grounded hints for onboarding notes (not product claims).
    tech_hints: list[str] = field(default_factory=list)

    def summary_lines(self) -> list[str]:
        lines = [
            f"github_url: {self.github_url}",
            f"top_level: {', '.join(self.top_level[:40])}{' …' if len(self.top_level) > 40 else ''}",
            f"python_project: {self.is_python_project}",
        ]
        if self.pyproject_path:
            lines.append(f"pyproject: {self.pyproject_path.name}")
        if self.test_path:
            lines.append(f"test_anchor: {self.test_path}")
        if self.doc_path:
            lines.append(f"doc_anchor: {self.doc_path}")
        if self.grounded_js_static_paths:
            lines.append(f"js_static_paths: {', '.join(self.grounded_js_static_paths[:20])}")
        if self.notable_layout_dirs:
            lines.append(f"layout_dirs: {', '.join(self.notable_layout_dirs)}")
        return lines


def _read_text_limit(path: Path, max_chars: int = 4000) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")[:max_chars]
    except OSError:
        return ""


def _parse_pyproject(path: Path) -> dict[str, Any]:
    try:
        import tomllib
    except ImportError:  # pragma: no cover
        import tomli as tomllib  # type: ignore[no-redef]

    raw = path.read_bytes()
    return tomllib.loads(raw.decode("utf-8", errors="replace"))


def _find_test_file(root: Path) -> str | None:
    candidates: list[Path] = []
    for pattern in ("tests", "test"):
        base = root / pattern
        if not base.is_dir():
            continue
        for p in sorted(base.rglob("test_*.py")):
            candidates.append(p)
        for p in sorted(base.rglob("*_test.py")):
            candidates.append(p)
    if not candidates:
        return None
    best = sorted(candidates, key=lambda p: len(p.as_posix()))[0]
    return best.relative_to(root).as_posix()


def _collect_grounded_js_static_paths(root: Path) -> list[str]:
    """File/directory paths that commonly appear in JS/static sites — only if they exist."""
    found: list[str] = []

    def add_file(rel: str) -> None:
        if (root / rel).is_file() and rel not in found:
            found.append(rel)

    def add_dir(rel: str) -> None:
        p = root / rel.rstrip("/")
        if p.is_dir() and rel not in found:
            found.append(rel if rel.endswith("/") else rel + "/")

    for name in (
        "package.json",
        "package-lock.json",
        "pnpm-lock.yaml",
        "yarn.lock",
        "bun.lockb",
    ):
        add_file(name)

    for glob in (
        "playwright.config.*",
        "cypress.config.*",
        "vite.config.*",
        "astro.config.*",
        "next.config.*",
        "nuxt.config.*",
    ):
        for p in sorted(root.glob(glob)):
            if p.is_file():
                rel = p.relative_to(root).as_posix()
                if rel not in found:
                    found.append(rel)

    for name in ("cypress.json", "netlify.toml", "vercel.json"):
        add_file(name)

    if (root / "index.html").is_file():
        add_file("index.html")

    for d in ("public", "src", "app"):
        add_dir(d)

    return found


def _tech_hints_from_paths(paths: list[str]) -> list[str]:
    hints: list[str] = []
    for rel in paths:
        low = rel.lower()
        if "playwright" in low:
            hints.append(f"Playwright config present: {rel} (tooling only; not a test result).")
        elif "cypress" in low:
            hints.append(f"Cypress config present: {rel} (tooling only).")
        elif low == "netlify.toml":
            hints.append("netlify.toml present (deployment config file).")
        elif low == "vercel.json":
            hints.append("vercel.json present (deployment config file).")
        elif "vite.config" in low:
            hints.append(f"Vite config present: {rel}.")
        elif "next.config" in low:
            hints.append(f"Next.js config file present: {rel} (does not assert runtime behavior).")
        elif "nuxt.config" in low:
            hints.append(f"Nuxt config file present: {rel}.")
        elif "astro.config" in low:
            hints.append(f"Astro config file present: {rel}.")
    # dedupe preserve order
    seen: set[str] = set()
    out: list[str] = []
    for h in hints:
        if h not in seen:
            seen.add(h)
            out.append(h)
    return out


def _find_doc_file(root: Path) -> str | None:
    for rel in ("docs/README.md", "docs/readme.md", "docs/index.md"):
        p = root / rel
        if p.is_file():
            return rel
    docs = root / "docs"
    if docs.is_dir():
        md = sorted(docs.glob("*.md"))
        if md:
            return md[0].relative_to(root).as_posix()
    return None


def scan_repo(
    repo_root: Path,
    *,
    product_id: str,
    github_url: str,
) -> RepoFacts:
    repo_root = repo_root.resolve()
    facts = RepoFacts(product_id=product_id, repo_root=repo_root, github_url=github_url)
    if repo_root.is_dir():
        facts.top_level = sorted(p.name for p in repo_root.iterdir() if not p.name.startswith("."))
    facts.has_readme = (repo_root / "README.md").is_file()
    if facts.has_readme:
        line = _read_text_limit(repo_root / "README.md", 400).splitlines()
        facts.readme_first_line = line[0].strip() if line else ""

    pp = repo_root / "pyproject.toml"
    if pp.is_file():
        facts.has_pyproject = True
        facts.pyproject_path = pp
        try:
            data = _parse_pyproject(pp)
        except Exception:
            data = {}
        proj = data.get("project")
        if isinstance(proj, dict):
            facts.project_name = str(proj.get("name") or "").strip()
            desc = proj.get("description")
            if isinstance(desc, str):
                facts.project_description = desc.strip()
            req = proj.get("requires-python")
            if isinstance(req, str):
                facts.requires_python = req.strip()
            scripts = proj.get("scripts")
            if isinstance(scripts, dict):
                for name, target in sorted(scripts.items()):
                    facts.script_entries.append(
                        {"name": str(name), "target": str(target), "source": "pyproject.toml [project.scripts]"}
                    )
        tool = data.get("tool")
        if isinstance(tool, dict) and isinstance(tool.get("uv"), dict):
            facts.package_manager = "uv"
        elif (repo_root / "uv.lock").is_file():
            facts.package_manager = "uv"
        elif (repo_root / "Pipfile.lock").is_file():
            facts.package_manager = "pipenv"
        elif (repo_root / "poetry.lock").is_file():
            facts.package_manager = "poetry"
        elif (repo_root / "requirements.txt").is_file():
            facts.package_manager = "pip"
        else:
            facts.package_manager = "pip"

    facts.has_setup_py = (repo_root / "setup.py").is_file()
    pj = repo_root / "package.json"
    if pj.is_file():
        facts.has_package_json = True
        try:
            meta = json.loads(pj.read_text(encoding="utf-8", errors="replace"))
            if isinstance(meta, dict) and not facts.project_name:
                facts.project_name = str(meta.get("name") or "").strip()
        except json.JSONDecodeError:
            pass

    facts.is_python_project = bool(
        facts.has_pyproject or facts.has_setup_py or ((repo_root / "src").is_dir() and facts.has_readme)
    )

    facts.test_path = _find_test_file(repo_root)
    facts.doc_path = _find_doc_file(repo_root)
    facts.has_security_md = (repo_root / "SECURITY.md").is_file()

    for d in ("data", "metrics", "var", "storage"):
        if (repo_root / d).is_dir():
            facts.data_like_dirs.append(f"{d}/")

    facts.grounded_js_static_paths = _collect_grounded_js_static_paths(repo_root)
    facts.tech_hints = _tech_hints_from_paths(facts.grounded_js_static_paths)
    for dirname in ("public", "src", "app"):
        p = repo_root / dirname
        if p.is_dir() and f"{dirname}/" not in facts.notable_layout_dirs:
            facts.notable_layout_dirs.append(f"{dirname}/")

    return facts


def display_name_from_facts(facts: RepoFacts, product_id: str) -> str:
    if facts.project_name:
        return facts.project_name.replace("-", " ").replace("_", " ").strip().title()
    return product_id.replace("-", " ").replace("_", " ").title()
