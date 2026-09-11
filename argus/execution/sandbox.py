"""
Execution sandbox: restrict working directories and block high-risk command patterns.

Complements ``argus.actions.validate.dangerous_patterns`` (still applied via dry-run).
"""

from __future__ import annotations

import re
import tempfile
from pathlib import Path

from argus.actions.models import ActionContract
from argus.actions.validate import resolve_working_directory


def sandbox_working_directory_errors(
    contract: ActionContract,
    *,
    repo_root: Path,
) -> list[str]:
    """
    Allow only:
    - Under the repository: ``products/<product_id>/`` (or a subdirectory of it)
    - Under the repository: ``runs/execution/tmp/`` (scratch)
    - Outside the repository: paths under the system temp dir (``tempfile.gettempdir()``)

    Paths inside the repo are **never** treated as "temp" just because the repo lives under ``/tmp``.
    """
    root = repo_root.resolve()
    pid = contract.product_id.strip()
    if not pid:
        return ["sandbox: product_id is required for working directory policy"]

    wd_path, wd_err = resolve_working_directory(root, contract.working_directory)
    if wd_err:
        return [f"working_directory: {wd_err}"]
    assert wd_path is not None
    wd = wd_path.resolve()
    prod = (root / "products" / pid).resolve()
    scratch = (root / "runs" / "execution" / "tmp").resolve()

    try:
        under_repo = wd.is_relative_to(root)
    except ValueError:
        under_repo = False

    if under_repo:
        if wd == prod or wd.is_relative_to(prod):
            return []
        if wd == scratch or wd.is_relative_to(scratch):
            return []
        return [
            "sandbox: working_directory inside the repository must be under "
            f"products/{pid}/ or runs/execution/tmp/ "
            f"(resolved: {wd})",
        ]

    tmp = Path(tempfile.gettempdir()).resolve()
    if wd == tmp or wd.is_relative_to(tmp):
        return []

    return [
        "sandbox: working_directory outside the repository must be under "
        f"system temp ({tmp}) (resolved: {wd})",
    ]


# Extra patterns beyond ``dangerous_patterns`` (execution-specific).
_RE_RM_RF_DOT = re.compile(r"\brm\b.*\s-(?:rf|fr)\s+\.\./", re.I | re.S)
_RE_SUDO = re.compile(r"(?:^|[;&|])\s*(sudo|doas)\b", re.I)
_RE_PIPE_SHELL = re.compile(r"\|\s*(?:ba)?sh\b", re.I)
_RE_CURL_SH = re.compile(r"\b(?:curl|wget)\b[^|]*\|\s*(?:ba)?sh\b", re.I)
_RE_DD_DISK = re.compile(r"\bdd\s+.*\bof=/dev/", re.I)


def sandbox_command_errors(command: str, *, repo_root: Path) -> list[str]:
    """
    Parse ``command`` and flag execution-specific unsafe patterns.

    Structural issues (empty argv) are left to the runner.
    """
    errors: list[str] = []
    cmd = command.strip()
    if not cmd:
        return errors

    if _RE_RM_RF_DOT.search(cmd):
        errors.append("sandbox: recursive rm toward parent paths (../) is blocked")

    if _RE_SUDO.search(cmd):
        errors.append("sandbox: sudo/doas escalation is blocked for execution")

    if _RE_PIPE_SHELL.search(cmd) or _RE_CURL_SH.search(cmd):
        errors.append("sandbox: piping remote or untrusted content into a shell is blocked")

    if _RE_DD_DISK.search(cmd):
        errors.append("sandbox: dd to raw devices under /dev/ is blocked")

    # Destructive writes outside repo (absolute paths not under repo)
    for m in re.finditer(r"[12]?(?:>>|>)\s*([/~][^\s;&|`'\"]+|/[^\s;&|`'\"]+)", cmd):
        target = m.group(1).strip()
        if target.startswith("~"):
            errors.append(f"sandbox: redirection to home path {target!r} is blocked")
            continue
        p = Path(target)
        if not p.is_absolute():
            continue
        try:
            p.resolve().relative_to(repo_root.resolve())
        except ValueError:
            errors.append(f"sandbox: redirection outside repository is blocked ({target!r})")

    return errors


def validate_execution_sandbox(
    contract: ActionContract,
    *,
    repo_root: Path,
) -> list[str]:
    """All sandbox violations (working directory + command)."""
    out = sandbox_working_directory_errors(contract, repo_root=repo_root)
    out.extend(sandbox_command_errors(contract.command, repo_root=repo_root))
    return out
