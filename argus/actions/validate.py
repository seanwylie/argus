"""Validate action contracts against repo layout and policy."""

from __future__ import annotations

import json
import re
import shlex
from pathlib import Path
from typing import Any

from argus.actions.models import (
    KNOWN_ACTION_TYPES,
    ActionContract,
    LifecycleTransitionSpec,
)
from argus.core.models.enums import LifecycleStage
from argus.core.models.product import ProductLifecycle
from argus.products.inventory import ProductInventory, build_inventory
from argus.products.loader import load_yaml_file


def load_action_file(path: Path) -> tuple[ActionContract | None, str | None]:
    """
    Load an action contract from ``.yaml``/``.yml`` or ``.json``.

    Returns ``(contract, None)`` or ``(None, error_message)``.
    """
    try:
        raw_text = path.read_text(encoding="utf-8")
    except OSError as e:
        return None, f"Cannot read {path}: {e}"

    suffix = path.suffix.lower()
    data: dict[str, Any] | None = None
    if suffix in (".yaml", ".yml"):
        raw, err = load_yaml_file(path)
        if err is not None:
            return None, err
        data = raw
    elif suffix == ".json":
        try:
            loaded = json.loads(raw_text)
        except json.JSONDecodeError as e:
            return None, f"Invalid JSON in {path}: {e}"
        if loaded is None:
            data = {}
        elif not isinstance(loaded, dict):
            return None, f"Action JSON must be an object at top level, got {type(loaded).__name__}"
        else:
            data = dict(loaded)
    else:
        return None, f"Unsupported action file type {suffix!r} (use .yaml, .yml, or .json)"

    assert data is not None
    return ActionContract.from_mapping(data), None


def resolve_working_directory(repo_root: Path, working_directory: str) -> tuple[Path | None, str | None]:
    """
    Resolve ``working_directory`` under ``repo_root``.

    Rejects absolute paths and paths that escape the repository via ``..``.
    Returns ``(resolved_path, None)`` or ``(None, error_message)``.
    """
    s = working_directory.strip()
    if not s:
        return None, "working_directory is empty"
    rel = Path(s)
    if rel.is_absolute():
        return None, "working_directory must be a relative path within the repository"
    parts = rel.parts
    if ".." in parts:
        return None, "working_directory must not contain '..'"

    root = repo_root.resolve()
    candidate = (root / rel).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None, "working_directory resolves outside the repository root"
    return candidate, None


def _looks_like_path_token(tok: str) -> bool:
    t = tok.strip().strip("'\"")
    if not t or t.startswith("-"):
        return False
    if t.startswith(("|", ">", "<", "&")):
        return False
    if t.endswith((".sh", ".bash", ".py")):
        return True
    if t.startswith("./") or t.startswith("../"):
        return True
    if "/" in t and not t.startswith("$"):
        return True
    return False


def _requires_existence_check(tok: str) -> bool:
    """
    Tokens for which we require on-disk presence.

    We skip bare ``../`` segments (often tarball or log outputs); explicit
    ``./`` scripts and paths under ``scripts/`` still count.
    """
    t = tok.strip().strip("'\"")
    if t.startswith("./"):
        return True
    if t.startswith("../") and "scripts/" in t:
        return True
    if t.startswith("../"):
        return False
    if t.endswith((".sh", ".bash", ".py")):
        return True
    if "scripts/" in t:
        return True
    return False


def referenced_path_tokens(command: str) -> list[str]:
    """Split ``command`` and return tokens that may denote scripts or paths."""
    try:
        parts = shlex.split(command, posix=True)
    except ValueError:
        return []
    out: list[str] = []
    for p in parts:
        p = p.strip()
        if _looks_like_path_token(p):
            out.append(p.strip("'\""))
    return out


def script_and_hook_path_tokens(command: str) -> list[str]:
    """Tokens for which we require on-disk presence when validating."""
    return [t for t in referenced_path_tokens(command) if _requires_existence_check(t)]


def check_referenced_files(
    repo_root: Path,
    cwd: Path,
    tokens: list[str],
) -> list[tuple[str, bool]]:
    """
    For each token, return ``(repo_relative_or_display_path, exists)``.

    Resolution is relative to ``cwd`` (intended working directory).
    """
    results: list[tuple[str, bool]] = []
    root = repo_root.resolve()
    for tok in tokens:
        rel = Path(tok)
        if rel.is_absolute():
            candidate = rel.resolve()
            try:
                rel_disp = candidate.relative_to(root).as_posix()
            except ValueError:
                rel_disp = tok
            results.append((rel_disp, candidate.exists()))
            continue
        candidate = (cwd / rel).resolve()
        try:
            rel_to_root = candidate.relative_to(root)
            disp = rel_to_root.as_posix()
        except ValueError:
            disp = tok
        results.append((disp, candidate.is_file() or candidate.is_dir()))
    return results


# --- Dangerous pattern detection (heuristic; conservative) ---

# e.g. ``rm -rf``, ``rm -fr``, ``rm -Rf`` (single flag token)
_RE_RM_RF = re.compile(r"\brm\b.*\s-(rf|fr|Rf|rF|RF|FR)(\s|$)", re.I | re.S)
_RE_GIT_RESET_HARD = re.compile(r"\bgit\s+reset\s+--hard\b", re.I)
_RE_GIT_CLEAN_DANGEROUS = re.compile(r"\bgit\s+clean\s+-[a-zA-Z]*[xfd]", re.I)
_RE_REDIRECT_ABS = re.compile(r"[12]?(?:>>|>)\s*([/~][^\s;&|`'\"]+|/[^\s;&|`'\"]+)")
_RE_REDIRECT_SUSPICIOUS = re.compile(r"[12]?(?:>>|>)\s*(\.\./|\.\.[/\\])")
_RE_MKDIR_RM_PRODUCTS = re.compile(
    r"\b(?:rm|rmdir)\s+.*\bproducts[/\\]",
    re.I,
)


def dangerous_patterns(
    command: str,
    *,
    repo_root: Path,
    product_id: str,
    lifecycle: LifecycleTransitionSpec | None,
) -> list[str]:
    """
    Return human-readable flags for risky shell patterns.

    This is heuristic: intended to surface mistakes before any executor runs.
    """
    flags: list[str] = []
    cmd = command.strip()
    if not cmd:
        return flags

    if _RE_RM_RF.search(cmd):
        flags.append("command contains `rm` with recursive/force flags (`-rf` / `-fr`)")
    elif re.search(r"\brm\b", cmd):
        compact = re.sub(r"\s+", "", cmd)
        if "-rf" in compact or "-fr" in compact:
            flags.append("command contains `rm` with recursive/force flags (`-rf` / `-fr`)")
        elif re.search(r"(?<![a-zA-Z0-9])-r\b", cmd) and re.search(
            r"(?<![a-zA-Z0-9])-f\b", cmd
        ):
            flags.append("command contains `rm` with separate `-r` and `-f` flags")

    if _RE_GIT_RESET_HARD.search(cmd):
        flags.append("command contains `git reset --hard`")

    if _RE_GIT_CLEAN_DANGEROUS.search(cmd):
        flags.append("command contains destructive `git clean` flags")

    if _RE_MKDIR_RM_PRODUCTS.search(cmd):
        flags.append("command may delete or remove paths under products/")

    # Targeted: removing this product's directory
    esc = re.escape(product_id)
    if re.search(rf"\brm\b.*\bproducts[/\\]{esc}\b", cmd, re.I):
        flags.append(f"command appears to remove products/{product_id}/")

    root = repo_root.resolve()
    for m in _RE_REDIRECT_ABS.finditer(cmd):
        target = m.group(1).strip()
        if target.startswith("~"):
            flags.append(f"shell redirection to home-relative path: {target!r}")
            continue
        p = Path(target)
        if not p.is_absolute():
            continue
        try:
            p.resolve().relative_to(root)
        except ValueError:
            flags.append(f"shell redirection outside repository: {target!r}")

    if _RE_REDIRECT_SUSPICIOUS.search(cmd):
        flags.append("shell redirection targets a parent path (..)")

    if lifecycle is not None:
        try:
            fr = LifecycleStage(lifecycle.from_stage.strip().lower())
            to = LifecycleStage(lifecycle.to_stage.strip().lower())
        except ValueError:
            flags.append(
                "lifecycle_transition uses unknown stage name(s) "
                f"({lifecycle.from_stage!r} -> {lifecycle.to_stage!r})"
            )
        else:
            allowed = ProductLifecycle.valid_transitions_from(fr)
            if to not in allowed:
                flags.append(
                    f"unsafe lifecycle transition: {fr.value} -> {to.value} "
                    f"(allowed next: {sorted(s.value for s in allowed)})"
                )

    return flags


def validate_action_contract(
    contract: ActionContract,
    *,
    repo_root: Path,
    inventory: ProductInventory | None = None,
) -> list[str]:
    """
    Structural and policy validation. Returns a list of error strings (empty if ok).

    Does not execute the command.
    """
    errors: list[str] = []

    if not contract.action_id.strip():
        errors.append("action_id is required")
    if not contract.product_id.strip():
        errors.append("product_id is required")

    cmd = contract.command.strip()
    if not cmd:
        errors.append("command is required and must be non-empty")

    at = contract.normalized_action_type()
    if not at:
        errors.append("action_type is required")
    elif at not in KNOWN_ACTION_TYPES:
        errors.append(
            f"unknown action_type {contract.action_type!r} "
            f"(known: {', '.join(sorted(KNOWN_ACTION_TYPES))})",
        )

    wd_path, wd_err = resolve_working_directory(repo_root, contract.working_directory)
    if wd_err:
        errors.append(f"working_directory: {wd_err}")

    inv = inventory
    if inv is None:
        inv = build_inventory(repo_root)
    pid = contract.product_id.strip()
    if pid and pid not in inv.valid:
        errors.append(
            f"product {pid!r} not found in inventory "
            f"(expected a valid products/*/product.yaml under {inv.products_dir})",
        )

    # Script/path existence for applicable tokens only
    if wd_path is not None and cmd:
        to_check = script_and_hook_path_tokens(cmd)
        for rel_disp, exists in check_referenced_files(repo_root, wd_path, to_check):
            if not exists:
                errors.append(f"referenced path does not exist: {rel_disp}")

    # Optional lifecycle: invalid enum already flagged in dangerous_patterns;
    # here we only duplicate if transition keys are garbage (dangerous_patterns covers unknown stages)

    return errors
