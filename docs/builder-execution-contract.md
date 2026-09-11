# Builder execution contract (bounded increments)

**Roadmap:** **Hands (Phase 2)** and **Seal the Hands (Phase 2A)** are **delivered** (2A caveats are **graded** — see roadmap). **Operational Builder (Phase 2B)** is **active / in progress** (visibility, dashboard, history, **`builder_activity`**). **Generalize Hands (Phase 2C)** is **substantially complete with caveats** — minimal **contract registry**, **`bug_fix`** / **`signal_instrumentation`** contracts, **`set-target`** ergonomics, Argus-root **worktree** + **scoped FS** + **Linux Landlock** when applicable; plain **`argus_root`** and incremental contract kinds remain as documented below. **Autonomous Builder** and **portfolio memory / learning** are **out of scope** for 2C. See **[plans/ephemeral-roadmap.md](plans/ephemeral-roadmap.md)** and **[architecture/state-of-the-system.md](architecture/state-of-the-system.md)**.

### Alarm path vs portfolio visibility (non-alarm)

| Path | What it is |
|------|------------|
| **Alarm** | Reconcile-time **escalation rules** → **`builder_escalation_emit`** → packets under **`runs/escalations/latest/`** (and inbox) when signals are serious — operator attention |
| **Non-alarm portfolio** | **`argus portfolio builder-activity`** writes **`runs/portfolio/builder_activity/latest.json`** — deterministic summary of invoke/reconcile truth and pointers to source artifacts. **Not** learning, **not** outcomes scoring, **not** Phase 3 Memory. |

## Purpose

Builder prompts are **temporary law for one increment**: they constrain what the execution backend may do, align with `builder_task.json`, and give **reconcile** enough structure to compare **git changes vs allowed scope**.

**Contract kind registry:** `argus/builder/contract_registry.py` maps each supported `contract_kind` / `target_type` to its **contract builder**, **reconcile outcome** deriver, and small flags (e.g. whether `allowed_paths_exact` is required, whether `--generate-next-expansion` skips heuristics). **`argus builder prepare`** markdown (`builder_next_prompt.md`) is implemented in `next_expansion_prepare.py`, which calls the registry for the embedded `execution_contract`—that separation is **intentional** (static registry, not a plugin framework; moving renderers into the registry would be a larger structural change, not an incomplete feature).

### Escalation (automatic)

After a successful **reconcile record write**, Argus evaluates deterministic Builder escalation rules. When scope, execution outcome, or invoke-time trust signals are serious (e.g. **Argus core path breach**, **non-product-root breach**, **semantic scope breach**, **execution_outcome** `blocked` or `breached`, or **agent trust** degradation such as unsandboxed / missing `no_new_privs` / dirty tree before branch), a packet may be written under **`runs/escalations/latest/`** (same model as `argus escalation generate`). **Deduping:** identical **triggering_rules** sets for the same product within **24 hours** skip a second write (see `builder_escalation_emit` on the reconcile record). Use **`argus builder reconcile --no-escalation`** to disable emission for a run.

### Project permission gate (Phase 1)

**`argus builder invoke --execute`** evaluates the Phase 1 key **`builder_execute`** in `products/<id>/argus.policy.yaml` (default **`yes`** when the key is omitted) **before** any real backend subprocess. Denial or pending confirmation matches other gated execution: invoke **`invocation_status`** is **`failed`**, and the full decision is on the record as **`project_permission_decision`** (`argus.project_permission_decision.v1`). Review-only and dry-run invokes do not run this check. See [project-permissions-phase1.md](project-permissions-phase1.md).

### Host readiness (operator)

Before relying on **trustworthy** agent execution (bubblewrap + Linux `no_new_privs`, etc.), run:

**`argus builder doctor`**

It reports static **PATH / environment** signals only (`argus.builder.host_readiness.v1`): whether `bwrap`, `setpriv`, and `git` are visible, effective sandbox-related env vars, and an overall label **`ok` \| `degraded` \| `not_ready`**. It does **not** prove an invoke will succeed (permissions, product layout, nested git state). Use **`--json`** for automation; **`--strict`** exits with status 1 when the overall status is **`degraded`** (default is exit 1 only for **`not_ready`**).

This is separate from the repo-wide **`argus doctor`** command.

## Artifacts

- **`builder_next_prompt.md`** — Human/agent-facing instructions (hardened markdown).
- **`builder_task.json`** — Machine-readable task; includes **`execution_contract`** when the template supports it (`argus.builder.execution_contract.v1` for **`content_slot`**, **`bug_fix`**, and **`signal_instrumentation`**).

### Manual non-story workflow (`bug_fix` / `signal_instrumentation`)

Use this path when the increment is **not** a story slot: the **declared target** is still the **`primary_target`** object inside **`products/<id>/content/next_expansion.json`** — same file as story work, different **`target_type`**.

**Normal operator sequence:**

| Step | Command | What it does |
|------|---------|--------------|
| 1 | **`argus builder set-target`** | Writes **`content/next_expansion.json`**, replacing **`primary_target`** only. Other top-level keys (e.g. **`explicit_non_targets`**) are preserved. Without **`--prepare`**, does **not** create prompts or **`builder_task.json`**. |
| 1b | **`argus builder set-target … --prepare`** | Same write as step 1, then runs the same step as **`prepare`** below (writes **`builder_next_prompt.md`** + **`builder_task.json`**). Ignored with **`--dry-run`**. If prepare fails after the file write, exit status is non-zero and stderr explains both states. |
| 2 | **`argus builder prepare`** (if you did not use **`--prepare`**) | Reads **`next_expansion.json`**, writes **`builder_next_prompt.md`** + **`builder_task.json`** (default: **`products/<id>/generated/`**). |
| 3 | **`argus builder invoke`** | Resolves artifacts; default is review-only. Add **`--execute --backend agent`** (or **`cursor`**) to run. Use **`--prepare-first`** if the prepared task is stale vs declared **`next_expansion.json`**. |
| 4 | **`argus builder reconcile`** | Diff, scope check, execution outcome, merge readiness. **`--generate-next-expansion`** does not overwrite non-story targets with the story heuristic (by design). |
| 5 | **`argus builder review`** / **`argus builder merge`** | Local merge-readiness label and optional git merge when **`merge_candidate`** (same rules as story work; workspace / containment caveats apply — see **Workspace kind** and **Merge readiness** below). |

**Terminology:** **`primary_target`** = declared target in **`next_expansion.json`**. After **prepare**, **`builder_task.json`** holds **prepared** **`resolved_target`** + **`execution_contract`**. Invoke’s stale-contract check compares declared vs prepared.

**`bug_fix` example:**

```bash
# Optional: combine set-target + prepare in one command (--prepare)
argus builder set-target myproduct --kind bug_fix --id fix-001 \
  --allow-path app/foo.py --bug-statement "Wrong default in foo" --prepare

# Or: set-target then prepare separately
# argus builder set-target myproduct --kind bug_fix --id fix-001 \
#   --allow-path app/foo.py --bug-statement "Wrong default in foo"
# argus builder prepare myproduct

argus builder invoke myproduct --execute --backend agent
argus builder reconcile myproduct
argus builder review myproduct
# argus builder merge myproduct   # when review_status is merge_candidate
```

**`signal_instrumentation` example:**

```bash
argus builder set-target myproduct --kind signal_instrumentation --id sig-001 \
  --allow-path app/telemetry.py --signal-statement "Emit counter for X" \
  --touch-path "app/**/*.py" --prepare
argus builder invoke myproduct --execute --backend agent
argus builder reconcile myproduct
argus builder review myproduct
```

**`set-target` flags (summary):** **`--kind`**, **`--id`**, repeatable **`--allow-path`**; **`--bug-statement`** or **`--signal-statement`** as required; optional **`--prepare`** (run **`argus builder prepare`** after write), **`--prepare-output`** (`product` \| `runs`, default `product`); optional **`--pattern`**, **`--success-condition`**, **`--stop-condition`**; for **`signal_instrumentation`**: **`--expect-path`**, **`--touch-path`**. **`--dry-run`** validates without writing; **`--json`** prints the full **`next_expansion`** document to stdout after validation (prepare paths, when **`--prepare`** is used, go to stderr only).

## Agent containment (outer sandbox)

**Applies to:** `--execute --backend agent` only. The **cursor** backend opens a file in the IDE and does not use this layer (`containment_applied: not_applicable`).

**Goal:** Run the headless agent CLI inside an **outer envelope** (bubblewrap / `bwrap` on Linux) with a **stripped environment**, so the process does not inherit the full ambient shell authority (cloud tokens, git credentials in env, SSH agent socket paths, etc.). This is **not** a full VM or perfect isolation: it reduces blast radius; operators should still treat agent output as untrusted.

### Default behavior

- **`--sandbox auto`** (default): use bubblewrap when `bwrap` is on `PATH`. If bubblewrap is **missing**, execution is **refused** unless **`--allow-unsandboxed`** or **`ARGUS_BUILDER_ALLOW_UNSANDBOXED=1`** is set (explicit degraded trust).
- **`--sandbox bwrap`**: require bubblewrap; refuse if unavailable (same opt-out as above).
- **`--sandbox none`**: run the agent **without** bubblewrap (`containment_applied: none`, `trust_degraded_unsandboxed: true`).

Environment overrides: **`ARGUS_BUILDER_AGENT_SANDBOX`** (`auto` \| `bwrap` \| `none`).

### Network policy (explicit modes)

Network is **not** hidden: every agent invoke records **`network_mode`** on **`builder_containment`**. By default the agent runs with **host-equivalent network** inside bubblewrap (no extra isolation flags) — this is intentional so headless agents can reach local services, package indexes, or model APIs when configured.

| Mode | Behavior when sandboxed (`containment_applied: bwrap`) | Trust |
|------|--------------------------------------------------------|--------|
| **`default`** (recommended default) | Unrestricted vs host network (no `bwrap --unshare-net`) | **Not** degraded — expected for typical agent work |
| **`allow_all`** | Same as `default` (full access) | **`trust_degraded_network_open: true`** — explicitly wide-open |
| **`disabled`** | Adds **`bwrap --unshare-net`** (isolated network namespace; effectively offline for most workloads) | Not degraded by network alone; may **break** agents that need outbound access |

CLI: **`argus builder invoke --execute --backend agent --network MODE`**. Environment: **`ARGUS_BUILDER_NETWORK_MODE`** (`default` \| `allow_all` \| `disabled`). CLI wins over the environment when set.

Unsandboxed runs (`--sandbox none`, bubblewrap missing with `--allow-unsandboxed`, etc.) cannot enforce network policy in the outer envelope: **`network_applied: false`** with an explanatory **`network_reason`**.

**Merge readiness:** network mode does **not** block **`merge_candidate`** by itself. Domain allowlists and finer-grained policy are **not** implemented in this pass.

**Future:** optional allowlisted egress or stricter defaults could be added without changing the schema shape (`network_mode` + applied/reason flags).

### `no_new_privs` (Linux launcher seatbelt)

On **Linux**, when the agent subprocess is actually launched, Argus **requests** `PR_SET_NO_NEW_PRIVS` by running the outer command as:

`setpriv --no-new-privs -- <argv…>`

…wrapping the full argv (including `bwrap …` when bubblewrap is used). That flag is inherited by child processes and blocks **gaining privileges through exec** (e.g. setuid binaries, file capabilities). It does **not** turn off the network, block DNS, or replace bubblewrap; it is an extra, inspectable layer.

- **Detection:** `setpriv` must be on `PATH` (typically from **util-linux**). If it is missing on Linux, execution still proceeds when otherwise allowed, but **`no_new_privs_applied`** is false, **`trust_degraded_missing_no_new_privs`** is true, and merge readiness reflects that (see below).
- **Opt-out (not recommended):** **`ARGUS_BUILDER_SKIP_NO_NEW_PRIVS=1`** skips requesting `no_new_privs` (recorded in **`no_new_privs_reason`**).
- **Non-Linux:** not requested (`no_new_privs_reason: non_linux_skip`).

### Landlock (Linux, write-focused, defense in depth)

On **Linux**, when bubblewrap is used and filesystem scope is **`product_scoped`** or **`argus_root_worktree_scoped`**, Builder may wrap the **inner** agent argv with `python -m argus.builder.landlock_launcher -- …` so the agent process applies a **Landlock** ruleset (write-like ops only) to an allowlist derived from containment (scratch `HOME`, `/tmp`, product or worktree + `.git` paths as needed). **`legacy_repo_rw`** **does not** use this wrapper (same blast radius as full-repo mounts; recorded as **`landlock_skipped_reason: legacy_repo_rw_scope_bypasses_landlock`**).

- **Disable:** **`ARGUS_BUILDER_LANDLOCK=0`** (or `false` / `no` / `off`).
- **Honesty:** After execute, invoke merges a small JSON status file from the child into **`builder_containment`**: **`landlock_requested`**, **`landlock_applied`**, **`landlock_reason`**, **`trust_degraded_missing_landlock`** when Landlock was expected but not effectively applied. Non-Linux runs set **`landlock_skipped_reason: non_linux_skip`** and do not claim Landlock.

### Artifact (`builder_containment`, `argus.builder.containment.v1`)

Includes: `containment_requested`, `containment_applied` (`bwrap` \| `none` \| `not_applicable` \| `unavailable`), `containment_fallback_used`, `containment_reason`, `trust_degraded_unsandboxed`, **`trust_degraded_workspace_scope`** (true when **`git_baseline.git_workspace_kind`** is **`argus_root`** — filesystem scope is not product-scoped), `sanitized_env_stripped_count`, `sanitized_env_stripped_keys_sample` (names only, not values), `bwrap_path`, and **`no_new_privs_requested`**, **`no_new_privs_applied`**, **`no_new_privs_launcher`** (e.g. `setpriv`), **`setpriv_path`**, **`no_new_privs_reason`**, **`trust_degraded_missing_no_new_privs`**, Landlock fields (**`landlock_requested`**, **`landlock_applied`**, **`landlock_reason`**, **`trust_degraded_missing_landlock`**, **`landlock_skipped_reason`**, **`landlock_allowed_write_paths_summary`**), plus **`network_mode`** (`default` \| `allow_all` \| `disabled`), **`network_applied`** (whether the outer envelope applied the policy — typically true under bwrap, false when unsandboxed), **`network_reason`** (when not applied or when `disabled` used `unshare-net`), and **`trust_degraded_network_open`** (true only for explicit **`allow_all`** on executed agent runs).

### Filesystem notes (bwrap)

Bubblewrap mounts depend on **`filesystem_scope_mode`** on **`builder_containment`** (see **`repo_root_mount_mode`** / **`product_mount_mode`**):

- **`product_scoped`** — Argus repo root is mounted **read-only** with **read-write overlays** on the managed product subtree, scratch `HOME`, optional prepare paths, and (when git cwd is the Argus root) `.git` as a directory bind. This is the **strong containment** path for nested product git.
- **`argus_root_worktree_scoped`** — Used when **`git_workspace_kind: argus_root_worktree`** (agent execute in a **`git worktree`** under `runs/builder/worktrees/<id>/`). The **main** Argus checkout is mounted **read-only**; read-write overlays cover the **worktree directory**, scratch `HOME`, and the **main** repository **`.git/`** directory (git metadata for linked worktrees). **Not** full parity with **`product_scoped`**: the worktree is a **full** checkout, so writable paths mirror the whole tree **inside** the worktree, plus `.git` on the primary repo for git operations. Narrower than **`legacy_repo_rw`** on the primary checkout. Recorded fields include **`worktree_rw_path`** and **`git_dir_mount_mode`** when applicable.
- **`legacy_repo_rw`** — Entire repo root mounted **read-write** inside bwrap. Used for explicit artifact paths outside the product directory, **`git_workspace_kind: argus_root`** (no worktree), and **honest fallbacks** when **`argus_root_worktree_scoped`** cannot be constructed (e.g. missing worktree path).

A minimal fake **`HOME`** under `runs/builder/.sandbox/home/<product_id>/` is used. Standard system trees (`/usr`, `/lib`, `/bin`, …) are mounted read-only where present; `/etc/resolv.conf` (and a few nss files) may be mounted read-only so DNS resolution can work when network is not disabled. **Agent binaries** outside those trees get their **parent directory** bind-mounted read-only so dynamic linking can resolve.

### Workspace kind: nested product vs Argus root (`git_baseline.git_workspace_kind`)

Builder is **primarily product-scoped**: the intended layout is **`nested_product`** — a git repo under `products/<id>/.git` so branch isolation (on execute) and **`product_scoped`** filesystem mounts apply.

**`argus_root`** means there is **no** nested product `.git`; git uses the **Argus monorepo root**. Execution remains **allowed**, but trust is **degraded**:

- Branch isolation is **skipped** (`skipped_not_nested_product_repo`).
- Agent filesystem scope is **`legacy_repo_rw`**, not **`product_scoped`**; **`trust_degraded_workspace_scope`** is **`true`** on **`builder_containment`**.
- **`merge_candidate`** is **not** granted for **agent** runs; merge readiness is **`review_required`** with **`builder_workspace:argus_root_not_isolated`**.

**`argus_root_worktree`** — Branch isolation via **`git worktree add`** (Phase 2C). **`trust_degraded_workspace_scope`** stays **false** (worktree is isolated from the primary checkout for git purposes). Filesystem containment prefers **`argus_root_worktree_scoped`** (see above). If the sandbox falls back to **`legacy_repo_rw`**, merge readiness is **`review_required`** (`builder_containment:argus_root_worktree_legacy_repo_rw_fallback`), not **`merge_candidate`**.

**`argus builder status`** surfaces **`builder_workspace_kind`**, **`trust_degraded_workspace_scope`**, and related merge-readiness fields.

### Merge readiness

For **`merge_candidate`**, an **agent** execute requires **`containment_applied: bwrap`**. When **`no_new_privs_requested`** is true (default on Linux when not skipped), **`no_new_privs_applied`** must also be true — otherwise the label is **`review_required`** (`builder_containment:no_new_privs_not_applied`). For **`filesystem_scope_mode`** **`product_scoped`** or **`argus_root_worktree_scoped`**, if **`landlock_requested`** is true (Linux default-on Landlock path), **`landlock_applied`** must also be true — otherwise **`review_required`** (`builder_containment:landlock_requested_but_not_applied`). **`legacy_repo_rw`** does not request Landlock, so this gate does not apply there. Unsandboxed or sandbox-fallback runs downgrade to **`review_required`** as before (see `builder_branch_review`). Plain **`argus_root`** agent workspaces (no nested product git, no worktree) never reach **`merge_candidate`**. **`argus_root_worktree`** agent runs can reach **`merge_candidate`** only when **`filesystem_scope_mode`** is **`argus_root_worktree_scoped`** (not **`legacy_repo_rw`** fallback). See **Branch merge readiness** below.

## Git baseline and diff summary

- **Nested product git** — `argus builder invoke` / `reconcile` call `ensure_product_git_workspace` so `products/<id>/` gets a local `.git` when missing (reuses `init_argus_product_git` from `argus/products/git_lifecycle.py`). This does **not** modify the Argus repository root; it only ensures a git-backed workspace under the product directory for trustworthy per-product diffs.

### Branch isolation (`--execute`, nested product git only)

On **execute**, when the workspace is **nested** (`products/<id>/.git`), Builder creates a dedicated branch `builder/<increment-or-product-slug>-<10-hex>` (`argus.builder.git_branch_isolation.v1` on the invoke record) and runs the backend with `HEAD` on that branch. There is **no** merge, push, remote, or branch cleanup.

- **Artifact fields:** `git_branch_before`, `git_builder_branch`, `git_branch_created`, `branch_isolation_status` (`ok` | `degraded_dirty_tree` | `failed` | `skipped_*`), `trust_degraded_dirty_tree`, `branch_isolation_error` (when failed or skipped with detail).
- **Dirty working tree:** If the product repo had **uncommitted changes before** `git checkout -b`, execution still proceeds (same baseline honesty model as today), but **`branch_isolation_status`** is **`degraded_dirty_tree`** and **`trust_degraded_dirty_tree`** is true. Argus does **not** stash or auto-commit.
- **Failure:** If creating/switching the branch fails, invoke records **`failed`** and the Cursor/agent subprocess is **not** run.
- **Reconcile:** `git_branch_context` records **`git_branch_at_reconcile`** (and optional `read_error`) for audit; diff and scope logic still use the nested `git_cwd` and invoke baseline as before.
- **Invoke record** — `git_baseline` (`argus.builder.git_baseline.v1`): `baseline_commit` (HEAD before work), `working_tree_dirty_before`, `git_workspace_kind` (`nested_product` vs `argus_root`), `git_cwd`, `argus_path_prefix`. After `--execute`, **`post_invoke_git_diff`** holds a bounded `git diff` vs baseline (`truncated_diff`, `diff_stat`, `changed_files_argus_relative`).
- **Reconcile record** — **`builder_diff_summary`** (`argus.builder.git_diff_summary.v1`): prefers **invoke baseline diff** when `baseline_commit` is present; otherwise falls back to the Argus repo working tree. The **changed file list** is merged with the Argus repo’s `git diff` listing so **`argus/`** edits (agent cwd = repo root) are never invisible to scope when using a nested product repo. **`truncated_diff`** is not the full patch; **`limitations`** spell out dirty-tree and merge behavior.
- **Path scope** — When `builder_diff_summary` has a `source`, reconcile passes **`changed_files_argus_relative`** into path scope as **`path_scope_source: builder_diff_summary`** (canonical list for allow-list / argus breach checks).

## Story slot contract

Implemented in `argus/builder/execution_contract.py`:

- **Allowed paths** — Exact list + glob patterns (neighbor `app/site/slot/*.html`, same-group `content/slots/group_XX_slot_*.json`).
- **`next_expansion_policy: do_not_change_primary_target`** — One increment per run; declared target advancement is owned by **`argus builder reconcile --generate-next-expansion`**, not by editing `primary_target` in-session.
- **Forbidden** — Argus core, other products, drive-by refactors, fake metrics, etc.

### Product-scoped execution must not modify `argus/`

Normal Builder work is **product-scoped**: changes must stay under `products/<product_id>/` and within the contract allow-list. Reconcile **path scope** treats any git change under **`argus/`** as a **hard breach** with reason prefix **`path_scope:modified_argus_core`** (distinct from other out-of-tree changes, which use **`path_scope:modified_non_product_root`** — e.g. `config/`, `docs/`, repo root, other products).

Argus-core or tooling changes should be modeled as a **separate explicit task** (future: dedicated Argus-scoped Builder mode); this pass does not add a full self-edit framework.

## Bug fix contract (`bug_fix`)

Implemented in `argus/builder/execution_contract.py` as **`build_bug_fix_execution_contract`**, with prompts from **`next_expansion_prepare`** (`target_type: bug_fix` in **`content/next_expansion.json`**).

- **`contract_kind: bug_fix`** — `increment_target_id` / `bug_id`, **`allowed_paths_exact`** (required, non-empty), optional **`allowed_path_patterns`**, **`bug_statement`**, success/stop summaries, **`forbidden_absolute`**, and **`next_expansion_policy: do_not_change_primary_target`** (same semantic check as story slots when `next_expansion.json` is present).
- **Path scope** — Reconcile uses **`builder_diff_summary`** vs the contract allow-list (same path-scope machinery as content_slot).
- **Outcome** — `argus/builder/bug_fix_outcome.py`: conservative **`completed` / `partial` / `blocked` / `breached` / `unknown`** from invoke + scope + diff signals; **no** claim of runtime correctness.
- **`--generate-next-expansion`** — Skipped when the on-disk target is **`bug_fix`** (non-story targets are not overwritten by the story-slot heuristic generator).

## Signal instrumentation contract (`signal_instrumentation`)

Implemented in `argus/builder/execution_contract.py` as **`build_signal_instrumentation_execution_contract`**, with prompts from **`next_expansion_prepare`** (`target_type: signal_instrumentation` in **`content/next_expansion.json`**).

- **`contract_kind: signal_instrumentation`** — `increment_target_id` / `signal_id`, **`allowed_paths_exact`** (required, non-empty), optional **`allowed_path_patterns`**, **`signal_statement`** (or `instrumentation_objective` / `rationale`), optional **`expected_product_paths_exist`** (product-relative paths reconcile checks for existence), optional **`instrumentation_touch_paths`** (fnmatch hints — at least one git-touched product path must match each hint when hints are non-empty), success/stop summaries, **`forbidden_absolute`**, **`next_expansion_policy: do_not_change_primary_target`**.
- **Path scope** — Same **`builder_diff_summary`** + allow-list machinery as other contracts.
- **Outcome** — `argus/builder/signal_instrumentation_outcome.py`: invoke + scope + diff + optional **postexistence** + **touch overlap**; **no** claim that telemetry is semantically correct or firing at runtime.
- **`--generate-next-expansion`** — Skipped when the on-disk target is **`signal_instrumentation`** (same: non-story target preserved).

## Reconcile scope check

`run_builder_reconcile` adds **`builder_scope_check`** to the reconcile record.

### `argus.builder_scope_check.v2` (current)

Nested structure:

- **`path_scope`** — same information as v1: git working tree vs contract allow-list (`argus.builder_scope_check.v1` payload under this key). Includes **`argus_core_breach`** / **`modified_argus_paths`** vs **`non_product_root_breach`** / **`modified_non_product_paths`** so Argus self-modification is visible separately from other out-of-tree edits.
- **`semantic_scope`** — when the contract policy is **`next_expansion_policy: do_not_change_primary_target`**, compares **`builder_task.json` `resolved_target`** to **`primary_target`** in on-disk **`content/next_expansion.json`** (before optional `--generate-next-expansion`). This catches **in-path-but-wrong-field** edits that path allow-lists alone cannot see.
- **`path_scope_breach`** / **`semantic_scope_breach`** — separate flags.
- **`scope_breach`** — true if either path or semantic check failed.
- **`breach_reasons`** — combined machine-readable strings from both layers.

If **`scope_breach`** and **`--prepare-next`** would run, **`prepare_next` is skipped** with reason **`scope_breach_blocks_prepare_next`** (path or semantic).

Older records may still use **`argus.builder_scope_check.v1`** at the top level (path-only).

### Builder status

`argus builder status` exposes the latest reconcile scope summary via **`latest_reconcile`**: `builder_scope_check_schema`, `scope_check_status`, `scope_breach`, `path_scope_breach`, `semantic_scope_breach`, `scope_breach_reasons`, **`path_scope_argus_core_breach`**, and **`path_scope_non_product_root_breach`** (from nested `path_scope` on v2 records, or v1 path payload). **`latest_invoke`** includes **`branch_isolation_status`**, **`git_builder_branch`**, **`trust_degraded_dirty_tree`**, **`builder_workspace_kind`** (from **`git_baseline.git_workspace_kind`**), **`trust_degraded_workspace_scope`**, and related fields; **`latest_reconcile`** includes **`git_branch_at_reconcile`** from **`git_branch_context`**, plus **`review_git_workspace_kind`** / **`review_trust_degraded_workspace_scope`** from **`builder_branch_review`** when present.

### Branch merge readiness (local review only)

Reconcile persists **`builder_branch_review`** (`argus.builder.branch_review.v1`): a **conservative** label for whether a nested Builder branch is reasonable to **inspect** or **manually merge** locally. There is **no** auto-merge, push, or PR.

| `review_status` | Meaning (short) |
|-----------------|-----------------|
| **`merge_candidate`** | Isolated branch `ok`, no degraded dirty-tree flag, scope/outcome signals allow **completed**, invoke succeeded, diff not using baseline fallback. |
| **`review_required`** | Not unsafe/blocked, but trust or outcome is ambiguous (e.g. **Argus-root workspace** agent run, dirty tree before branch, `unknown` outcome, diff fallback). |
| **`blocked`** | No merge-path trust (e.g. invoke not `execute`, branch isolation failed or skipped for reasons other than Argus-root nested-git absence, **blocked**/**partial** execution outcome, or missing invoke). |
| **`unsafe`** | Scope/path/semantic/Argus-core/non-product signals or execution outcome **breached** — do not merge without fixing scope. |

**CLI:** `argus builder review <PRODUCT_ID>` prints the same fields; `argus builder status` shows merge readiness at the top of the Latest reconcile section. When status is **`merge_candidate`**, review output includes a **`suggested_next`** line pointing at `argus builder merge`.

### Manual local merge (operator recipe)

This applies to **nested product git** only (`products/<PRODUCT_ID>/.git`). The Argus repository root is not the merge target for Builder isolation branches.

**1. Refresh and record truth**

- Run `argus builder reconcile <PRODUCT_ID>` so **`builder_diff_summary`**, **`builder_scope_check`**, **`execution_outcome`**, and **`builder_branch_review`** reflect the tree you are about to merge.

**2. Confirm merge readiness**

- Run `argus builder review <PRODUCT_ID>` (or `argus builder status <PRODUCT_ID>`).
- Proceed only if **`review_status`** is **`merge_candidate`**. If it is **`unsafe`**, **`blocked`**, or **`review_required`**, stop: fix scope, re-run invoke/reconcile, or accept manual review without using the merge helper.

**3. Tool-assisted local merge (optional)**

- `argus builder merge <PRODUCT_ID> --into <INTEGRATION_BRANCH>` runs **`git checkout`** then **`git merge --no-edit`** in the **product** git repo (not the Argus root). It **refuses** unless **`review_status == merge_candidate`** at merge time.
- Default integration branch: **`main`** if it exists locally, else **`master`**. Override with **`--into`**.
- **`--dry-run`**: checks only (no `git merge`); still requires **`merge_candidate`**.
- **`--no-record`**: skip writing **`runs/builder/merge/<PRODUCT_ID>/latest.json`**.
- **Does not**: push, open PRs, delete **`builder/...`** branches, or merge from non-**merge_candidate** states.

**4. Fully manual merge (same outcome as the helper)**

```bash
cd products/<PRODUCT_ID>
git status                    # working tree must be clean
git checkout main             # or your integration branch
git merge --no-edit <BUILDER_BRANCH>
```

Use the **`builder_branch`** / **`git_builder_branch`** value from **`argus builder review`** or the invoke record.

**5. Verify**

- Inspect **`git log`**, run your usual tests, and spot-check files allowed by the execution contract.

**6. Cleanup (optional, always manual)**

- When satisfied, you may delete the local **`builder/...`** branch (`git branch -d …`). Argus never deletes it automatically.

**Merge audit artifact:** `runs/builder/merge/<PRODUCT_ID>/latest.json` (`argus.builder.merge_record.v1`) records **`merge_status`**, **`target_branch`**, **`builder_branch`**, refusal or git error text, and timestamps.

## Execution outcome (`content_slot`, `bug_fix`, `signal_instrumentation`)

Reconcile adds **`execution_outcome`** (`argus.builder.execution_outcome.v1`). Dispatch uses **`execution_contract.contract_kind`** or **`resolved_target.target_type`**.

### `content_slot`

| Outcome | Meaning |
|--------|---------|
| **breached** | Path or semantic scope failed (same signal as `builder_scope_check.scope_breach`). |
| **blocked** | Latest invoke record shows failed execution (`invocation_status: failed`). |
| **completed** | Scope OK, invoke reports success, and minimal **filesystem evidence** exists: valid non-trivial `content/slots/<id>.json` and non-trivial `app/site/slot/<id>.html` under the product. |
| **partial** | Scope OK, but evidence is insufficient — e.g. successful invoke without both artifacts, or artifacts present without a successful invoke (unverified). |
| **unknown** | Cannot classify confidently (e.g. no invoke record and weak/absent files). |

### `bug_fix`

| Outcome | Meaning (conservative) |
|--------|------------------------|
| **breached** | Path or semantic scope failed. |
| **blocked** | Invoke failed or missing when needed for classification. |
| **completed** | Scope OK, invoke succeeded, and git diff shows at least one changed file under allowed scope. |
| **partial** | Scope OK but weak evidence (e.g. success with no detected changes, or ambiguous signals). |
| **unknown** | Insufficient signals to choose a stronger label. |

### `signal_instrumentation`

| Outcome | Meaning (conservative) |
|--------|------------------------|
| **breached** | Path or semantic scope failed. |
| **blocked** | Invoke failed. |
| **completed** | Scope OK, invoke succeeded, diff shows changes, declared **postcondition paths** exist on disk (if any were declared), and **touch hints** are satisfied (if any were declared). |
| **partial** | Scope OK but weak evidence (e.g. invoke ok with no diff, missing postcondition files, or touch hint mismatch). |
| **unknown** | Insufficient signals (e.g. no invoke and no diff). |

**Advancement / `prepare-next`:** blocked when outcome is **breached**, **blocked**, or **partial** (not when **unknown**). Scope breach is still checked first.

This is **not** a quality review: for **content_slot**, checks are existence/size/JSON shape only; for **bug_fix**, there is **no** runtime verification; for **signal_instrumentation**, existence checks are **static** (paths on disk) and do **not** prove signals are emitted correctly. Agent stdout is not parsed for “done” claims in this pass.

## Limits (honest gaps)

- Semantic enforcement is **limited to** `primary_target` identity under **`do_not_change_primary_target`** (other fields in `next_expansion.json` are not diffed in this pass).
- Contracts are implemented for **`content_slot`**, **`bug_fix`**, and **`signal_instrumentation`**; other `target_type` values need future templates.
- Empty git diff means “no changes detected” — not “task succeeded.”
- If git is unavailable, **path** scope cannot be evaluated; **semantic** checks still run when the task carries an execution contract.
