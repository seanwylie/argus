"""
Credential containment v0: optional policy file + env flag; deny ambient credential env by default when enforced.

Does **not** rewrite HOME, SSH config paths, or git global config — subprocess children may still resolve
``~/.ssh`` if invoked. v0 targets **environment-variable** ambient secrets (AWS_*, tokens).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

CONTAINMENT_POLICY_SCHEMA = "argus.credential_containment_policy.v0"
CAPABILITY_MAP_SCHEMA = "argus.containment_capability_map.v0"

ENV_CONTAINMENT = "ARGUS_CREDENTIAL_CONTAINMENT"
DEFAULT_POLICY_REL = Path("config/argus_containment.yaml")

# Stripped when containment is enforced (unless policy overrides lists).
_DEFAULT_STRIP_PREFIXES = ("AWS_", "AZURE_", "GCP_", "ALICLOUD_")
_DEFAULT_STRIP_EXACT = frozenset(
    {
        "GITHUB_TOKEN",
        "GH_TOKEN",
        "GITLAB_TOKEN",
        "NETLIFY_AUTH_TOKEN",
        "VERCEL_TOKEN",
        "SSH_AUTH_SOCK",  # agent forwarding to ambient keys
    }
)


class ContainmentError(RuntimeError):
    """A protected action was refused because a capability was missing or declined."""


def policy_path(repo_root: Path) -> Path:
    return repo_root.resolve() / DEFAULT_POLICY_REL


def load_containment_policy(repo_root: Path) -> dict[str, Any] | None:
    p = policy_path(repo_root)
    if not p.is_file():
        return None
    try:
        raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError):
        return None
    return raw if isinstance(raw, dict) else None


def is_containment_enforced(repo_root: Path) -> bool:
    v = (os.environ.get(ENV_CONTAINMENT) or "").strip().lower()
    if v in ("1", "true", "yes", "on"):
        return True
    pol = load_containment_policy(repo_root)
    if pol and pol.get("enforce") is True:
        return True
    return False


def _strip_lists(pol: dict[str, Any] | None) -> tuple[tuple[str, ...], frozenset[str]]:
    if not pol:
        return _DEFAULT_STRIP_PREFIXES, _DEFAULT_STRIP_EXACT
    prefs = pol.get("strip_env_prefixes")
    exact = pol.get("strip_env_exact")
    p_out = tuple(str(x) for x in prefs) if isinstance(prefs, list) else _DEFAULT_STRIP_PREFIXES
    e_set: set[str] = set(_DEFAULT_STRIP_EXACT)
    if isinstance(exact, list):
        e_set = {str(x) for x in exact}
    return p_out, frozenset(e_set)


def sanitized_subprocess_environment(
    base: dict[str, str],
    *,
    policy: dict[str, Any] | None = None,
) -> dict[str, str]:
    """Return a copy of ``base`` with ambient credential-like keys removed."""
    prefs, exact = _strip_lists(policy)
    out: dict[str, str] = {}
    for k, v in base.items():
        if k in exact:
            continue
        if any(k.startswith(pref) for pref in prefs):
            continue
        out[k] = v
    return out


def subprocess_env_for_repo(repo_root: Path) -> dict[str, str]:
    """Environment for ``subprocess`` when running repo-scoped commands."""
    base = dict(os.environ)
    if not is_containment_enforced(repo_root):
        return base
    pol = load_containment_policy(repo_root)
    return sanitized_subprocess_environment(base, policy=pol)


def build_capability_map(repo_root: Path) -> dict[str, Any]:
    """Inspectable capability surface — declarative; does not probe cloud APIs."""
    root = repo_root.resolve()
    pol = load_containment_policy(repo_root) or {}
    grants = pol.get("grants") if isinstance(pol.get("grants"), dict) else {}

    containment_on = is_containment_enforced(root)

    def _avail(ok: bool, reason: str) -> dict[str, Any]:
        return {"status": "available" if ok else "missing_prerequisite", "reason": reason}

    def _declined(reason: str) -> dict[str, Any]:
        return {"status": "declined", "reason": reason}

    caps: dict[str, Any] = {
        "local_build": _avail((root / "pyproject.toml").is_file(), "pyproject.toml in repo"),
        "tests": _avail((root / "tests").is_dir(), "tests/ directory"),
        "git_read": _avail((root / ".git").exists(), "git metadata present"),
    }
    caps["git_push"] = (
        _avail(True, "grants.git_push in local policy")
        if grants.get("git_push") is True
        else _declined("requires `grants.git_push: true` in local policy (human grant)")
    )
    caps["aws_read_staging"] = (
        _avail(True, "grants.aws_read_staging — still requires repo-local cred wiring")
        if grants.get("aws_read_staging") is True
        else _declined("requires `grants.aws_read_staging: true`; v0 does not load ~/.aws")
    )
    caps["aws_write_staging"] = (
        _avail(True, "grants.aws_write_staging — human-reviewed IAM only")
        if grants.get("aws_write_staging") is True
        else _declined("staging write not granted; prohibited by default")
    )
    caps["aws_read_prod"] = _declined("v0: prod read not grantable")
    caps["aws_write_prod"] = _declined("v0: prod write always declined")
    caps["deploy"] = (
        _avail(True, "grants.deploy in local policy")
        if grants.get("deploy") is True
        else _declined("requires `grants.deploy: true` (human grant)")
    )

    return {
        "schema": CAPABILITY_MAP_SCHEMA,
        "containment_enforced": containment_on,
        "policy_path_relative": DEFAULT_POLICY_REL.as_posix(),
        "policy_present": policy_path(root).is_file(),
        "capabilities": caps,
    }


def refuse_unless_capability(repo_root: Path, capability_id: str) -> None:
    """
    Refuse a protected action unless the capability is **available** (not missing, not declined).

    Call from code paths that perform git push, AWS mutation, deploy, etc.
    """
    m = build_capability_map(repo_root)
    caps = m.get("capabilities") if isinstance(m.get("capabilities"), dict) else {}
    row = caps.get(capability_id)
    if not isinstance(row, dict):
        raise ContainmentError(
            f"Unknown capability {capability_id!r}. See `argus containment escalation-report`."
        )
    st = str(row.get("status") or "")
    if st == "available":
        return
    report_hint = "Run: argus containment escalation-report"
    raise ContainmentError(
        f"Capability {capability_id!r} is not available (status={st!r}: {row.get('reason')}). {report_hint}"
    )
