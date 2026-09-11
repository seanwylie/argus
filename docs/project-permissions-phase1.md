# Phase 1 project permissions (`argus.policy.yaml`)

Argus adds a **small, project-scoped** policy layer: **what Argus may attempt** for a product. It does **not** replace cloud IAM, GitHub permissions, or shell access — those remain the real enforcement surface. This file is **human-editable**, **versionable**, and read **on each execution** (no in-process cache).

## Location

`products/<product_id>/argus.policy.yaml`

Created by **`argus products create`** (scaffold), **`argus products scaffold-creation`**, and GitHub onboarding (via scaffold).

## Keys (Phase 1)

Each value must be one of: **`yes`**, **`no`**, **`confirm`**.

### Strict YAML (required)

Values must be **quoted strings** in YAML so they load as strings, not YAML 1.1 booleans.

- **Correct:** `mutate_nonprod: "yes"` — Argus reads the string `yes`.
- **Incorrect:** `mutate_nonprod: yes` — PyYAML loads a **boolean** `true`. Argus **rejects** the file with a clear error (no silent coercion).

The same applies to `no` (unquoted → boolean `false`). Use **`"yes"`**, **`"no"`**, and **`"confirm"`** only — not synonyms like `allow` / `deny`.

If `argus.policy.yaml` is **missing**, Argus uses **documented built-in defaults** for all Phase 1 keys and records a **warning** that no explicit file was found (not silent). If the file is **present** but invalid, **no** execution proceeds for that product until fixed.

| Key | Meaning (semantics) |
|-----|---------------------|
| `observe_prod_signals` | Read/observe production-facing signals (metrics, health, cost, etc.) |
| `mutate_nonprod` | Change non-production resources (staging, dev sandboxes, local infra hooks) |
| `mutate_prod` | Change production resources |
| `commit_local` | Create local git commits in the repo |
| `push_remote` | Push branches / tags to remotes |
| `deploy` | Deployment-style actions (CD, kubectl, serverless deploy, etc.) |
| `change_experiments` | Mutate experiment definitions or experiment-linked automation |
| `builder_execute` | Run **`argus builder invoke --execute`** (Cursor or agent backend); policy `no` refuses before any subprocess |

## Semantics

- **`yes`** — Argus **may** attempt the action **if** the environment supports it (see mismatch below).
- **`no`** — Argus **refuses** the action even when the environment could run it.
- **`confirm`** — Argus **does not** auto-run; operators must approve out-of-band or change policy to `yes` after review.

## Runtime

`argus execution run` (and paths that call the same gate) checks the contract’s inferred permission key (or `project_permission_key` on the action file) **before** the subprocess runs.

**Builder:** `argus builder invoke --execute` evaluates **`builder_execute`** via the same Phase 1 gate (`evaluate_phase1_for_keys`) before opening Cursor or running the agent CLI. The invoke record includes **`project_permission_decision`** (`argus.project_permission_decision.v1`). Review-only / dry-run invokes do not evaluate this key.

## Policy vs environment

If policy is **`yes`** or **`confirm`** but Argus’s **declarative** environment hints (containment grants, on-disk signals, git metadata, etc.) say the capability is not available, execution is **refused** with a message that policy allows the action but the **environment** does not yet support it. Fix grants / wiring, or set policy to `no` if you do not want that action attempted.

## Inspect

```bash
argus products permissions show <product_id>
```

Product readiness (`argus portfolio product-readiness …`) includes the same summary under `project_permissions`.

## Revision

Edit `products/<id>/argus.policy.yaml` and re-run; no re-import required.
