"""Verbose human escalation report: GitHub + AWS least-privilege guidance (no live API calls)."""

from __future__ import annotations

import json
from pathlib import Path

from argus.containment.policy import (
    ENV_CONTAINMENT,
    build_capability_map,
    is_containment_enforced,
    load_containment_policy,
    policy_path,
)


def render_escalation_report(repo_root: Path) -> str:
    root = repo_root.resolve()
    pol = load_containment_policy(root)
    cmap = build_capability_map(root)
    enforced = is_containment_enforced(root)
    pp = policy_path(root)

    caps_blob = json.dumps(cmap, indent=2)

    return f"""# Argus credential containment — human escalation report (v0)

This document is **guidance** for configuring **minimum** project access so Argus can run
improvement workflows without inheriting ambient machine credentials in subprocesses when containment is enforced.

## 1. Current containment state

- **Containment enforced now:** `{enforced}` (`{ENV_CONTAINMENT}` env or `enforce: true` in policy file).
- **Policy file path:** `{pp}` (exists: `{pp.is_file()}`).
- **When enforced:** subprocess-spawned commands receive an environment **stripped** of common ambient
  cloud/token variables (see policy defaults). Argus **does not** read `~/.aws`, `~/.ssh`, or global git
  credential helpers — but **child processes** may still resolve SSH/git config via `HOME` unless further
  isolation is added in a future version.

### What is blocked when enforced

- Passing **AWS_\\***, **AZURE_\\***, **GCP_\\***, and common **CI tokens** (GITHUB_TOKEN, GH_TOKEN, …)
  into Argus-driven subprocesses via the parent environment.
- **SSH_AUTH_SOCK** is stripped to reduce accidental agent-based use of ambient keys.

### What remains allowed

- Normal PATH, locale, and repo-local working directories.
- The Argus Python process may still use host configuration — containment v0 focuses on **subprocess**
  hygiene for execution/actions.

### Capability map snapshot (v0)

```json
{caps_blob}
```

Undeclared **git_push**, **aws_\\***, and **deploy** capabilities are **declined** until `grants` appear in
the local policy file and you implement repo-local secret wiring (v0 does **not** auto-load `.env` files).

## 2. GitHub — suggested permission tiers

### Read-only baseline (recommended default)

- **Repo contents: read** (clone, fetch, read metadata) for the Argus monorepo and any product repos under test.
- **No** `contents: write`, **no** `workflow` write, **no** admin.

### Local branch work (typical code improvement)

- Use **branch pushes** on **non-protected** branches; keep **default branch** protected; require PR + review for merge.
- Argus automation should **not** receive a PAT with bypass permissions.

### Push / PR creation

- **v0 default:** treat **git_push** as **declined** in Argus until `grants.git_push: true` is set in
  `config/argus_containment.yaml` **and** you provide tokens through a **repo-local** mechanism you control.
- Prefer **manual PR creation** for high-stakes repos until automation is reviewed.

## 3. AWS — suggested permission tiers

### No production by default

- **aws_read_prod** / **aws_write_prod**: **declined** in v0 — do not grant Argus subprocesses prod credentials in this phase.

### Read-only staging / observation

- Use **read-only** IAM for staging accounts: e.g. `ReadOnlyAccess` scoped by resource ARN where possible.

### Non-prod write (optional, human-gated)

- Grant **aws_write_staging** only after explicit `grants.aws_write_staging: true` and a reviewed IAM role
  limited to staging resources (no `Resource: *`).

### Separation

- **Observation** vs **mutation** vs **deployment** — use **separate** roles or accounts; never reuse prod keys in Argus trial environments.

## 4. Recommended least-privilege structure

| Tier | Purpose | Typical scope |
|------|---------|----------------|
| **R0 read-only** | audits, signals, doctor | repo + staging read |
| **R1 staging write** | improvement experiments | staging-only ARNs |
| **R2 deploy** | release automation | separate pipeline + human approval |
| **Prohibited** | prod mutation from Argus trial | deny by default |

## 5. Repo-local declaration model (v0)

- Policy file: `config/argus_containment.yaml` (copy from `config/argus_containment.example.yaml`).
- Set `enforce: true` **or** export `{ENV_CONTAINMENT}=1` to strip ambient credential env vars in Argus **execution** and **actions** subprocesses.
- Use `grants` booleans to document **intent**; inject secrets through mechanisms **you** define under repo control (e.g. CI OIDC).

## 6. Human decisions required

1. Whether to **enable** containment for trial runs (`enforce` or env flag).
2. Which **GitHub** scopes and branch protections apply to automation vs humans.
3. Which **AWS** account/role is **staging-only** and which ARNs are in scope.
4. Whether **git_push** / **deploy** grants are ever set — and under what review process.
5. How repo-local secrets are delivered **without** falling back to ambient home-dir files (team-specific).

---

- **Policy file loaded:** {bool(pol)}
"""
