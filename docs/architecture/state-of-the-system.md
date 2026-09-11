# State of the System — Architecture and Autonomy Review

**April 2026 — Builder containment and security posture update**

*Supersedes the April 16 initial Builder boundary review. This document reflects the codebase after Builder gained bubblewrap sandboxing, **product-scoped** and **Argus-root worktree–scoped** filesystem mounts (when applicable), optional **Linux Landlock** write restrictions inside bwrap, **Phase 1 permission gating** on invoke, **post-reconcile escalation emission**, **explicit network modes**, branch isolation (nested product and/or **git worktree** for monorepo), scope verification, and a merge readiness model.*

---

## 1. Executive Summary

Argus is a ~95k-line Python system that manages a portfolio of software products through a deterministic, file-based substrate. It collects signals, produces findings, makes decisions, manages lifecycle, and runs bounded autonomous sessions — all grounded in durable JSON artifacts under `runs/`.

**What changed since the last review:** Builder (`argus/builder/`) has a **layered containment and policy layer**. The agent subprocess runs inside a **bubblewrap (`bwrap`) sandbox** by default — PID-namespaced, with a stripped environment, a fake HOME, read-only system mounts, and a refuse-without-sandbox default. For **nested product git** workspaces, the repo root is typically mounted **read-only** with **read-write overlays** on the managed product subtree (see `filesystem_scope_mode` / `builder_containment`). For **`git_workspace_kind: argus_root_worktree`** with **`argus_root_worktree_scoped`**, the **main** checkout is **read-only** and a dedicated **git worktree** (plus main **`.git`**) is **read-write**; on **Linux**, an optional **Landlock** layer further restricts writes to an allowlist. Builder has **branch isolation** for nested product git (`builder/…` branches) and **worktree-based isolation** for the Argus monorepo when that path is enabled, plus **two-layer scope verification** (path + semantic) in reconcile, **execution outcome classification**, a **conservative merge readiness model**, **`builder_execute` permission evaluation** before execute, **deterministic escalation** after reconcile for serious signals, and **recorded network policy** (`network_mode`, optional `--unshare-net`).

**Current reality:**
- Builder is **CLI-only, opt-in** (`--execute` flag required). It is **not** wired into the autonomous runner loop.
- The stale-contract guard blocks execution when the prepared task diverges from the declared `next_expansion.json` target.
- **Bubblewrap sandbox is default-on** (`--sandbox auto`). Execution is refused without `bwrap` on PATH unless the operator explicitly accepts degraded trust (`--allow-unsandboxed`). Environment is aggressively stripped: cloud, git, SSH, LLM API, and container credentials removed; only PATH/HOME/LANG/TERM/TZ kept.
- **`setpriv --no-new-privs`** wraps the outer argv on Linux, blocking privilege escalation through exec.
- **Branch isolation** creates `builder/<slug>-<hex>` branches for nested product git repos (`products/<id>/.git`). For **Argus monorepo** workspaces, optional **`git worktree`** isolation uses a dedicated worktree under `runs/builder/worktrees/` (no auto-merge, no push).
- **Post-execution reconcile** captures `builder_diff_summary` (git diff vs baseline), `builder_scope_check.v2` (path scope + semantic scope), `execution_outcome`, **`builder_escalation_emit`** (when rules fire), and `builder_branch_review` (merge readiness label). Scope breach blocks advancement.
- **Permission gate:** **`builder_execute`** is evaluated via **`evaluate_phase1_for_keys`** in `invoke.py` before subprocess (see `project_permission_decision` on the invoke record).
- **Network:** **Not hidden** — `builder_containment` records **`network_mode`** (`default` / `allow_all` / `disabled`). Default allows host-equivalent access inside bwrap; **`disabled`** adds `--unshare-net`. This is **policy visibility**, not silent full trust.
- **Filesystem (graded):** **`product_scoped`** — RO repo root + RW product subtree. **`argus_root_worktree_scoped`** — RO main checkout + RW **worktree** + RW main **`.git`** (not parity with product-scoped; full checkout under worktree). **Plain `argus_root`** or **explicit paths** outside the product dir still use **`legacy_repo_rw`** — wide RW bind. **Linux:** optional **Landlock** write allowlist inside bwrap for non-legacy scopes (skips **`legacy_repo_rw`** honestly).

**Assessment:** Operator-triggered Builder is **a legitimate, policy-aware execution path** with defense-in-depth and inspectable artifacts. **Phase 2 (“Hands”) is operational** for local operator use; **Phase 2A (“Seal the Hands”) is complete with refined caveats** — monorepo trust is **graded** (`argus_root` vs `argus_root_worktree` + scoped FS), not a single blanket “weak.” **Phase 2C** is **substantially complete with caveats** — minimal **contract registry**, **`bug_fix`** / **`signal_instrumentation`** contracts, **`set-target`** ergonomics, worktree + scoped FS + **Linux Landlock** when applicable; plain **`argus_root`** and incremental extensions remain honestly limited. **Phase 2B** (visibility / dashboard / history) is **active / in progress** and **does not** replace 2C deliverables. **Remaining** emphasis: **2B** polish and **incremental** contract/extension work — **not** portfolio memory, learning, or **autonomous Builder** (still **deferred**; see `docs/plans/ephemeral-roadmap.md`).

---

## 2. What Argus Is Architecturally Today

### System identity

Argus is best described as a **deterministic portfolio management system with an emerging execution layer**. The core is still analysis and lifecycle management; Builder is a new appendage that reaches into a fundamentally different domain (code modification).

The prior review's "bounded autonomous steward" label remains accurate for the portfolio spine. But the system now has **two operational modes** with very different risk profiles:

| Mode | Risk profile | Maturity |
|------|-------------|----------|
| **Portfolio analysis and lifecycle** | Low — reads artifacts, writes JSON, bounded promotions (scaffold/bootstrap/plan) | Mature; deep guardrails, escalation, coherence checking |
| **Builder code execution** | Medium–High — agent runs in bwrap with stripped env; **product-scoped FS** when nested product workspace; **worktree-scoped FS** for monorepo when `argus_root_worktree` + scoped mode; **legacy repo rw** for plain **`argus_root`** / some explicit paths; **Linux Landlock** when applied; **network policy explicit** in artifacts | **Operational for operator-triggered use**; permission gate + escalation + containment metadata; **not** autonomous-runner integrated |

### Architectural layers (updated from prior ten-layer model)

The prior review's ten strata remain structurally valid but omit the execution layer:

1. **Deterministic pipeline** — signals → temporal → findings → decisions → ideas
2. **Learning loop** — per-product strategy snapshots, experiments
3. **Mission and policy** — profiles, mapping, effective mission, provenance
4. **Per-product orchestration** — eligibility, step execution, action policies
5. **Product lifecycle** — creation proposals/scaffold/bootstrap, deprecation proposals/plans, promotion records
6. **Portfolio operator spine** — queue, cycle, scheduler, outcomes, patterns, intervention
7. **Portfolio strategy** — whole-portfolio posture, soft influence on queue/creation
8. **Autonomy and lifecycle** — autonomous runner, runner service, escalation inbox, substrate coherence
9. **Policy self-assessment** — effectiveness, recommendations, learning synthesis
10. **Operator console** — Streamlit dashboard (now with partial control via Governance tab)
11. **Builder execution layer** — prepare → invoke → reconcile → status; work orders from signal contract; creation Phase 1 from world-context candidates

Layer 11 is structurally separate from layers 1–10 for **orchestration** — it does not flow through the portfolio operator spine or the autonomous runner. **Per-product policy** still applies: Builder invoke evaluates **`builder_execute`** via `project_permissions/gate.py` before execute. The **remaining** integration gap is **portfolio feedback and autonomous dispatch**, not “no policy at all.”

---

## 3. What Changed Since the Previous Architecture State

The prior review (April 14, 2026) described a system with no Builder, a read-only dashboard, and execution limited to pipeline stages and lifecycle promotions.

### Changes not captured in the prior document

| Area | Prior assumption | Current reality |
|------|-----------------|-----------------|
| **Builder** | Not mentioned | Full subsystem: prepare, invoke (cursor + agent backends), reconcile, status, work orders, creation Phase 1 |
| **Code execution** | "Pipeline stages + lifecycle promotions" | Agent subprocess can modify any file in workspace; 7200s default timeout |
| **Dashboard control** | "Read-only; not a control plane" | Governance tab writes approvals, runs CLI subprocesses, calls `advance_orchestration(execute=False)` |
| **Tab count** | 7 tabs | 8 tabs (Governance added) |
| **Artifact specs** | 10 keys | 14 keys in `console_artifact_specs()` |
| **Git handling** | Not discussed | `git_lifecycle.py` (init+commit), importer cache (clone+pull), github_onboarding (shallow clone into `app/`) |
| **Containment** | Not discussed | `containment/policy.py` — env var stripping, capability map, `refuse_unless_capability` |
| **Permission gate** | Not discussed | `project_permissions/gate.py` — Phase 1 keys, `argus.policy.yaml` per product, Phase 2 approval grants |
| **Cross-session memory** | "Sessions don't carry state" | `autonomy_memory.py` — confidence adjustment from history, degraded streak via `substrate_policy_state` |
| **Autonomous spawn** | Not discussed | `autonomy/spawn.py` — proposal + apply phases with quota system (separate from portfolio runner) |

### What is now stale in the prior document

- §5 autonomy assessment: does not account for Builder execution risk
- §8 operator control model: dashboard is no longer purely read-only
- §11 load-bearing invariants: "LLMs contribute interpretation only — never control decisions" — Builder agent is LLM-powered and controls code changes
- §12 gaps: does not list git discipline, scope enforcement, or reconciliation verification
- §13 risks: does not include code execution risk, credential exposure, or scope breach
- Quantitative metrics (LOC, schema counts, CLI counts) are outdated

---

## 4. Builder and Real Execution: Containment Model

### Where Builder sits

Builder is a **parallel execution path** that bypasses the portfolio operator spine:

```
Portfolio spine:     signals → findings → decisions → orchestration → cycle → guardrails
Builder:             next_expansion → prepare → invoke(agent) → reconcile → [signals, findings]
```

The only integration point is that reconcile can run `cmd_signals_collect` and `cmd_findings_generate` after execution. Builder does not feed outcomes, patterns, intervention, escalation, or the autonomous runner.

### What the agent runs inside

When `run_builder_invoke` runs with `execute=True, execution_backend="agent"`:

1. **`build_agent_containment`** (`argus/builder/sandbox.py`) decides the subprocess environment:
   - Default (`--sandbox auto`): **requires bubblewrap** (`bwrap`) on PATH. Refuses execution without it unless `--allow-unsandboxed` or `ARGUS_BUILDER_ALLOW_UNSANDBOXED=1` is set (records `trust_degraded_unsandboxed: true`).
   - `--sandbox bwrap`: explicit bubblewrap requirement.
   - `--sandbox none`: explicit degraded trust (recorded in containment artifact).

2. **Bubblewrap sandbox** provides:
   - `--unshare-pid` + `--die-with-parent` (PID namespace; agent dies with parent)
   - Optional **`--unshare-net`** when **`network_mode: disabled`** (recorded on `builder_containment`)
   - `--proc /proc`, `--dev /dev`, `--tmpfs /tmp`
   - Read-only bind mounts: `/usr`, `/lib`, `/lib64`, `/bin`, `/sbin`, `/opt`, `/etc/resolv.conf`, `/etc/nsswitch.conf`, `/etc/hosts`
   - **Filesystem:** **product-scoped** (`--ro-bind` repo root + RW overlays on `products/<id>/`, scratch HOME, prepare dir, …), **`argus_root_worktree_scoped`** (RO main checkout + RW worktree + RW main `.git`, see `sandbox.py`), or **`legacy_repo_rw`** full-repo RW bind when scope inference requires it
   - Fake HOME at `runs/builder/.sandbox/home/<product_id>/` (agent cannot access `~/.ssh`, `~/.gitconfig`, etc.)
   - Agent binary parent directory bind-mounted read-only if outside standard trees

3. **Environment stripping** (`strip_builder_agent_environment`):
   - Aggressive prefix strip: `AWS_`, `AZURE_`, `GCP_`, `GITHUB_`, `GITLAB_`, `GIT_`, `SSH_`, `KUBE*`, `GOOGLE_`, `OPENAI_`, `ANTHROPIC_`, `DOCKER_`, `NPM_TOKEN_`, etc.
   - Exact key strip: `SSH_AUTH_SOCK`, `GITHUB_TOKEN`, `GH_TOKEN`, `AWS_*`, `GOOGLE_APPLICATION_CREDENTIALS`, `ARGUS_AGENT_EXTRA_ARGS`, etc.
   - Allow-list only: `PATH`, `HOME`, `USER`, `LOGNAME`, `LANG`, `LC_ALL`, `LC_CTYPE`, `TERM`, `TZ`, `TMPDIR`

4. **`setpriv --no-new-privs`** on Linux wraps the outer argv (including bwrap), blocking privilege escalation through exec (setuid binaries, file capabilities). Missing `setpriv` records `trust_degraded_missing_no_new_privs: true`.

5. **Landlock (Linux, defense in depth):** When enabled and not **`legacy_repo_rw`**, the inner agent may run via `landlock_launcher` with a **write-focused** allowlist (`landlock_*` fields on containment); failures are **recorded**, not silently trusted.

6. **Containment artifact** (`argus.builder.containment.v1`): every invoke records `containment_requested`, `containment_applied`, `containment_fallback_used`, `trust_degraded_*` flags, **filesystem scope modes**, **network_mode / network_applied / network_reason**, stripped env key count, bwrap path, no_new_privs status, **`landlock_*`** when relevant. Full audit trail.

### What Builder does NOT do (remaining gaps)

- **Network not restricted by default:** Default **`network_mode`** allows host-equivalent access inside bwrap (documented). **Offline** is **opt-in** (`--network disabled` / env). This is intentional for typical agent workloads.
- **Product-scoped mount not universal:** When **`filesystem_scope_mode: product_scoped`**, preventive mounts apply. **`legacy_repo_rw`** (plain **`argus_root`** workspace, or explicit paths outside the product dir) still exposes a **wide read-write** repo bind — scope remains **reconcile-enforced**, not mount-enforced, in those cases. **`argus_root_worktree_scoped`** is **narrower** than legacy on the primary checkout but still a **large RW worktree** (not nested-product parity).
- **No rollback:** If the agent makes bad changes on a branch, the operator must manually delete or reset the branch.

**Implemented (do not treat as “missing” in checklists):**

- **Permission integration:** `run_builder_invoke` calls **`evaluate_phase1_for_keys`** for **`builder_execute`** before execute; see invoke record **`project_permission_decision`**.
- **Escalation:** After reconcile, **Builder escalation rules** can emit **`builder_escalation_emit`** (and `runs/escalations/latest/` packets) for serious scope/breach/trust signals — not “JSON only.”
- **Host readiness:** **`argus builder doctor`** (static PATH/env; `argus.builder.host_readiness.v1`).

### What Builder DOES enforce

- **Bubblewrap sandbox (default-on, refuse-without):** Filesystem boundary, PID namespace, fake HOME, stripped env. Operator must explicitly opt into degraded trust to bypass. **Product-scoped rw overlays** when `filesystem_scope_mode: product_scoped` (see `builder_containment`).
- **Phase 1 permission gate:** **`builder_execute`** evaluated before execute; failed gate → `invocation_status: failed` with **`project_permission_decision`**.
- **Post-reconcile escalation (rules-based):** Serious scope/trust/outcome signals can emit **`builder_escalation_emit`** and escalation packets (see `argus builder reconcile`).
- **Stale contract guard** (`assess_prepared_contract_vs_declared`): Blocks execution when prepared task diverges from declared target.
- **Branch isolation** (nested product git): Creates `builder/<slug>-<hex>` branch before execution. No auto-merge, no push. Dirty-tree proceeds with `trust_degraded_dirty_tree: true`.
- **Git baseline + diff capture:** Pre-invoke `baseline_commit` (HEAD before work), `working_tree_dirty_before`, `git_workspace_kind`. Post-invoke `post_invoke_git_diff` with bounded diff, diff stat, and changed files (argus-relative paths).
- **Scope verification** (`builder_scope_check.v2`): Two-layer post-execution check:
  - **Path scope:** Git diff changed files vs contract allow-list. Argus-core modifications (`argus/` tree) flagged as hard breach (`path_scope:modified_argus_core`), distinct from other out-of-tree changes (`path_scope:modified_non_product_root`).
  - **Semantic scope:** When contract policy is `do_not_change_primary_target`, compares `builder_task.json` resolved target to on-disk `next_expansion.json` primary target. Catches in-path-but-wrong-field edits that path allow-lists cannot see.
  - Scope breach blocks `prepare-next` advancement.
- **Execution outcome** (`argus.builder.execution_outcome.v1`): Classifies result as `completed` / `partial` / `blocked` / `breached` / `unknown` based on scope, invoke status, and filesystem evidence.
- **Merge readiness** (`argus.builder.branch_review.v1`): Conservative label:
  - `merge_candidate`: branch isolation OK, no degraded flags, scope/outcome clean, containment = bwrap, no_new_privs applied (when requested); for **`product_scoped`** or **`argus_root_worktree_scoped`**, Landlock must be **applied** when Landlock was **requested** (see `branch_review.py`)
  - `review_required`: not unsafe but trust or outcome ambiguous
  - `blocked`: no merge path (invoke not execute, branch isolation skipped/failed)
  - `unsafe`: scope/path/semantic/argus-core breach — do not merge without fixing scope
- **Manual merge workflow:** `argus builder merge <PRODUCT_ID>` — refuses unless `review_status == merge_candidate`; local-only; no push/PR.
- **Honest records:** Every invoke writes a versioned record with containment, git baseline, exit code, truncated output. "Exit 0 does not mean task success."
- **Opt-in execution:** `--execute` must be explicitly passed. Default is review mode.
- **Timeout:** Configurable (default 7200s, env `ARGUS_AGENT_TIMEOUT_SECONDS`).

### Residual risks (updated)

1. **Network exfiltration (default mode):** Unless **`network_mode: disabled`**, the agent can reach the internet inside bwrap. Policy is **explicit** in artifacts; trust flags exist for **`allow_all`**.
2. **Repo-wide write access (legacy FS scope):** When **`filesystem_scope_mode: legacy_repo_rw`**, the agent may still write broadly under the repo. **Product-scoped** mode reduces blast radius for nested product layouts.
3. **Plain monorepo-root git (`argus_root`):** When `git_workspace_kind == "argus_root"` (no nested `.git` under product, no worktree path), per-product **branch isolation is skipped** — **`legacy_repo_rw`**-style containment applies. **`argus_root_worktree`** uses **`git worktree`** + scoped mounts — **not** the same as nested-product isolation, but **not** “no isolation option.”
4. **Contract coverage is minimal, not universal:** **`content_slot`**, **`bug_fix`**, and **`signal_instrumentation`** have registry-backed execution contracts and scope allow-lists; **other** target kinds or ad-hoc tasks may still lack a contract block — reconcile scope may be **vacuous** in those cases.
5. **Execution outcome is existence-based:** "Completed" means the right files exist at non-trivial size — not that content is correct.
6. **Agent tool use within sandbox:** The headless agent has arbitrary tool access (shell, file write) within the bwrap mount. There is no tool-call allowlist or denylist.

---

## 5. Updated Autonomy Assessment

### What Argus can do autonomously (in practice)

**Portfolio spine (mature, guarded):**
- Run bounded autonomous sessions (refresh → cycle → lifecycle → summary → narrative, iterated with guardrails)
- Stop on pipeline failure, quiescence, intervention-heavy streak, material-change streak, artifact coherence degradation, explicit sentinel, or max cycles
- Execute bounded promotions when `--allow-promotion`: at most one creation scaffold, one bootstrap, one deprecation plan per session — only items flagged `safe_for_auto`
- Suppress promotions on degraded substrate; abort sessions on persistent degradation
- Build escalation inbox and cross-session memory; adjust confidence within narrow bounds

**Builder (maturing, layered containment):**
- Prepare prompt+task from declared expansion target
- Invoke a headless agent inside bubblewrap sandbox with stripped env (**CLI only; not in autonomous runner**)
- Record containment status, git baseline, post-invoke diff in invoke artifact
- Reconcile with two-layer scope verification (path + semantic) and execution outcome classification
- Block advancement on scope breach; classify merge readiness conservatively
- Block invoke on stale contract alignment

### What it should NOT do yet

- **Builder in the autonomous loop:** The autonomous runner does not call Builder. This is correct. **Unattended** use still lacks **portfolio feedback**, **broad contract coverage**, **runner integration**, and **operational UX** — not a blank “unsafe” verdict on the current operator path.
- **Multi-product Builder execution:** Heuristic **`next_expansion`** generation is not uniform across products; **non-story** contracts (`bug_fix`, `signal_instrumentation`) require explicit targets and registry wiring — **not** a guarantee that every product has a full template matrix without setup.
- **Builder without bwrap:** Unsandboxed execution is possible (`--allow-unsandboxed`) but records `trust_degraded_unsandboxed` and downgrades merge readiness. Routine use should require bwrap.
- **Auto-merge of Builder output:** `argus builder merge` exists but requires `merge_candidate` status and is local-only. No auto-merge in any automated context.

### Safe for unattended operation

- Portfolio analysis spine (all read-only evaluations)
- Autonomous runner **without** `--allow-promotion` (pure analysis iterations)
- Autonomous runner **with** `--allow-promotion` on a portfolio where creation/deprecation are low-stakes (scaffold + bootstrap are reversible; deprecation plans are advisory-only)
- Runner service with cadence intervals and STOP sentinel

### Prematurely ambitious without further hardening

- Builder `--execute` in any automated context (autonomous runner, cron, service loop)
- Builder without bwrap in production use
- Builder increments with **no** registered **`contract_kind`** / execution contract block (path scope may lack an allow-list)
- Any flow that chains Builder invoke → reconcile → Builder invoke without operator review of merge readiness

---

## 6. Updated Argus → Planner → Builder Model

### Current reality: three parallel tracks, not a pipeline

The system has three distinct "recommendation → execution" paths that do not feed into each other:

**Track 1: Portfolio planning**
```
signals/findings/decisions → product strategy snapshot → planning snapshot → ActionContract
→ (requires operator approval) → argus execution run
```
Grounded in `argus/planning/`, `argus/strategy/`, `argus/actions/`. ActionContracts have `requires_approval=True`, `safe_to_auto_execute=False` by default. This track is **advisory** unless the operator explicitly runs approved actions.

**Track 2: Builder execution**
```
next_expansion.json → prepare (prompt + task) → invoke (agent subprocess) → reconcile
```
Grounded in `argus/builder/`. Completely independent of Track 1. Builder's work-order path (`signal_contract → builder_work_orders`) is also disconnected from planning actions.

**Registry vs prepare:** `contract_registry.py` owns **execution-contract dispatch** and **reconcile outcome dispatch**; `next_expansion_prepare.py` owns **markdown** (`builder_next_prompt.md`) and task JSON assembly, and pulls the registry so `builder_task.json` matches reconcile. The split is deliberate—renderers are not “missing” from the registry.

**Track 3: Worker lane**
```
work_order issuance → approve → execute_work_order
```
Grounded in `argus/worker/`. Only `signal_instrumentation` has real execution; everything else is dry skeleton. Uses `argus.work_order.v1` (different schema from Builder's `argus.builder_work_order.v1`).

### Boundary assessment

| Boundary | Clean? | Issue |
|----------|--------|-------|
| Analysis → decisioning | Yes | Deterministic pipeline with clear schemas |
| Decisioning → planning | Yes | Strategy snapshot → planning snapshot is typed and testable |
| Planning → execution | Partially | ActionContract model exists but most execution is operator-CLI; no auto-execute path |
| Analysis → Builder | Weak | `next_expansion.json` is the only typed input; no formal connection to findings/decisions |
| Builder prepare → invoke | Clean | Stale contract guard; typed task JSON; containment decision |
| Builder invoke → reconcile | Improved | Diff capture, two-layer scope check, execution outcome, merge readiness. Still no quality verification beyond file existence |
| Builder → portfolio feedback | **Partial** | **Coordination visibility:** `runs/portfolio/builder_activity/latest.json` includes **`builder_outcome_summaries`** when refreshed; full per-product **`argus.builder_outcome.v1`** lives under **`runs/builder/outcome/<id>/latest.json`** (artifact-grounded, conservative attribution, **`comparison_provenance`**, plus Phase 3B **`observation_timing`** / **`recent_observation_summary`** — still **not** causal learning, strategy, or cross-product scoring). **Strategic loop** (outcomes/patterns/strategy influenced by Builder) remains **Phase 3 / Tier 3** beyond this bridge |

### Are we jumping ahead?

**Less than before, but yes in specific areas.** Builder execution is real, functional, and now has meaningful containment:
- The **planning** bridge into Builder remains thin — `next_expansion.json` / manual targets drive most work; **registry-backed** kinds include **`content_slot`**, **`bug_fix`**, **`signal_instrumentation`** — not a content-slot-only world, but **not** an open plugin surface
- The downstream verification (did Builder do the right thing) is **substantially improved** — diff capture, path+semantic scope check, execution outcome classification, merge readiness labeling. Still no quality verification beyond file existence.
- The **strategic** lateral integration (Builder work → portfolio outcomes / strategy / learning) still doesn’t exist; **visibility** (`builder_activity`) is intentionally narrower

The architecture has shifted from "powerful execution with no verification" to "powerful execution with honest verification, **policy gates**, **escalation**, **explicit network and FS scope metadata**, and conservative merge gates." **Remaining** emphasis: **operator visibility (2B)** (incl. **`builder_activity`** coordination rollup), **incremental** contract/extension polish on top of **2C** deliverables already in code (registry, non-story contracts, worktree/scoped substrate), and **strategic portfolio feedback (Phase 3)** — not re-litigating whether bubblewrap, worktrees, or non-story contracts exist.

---

## 7. End-to-End Lifecycle Assessment

### 7.1 Empty state → new product → real repo → iteration

**What exists:**

| Step | Module | Status |
|------|--------|--------|
| Detect portfolio gap | `argus/products/creation.py` | Real; gap heuristics from inventory/outcomes/patterns/queue |
| Propose new product | `creation.py` → `evaluate_creation_proposals` | Real; mission-grounded proposals |
| Scaffold product directory | `argus/products/creation_scaffold.py` | Real; writes `products/<id>/` with manifest, scripts, stubs |
| Local git init | `argus/products/git_lifecycle.py` | Real; `git init` + first commit, no remote |
| Bootstrap first Argus loop | `argus/products/creation_bootstrap.py` | Real; signals → findings → decisions → ideas |
| Bring under portfolio management | `argus/portfolio/lifecycle.py` | Real; lifecycle status derivation |
| Autonomous promotion (scaffold + bootstrap) | `argus/portfolio/autonomous_runner.py` | Real; bounded, `safe_for_auto` gated |

**What is missing:**

- **Remote repository creation:** No GitHub/GitLab repo creation. Product exists only as a local directory. There is no `git remote add` or `git push` in any code path.
- **Branch workflow:** No feature branches, no main branch protection, no PR model. All work happens on the default branch.
- **Builder integration for new products:** Builder's `creation_phase1.py` can propose and apply scaffold from world-context candidates, but the connection to the autonomous runner's promotion path is indirect (promotion calls `creation_scaffold`, not Builder).
- **Iteration via Builder:** After bootstrap, there is no automated path from "product exists" to "Builder iterates on it." The operator must manually set up `next_expansion.json` and invoke Builder.
- **Scope enforcement for new products:** When the autonomous runner scaffolds a product, there is no containment boundary. The scaffold write targets `products/<id>/` by hardcoded convention, not by policy.

**Danger points:**

1. **Scaffold always targets default `products/` tree** — `creation_scaffold.py` does not accept `--products-dir` parameter, unlike bootstrap and other modules. If the convention changes, scaffold breaks.
2. **Mission driver coverage heuristic is non-functional** — `creation.py` `_detect_gaps` builds `driver_product_coverage` but never increments counts from inventory; `gap.mission_driver_uncovered` always fires when drivers exist.
3. **No validation that scaffolded product is actually buildable** — bootstrap runs signals/findings but doesn't verify the product has real code or can produce meaningful output.

### 7.2 Existing repo → import/clone → iteration

**Two separate, non-unified pipelines:**

| Aspect | Importer (`argus/importer/cli.py`) | GitHub onboarding (`argus/portfolio/github_onboarding.py`) |
|--------|-----------------------------------|-----------------------------------------------------------|
| Tree layout | Full repo = product root | Repo cloned into `products/<id>/app/`; Argus owns root |
| Manifest | Importer-generated `product.yaml` | Scaffold `product.yaml` + onboarding extension |
| Git model | Clone to cache → rsync → optional first-pass | Shallow clone → admit → optional bootstrap |
| Replay | `replay.py` can re-sync from stored commit | No replay mechanism |
| Orchestration | Not called during import | Calls `write_orchestration_state` |
| `--products-dir` | Supported | Supported |

**What exists:** Both paths can bring an external repo under Argus management with signals/findings/decisions.

**What is missing:**

- **Unified import model:** Two paths with different manifest structures and follow-on hooks. Operator must choose; the system doesn't guide the choice.
- **Ongoing sync:** Neither path has a "pull latest from remote and re-evaluate" loop. Importer has `replay` (re-sync from stored commit) but no periodic pull. GitHub onboarding has no update mechanism at all.
- **Builder iteration on imported products:** After import, Builder can be pointed at the product, but only if that product ships a `content_catalog.json`. The expansion heuristic assumes a catalog of ordered slots and a conventional static-site layout, so it does not apply to products shaped differently. Work orders from the signal contract are the general path but produce briefs, not executable tasks.
- **Drift detection:** Importer's `inspect_import_drift` compares stored commit to cache HEAD and optionally fetches, but this is a manual check, not a loop.

**Danger points:**

1. **GitHub onboarding is destructive:** `shutil.rmtree(app_dir)` before every clone. No incremental update, no merge.
2. **Importer rsync `--delete` by default:** Can remove files in `products/<id>/` that aren't in the source repo.
3. **No remote push after Builder execution:** If Builder modifies an imported product, changes stay local. There's no mechanism to push back to the source repo or create a PR.
4. **Two import models create manifest divergence:** Products imported via `importer` have different `raw_extensions` than those imported via `github_onboarding`. Portfolio-level logic that reads these extensions must handle both formats.

---

## 8. Repo Truth, Git, and Reconciliation Assessment

### Current repo truth model

Argus has **three reconciliation concepts** at different levels:

1. **Portfolio artifact coherence** (`argus/portfolio/artifact_coherence.py`): Verifies internal consistency of `runs/` artifacts — outcomes canonical, intervention durability, autonomous session alignment, strategy input integrity. This is **structural reconciliation** of the artifact substrate.

2. **Builder reconcile** (`argus/builder/reconcile.py`): Post-invoke verification layer that captures git diff, checks path+semantic scope against execution contract, classifies execution outcome, and labels merge readiness. This is **execution-level reconciliation** that verifies scope compliance but does not assert implementation quality.

3. **Builder branch review** (`argus/builder/branch_review.py`): Conservative merge readiness classification that synthesizes containment status, branch isolation, scope check, and execution outcome into a single label (`merge_candidate` / `review_required` / `blocked` / `unsafe`).

Together, reconcile and branch review answer: **"What did the agent change, was it within scope, and is it safe to merge?"** They do not answer: **"Is the implementation correct or high-quality?"**

### Git grounding assessment

| Capability | Status | Gap |
|------------|--------|-----|
| Local git init for new products | Real (`git_lifecycle.py`) | No remote, no branch workflow |
| Git clone for imports | Real (importer cache, github onboarding) | Shallow clone; no ongoing sync |
| Commit recording | Importer stores `imported_from_commit`; Builder records `baseline_commit` | Builder does not auto-commit after execution |
| Branch management (Builder) | Real for nested product git (`builder/<slug>-<hex>` branches); **`argus_root_worktree`** uses **`git worktree`** + builder branch at worktree | Plain **`argus_root`** (no worktree): **no** per-product branch isolation — not the same as “monorepo has no isolation option” |
| Diff capture (Builder) | Real — `post_invoke_git_diff` in invoke, `builder_diff_summary` in reconcile | Diff is truncated (bounded size); no full patch archive |
| Scope verification | Real — `builder_scope_check.v2` (path + semantic), argus-core breach flagged | **Preventive FS** when `product_scoped`; **post-hoc** when `legacy_repo_rw` |
| Merge workflow | Real — `argus builder merge` (local, requires `merge_candidate`) | No push, no PR, no remote operations |
| Push/PR | None | No remote operations beyond initial clone |
| Rollback | Manual only | Branch can be deleted/reset but no automated undo mechanism |

### What must still change

**Git-native product lifecycle is emerging but incomplete.** Specifically:

1. **~~Pre-execution commit discipline~~** — Done. `git_baseline.v1` records `baseline_commit`, `working_tree_dirty_before`, `git_workspace_kind`.
2. **~~Post-execution diff capture~~** — Done. `post_invoke_git_diff` + `builder_diff_summary.v1` with changed files (argus-relative).
3. **~~Branch isolation~~** — Done for nested product git. **Argus monorepo:** **`git worktree`** path (`argus_root_worktree`) **in code**; plain **`argus_root`** still has no per-product branch isolation.
4. **~~Scope verification from diff~~** — Done. `builder_scope_check.v2` with path + semantic layers. Argus-core breach distinguished.
5. **Commit attribution:** Builder does not auto-commit. The operator manually commits or merges. If auto-commit is added, it should carry metadata (task id, invoke record id, agent backend).
6. **Branch isolation for monorepo workspaces:** **`git worktree`**-based path is implemented for **`argus_root_worktree`**; plain **`argus_root`** remains degraded (see `git_worktree_isolation.py`, `branch_review.py`).
7. **Remote workflow:** No mechanism to push branches, create PRs, or integrate with GitHub/GitLab review workflows.

---

## 9. Operator Trust and Control Plane Assessment

### What an operator needs to trust Builder

| Need | Current state |
|------|--------------|
| **See what Builder will do before it does it** | Met: `builder_next_prompt.md` is readable; `builder_task.json` has target details; execution contract declares path allow-list |
| **See what Builder actually did** | Mostly met: `builder_diff_summary` captures git diff, stat, and changed files. Diff is truncated; no full patch archive |
| **Review changes before they take effect** | Met for nested git: branch isolation + `argus builder review` + `argus builder merge` (requires `merge_candidate`). For **`argus_root_worktree` + scoped FS**, merge workflow applies when **`merge_candidate`** — **not** met for plain **`argus_root`** (no `merge_candidate` for agent) |
| **Scope breach detection** | Met: `builder_scope_check.v2` (path + semantic); argus-core breach flagged separately. Post-hoc only |
| **Credential safety** | Mostly met: bubblewrap strips env; fake HOME prevents `~/.ssh`, `~/.gitconfig`. **Network policy recorded**; offline opt-in |
| **Rollback** | Partially met: branch isolation means changes are on a separate branch; operator can delete/reset. No automated undo |
| **Audit trail** | Met: containment artifact, git baseline, diff summary, scope check, execution outcome, branch review — all versioned JSON |
| **Automatic stop on bad execution** | Partially met: scope breach blocks advancement; merge readiness downgrades to `unsafe`. **Escalation packets** for serious signals (see `builder_escalation_emit`) |

### What should be surfaced

- **Pre-execution:** Prepared task summary, declared scope, contract alignment status, containment status (**partially met in CLI**; **dashboard gap** = Phase 2B)
- **Post-execution:** File diff summary, scope compliance, agent exit code, signals/findings delta
- **In dashboard:** Builder execution history per product, last invoke/reconcile status, any scope breaches (**Phase 2B**)
- **In escalation:** **Implemented** for defined rule sets — extend coverage is optional hardening

### What should be blocked automatically

- Invoke without bubblewrap sandbox (already enforced by default; `--allow-unsandboxed` is the explicit override)
- Invoke when permission gate for `builder_execute` denies (**implemented** — see `project_permission_decision`)
- Re-invoke on same target when prior reconcile shows `unsafe` merge readiness
- **Autonomous** Builder execution (still **not** wired — **not** the same as “permission gate missing”)
- Merge when `review_status != merge_candidate` (already enforced by `argus builder merge`)

---

## 10. What Is Now Load-Bearing

These are architectural invariants that should not be casually broken:

1. **Deterministic artifact substrate:** All decisions, lifecycle transitions, and portfolio state are durable JSON under `runs/`. Truth flows from files, not runtime state. **Builder records must maintain this invariant** — invoke/reconcile/status all write versioned JSON.

2. **File-based durability:** No hidden databases. The repo + `runs/` + `products/` is the complete system state. **Builder execution outcomes must be captured in artifacts**, not just subprocess exit codes.

3. **Mission/policy separation from truth:** Mission shapes behavior (scoring, thresholds, sensitivity); it does not alter signal collection, findings, or raw pipeline output. **Builder prompt content should reflect mission but the agent's filesystem access should not be mission-scoped** — scope is a safety concern, not a policy concern.

4. **Bounded promotions:** At most one creation, one bootstrap, one deprecation plan per autonomous session. Only `safe_for_auto` items execute. **Builder execution should follow the same bounded pattern** when it enters the autonomous loop.

5. **Lifecycle artifacts as source of truth:** Portfolio lifecycle synthesis reads creation/scaffold/bootstrap/deprecation artifacts to determine product status. **Builder execution should produce lifecycle-affecting artifacts** when it creates or modifies products.

6. **Reconcile as truth-checking:** Portfolio artifact coherence validates substrate consistency. **Builder reconcile now verifies execution scope** (path + semantic), classifies outcomes, and labels merge readiness. This is a real truth-checking layer, not just target transition detection.

7. **Git-native truth (emerging → real for Builder):** Import state records branch+commit. Product git init creates local history. **Builder now creates git baselines, captures diffs, isolates branches, and gates merges.** This pattern should extend to all execution paths.

8. **Execution contracts:** ActionContract model gates execution with approval flags. Phase 1 permission keys gate by policy. **Builder invoke evaluates `builder_execute` via `gate.py` before execute** and records **`project_permission_decision`**. Builder also has **`execution_contract.v1`** for scope enforcement in reconcile.

9. **Scope enforcement:** Builder execution contracts declare scope. **Verified in reconcile** via `builder_scope_check.v2` (path + semantic). **Preventive filesystem enforcement** applies when **`filesystem_scope_mode: product_scoped`**; legacy paths remain reconcile-heavy.

10. **Planner/Builder boundary:** Planning produces advisory recommendations; Builder executes. **The boundary must stay explicit** — Builder should not autonomously choose what to work on.

---

## 11. What Is Out of Date

### Prior document claims now superseded

| Claim (prior §) | Status | Reality |
|-----------------|--------|---------|
| Dashboard is read-only, not a control plane (§8) | **Stale** | Governance tab writes approvals, runs CLI, calls `advance_orchestration` |
| 7 tabs in dashboard (§2.12, burn-down) | **Stale** | 8 tabs (Governance added) |
| 10 artifact specs in console (§2.12) | **Stale** | 14 keys including world context, coherence, runner service, escalation |
| LLMs contribute interpretation only, never control decisions (§11.1) | **Stale** | Builder agent is LLM-powered and controls code changes |
| Sessions don't carry state (§12.7) | **Stale** | `autonomy_memory.py` carries cross-session patterns; `substrate_policy_state` tracks degraded streaks |
| LOC counts, schema counts, CLI counts (appendix) | **Stale** | Codebase has grown significantly |

### Claims from this document's prior revision (April 16 initial) now superseded

| Claim | Status | Reality |
|-------|--------|---------|
| "No sandbox, no filesystem jail, no branch isolation" (§4) | **Superseded** | Bubblewrap sandbox default-on; branch isolation for nested product git; **`argus_root_worktree`** worktree path for monorepo; env aggressively stripped |
| "No diff review" (§4) | **Superseded** | `builder_diff_summary.v1` captures git diff, stat, changed files in reconcile |
| "No scope enforcement beyond prompt constraints" (§4) | **Superseded** | `builder_scope_check.v2` (path + semantic); argus-core breach flagged; scope breach blocks advancement |
| "No detection mechanism" for scope breach (§4, §12) | **Superseded** | Path scope + semantic scope + argus-core breach detection in reconcile |
| "No containment by default" (§4) | **Superseded** | Bubblewrap + env stripping default-on; refuse-without-sandbox unless explicit degraded opt-in |
| "Containment model is v0 — env stripping only" (§1, §12) | **Superseded** | `sandbox.py` implements bubblewrap (PID namespace, fake HOME, ro mounts), distinct from `containment/policy.py` v0 |
| "No commit recording for Builder-modified trees" (§8) | **Superseded** | `git_baseline.v1` records `baseline_commit`; `post_invoke_git_diff` records bounded diff |
| "No feature branches, no main protection" (§8) | **Partially superseded** | Branch isolation for nested product git; **`argus_root_worktree`** adds worktree isolation. Plain **`argus_root`** still unprotected for per-product branches |

### Architectural assumptions that need rethinking

- **"Proposal only" for new work:** Creation proposals, deprecation proposals, experiment proposals — all assumed advisory. Builder execution means "proposal → execution" is now a real path, but the proposal schemas were designed for human review, not automated dispatch.
- **"Advisory" planners feeding humans:** Weekly plan, planning actions, strategy recommendations all assume a human will evaluate and execute. If Builder can be pointed at planning output, the "advisory" label becomes a risk label.
- **"Read-only analysis" as the dominant mode:** The autonomous runner's primary loop is still analysis-heavy. But the capability exists (via Builder CLI, manual invocation) to chain analysis → execution → re-analysis without operator review.

---

## 12. Biggest Risks

**Ranked by severity × proximity (updated for containment posture):**

1. **Network exfiltration (default mode)** (high, **when** default network): Policy is explicit; **offline is opt-in**. Risk remains for operators who need network and accept default/`allow_all`.

2. **Repo-wide write access (legacy FS scope)** (high, **when** `legacy_repo_rw`): Still possible for `argus_root` or explicit paths. **Product-scoped** reduces blast radius for nested layouts.

3. **Policy / environment mismatch** (medium, current): `builder_execute` is enforced, but **declarative** environment hints can still refuse execution when policy says “yes” — operator must fix grants (see project permissions docs).

4. **Escalation coverage** (medium, current): **Builder escalation** exists for defined rules. Broader “every degraded trust flag → inbox” is not fully automated.

5. **Builder/portfolio disconnection** (high, medium-term): Builder execution outcomes don't feed portfolio reasoning. The system cannot answer "did Builder's work improve the product?" without manual intervention.

6. **Three parallel containment models** (medium, current): `sandbox.py` (Builder), `containment/policy.py` (execution engine), and `execution/sandbox.py` (action execution) have independent env stripping, capability models, and enforcement. They don't share policy or compose. Maintenance burden and inconsistency risk.

7. **Two import models with manifest divergence** (medium, current): Importer and GitHub onboarding produce different `raw_extensions` schemas. Portfolio logic must handle both; mistakes cause silent data loss in lifecycle synthesis.

8. **Non-functional creation heuristic** (medium, current): Mission driver coverage in `creation.py` never increments from inventory, causing `gap.mission_driver_uncovered` to always fire.

9. **Contract coverage not universal** (medium, current): **`bug_fix`** and **`signal_instrumentation`** are implemented with registry wiring; **other** kinds or targets **without** a contract block still yield **vacuous** or weaker scope checks — incremental templates remain useful, not “first non-story missing.”

10. **No ongoing sync for imported repos** (medium, medium-term): After initial import/clone, there's no automated path to pull upstream changes. Products drift from their source repos silently.

11. **Planning → Builder gap** (medium, medium-term): No typed bridge from planning recommendations to Builder tasks. Builder currently operates independently of planning output.

12. **Plain monorepo-root workspace (`argus_root`)** (medium, current): When `git_workspace_kind == "argus_root"`, nested-style branch isolation is skipped and **`legacy_repo_rw`** applies — changes can touch the main working tree broadly. **`argus_root_worktree`** mitigates with a dedicated worktree + scoped mounts — **not** a full fix for all monorepo cases.

---

## 13. Concrete Recommendations

**Ordered by impact on trustworthiness of the autonomous loop. Items marked ~~strikethrough~~ are implemented; remaining items are ordered by priority.**

### Already implemented (verify, don't rebuild)

~~1. **Mandatory pre/post-execution commit discipline.**~~ Done. `git_baseline.v1` records `baseline_commit` and `working_tree_dirty_before`. `post_invoke_git_diff` captures bounded diff, stat, and changed files. `builder_diff_summary.v1` in reconcile.

~~2. **Branch isolation for Builder execution.**~~ Done for nested product git (`builder/<slug>-<hex>` branches). **`argus_root_worktree`** adds **`git worktree`** isolation; plain **`argus_root`** remains without nested-style branch isolation.

~~3. **Scope verification in reconcile.**~~ Done. `builder_scope_check.v2` with path scope (git diff vs allow-list, argus-core breach detection) and semantic scope (`primary_target` identity check). Scope breach blocks `prepare-next`.

~~4. **Containment enforcement for agent subprocess.**~~ Done via `sandbox.py` (separate from `containment/policy.py`). Bubblewrap default-on, refuse-without. Env aggressively stripped. `setpriv --no-new-privs` on Linux.

~~5. **Product-scoped filesystem (when applicable).**~~ Done — `filesystem_scope_mode: product_scoped` uses read-only repo root + read-write product subtree (and related paths). **Caveat:** `legacy_repo_rw` still used for `argus_root` git workspace and some explicit artifact paths.

~~6. **Permission gate (`builder_execute`).**~~ Done — `evaluate_phase1_for_keys` in `invoke.py`; `project_permission_decision` on invoke record.

~~7. **Builder escalation emission.**~~ Done — deterministic rules post-reconcile; `builder_escalation_emit` on reconcile record; packets under `runs/escalations/latest/` when triggered.

~~8. **Explicit network policy + host readiness.**~~ Done — `network_mode` / `--network` / `ARGUS_BUILDER_NETWORK_MODE`; `argus builder doctor` (`argus.builder.host_readiness.v1`).

### Tier 1: Remaining safety / consistency (before *autonomous* Builder and broad rollout)

1. **Default network is open by design** — residual risk for operators who need outbound access; **`disabled`** mode exists. Further policy (allowlists) deferred.

2. **Legacy filesystem scope** — when `filesystem_scope_mode: legacy_repo_rw`, preventive mounts do not apply. **Mitigation in code:** **`argus_root_worktree` + `argus_root_worktree_scoped`** (worktree + scoped mounts + Linux Landlock when applied); plain **`argus_root`** still relies on reconcile enforcement.

3. **Unify containment policy surface.** Create a shared containment policy that Builder (`sandbox.py`), execution engine (`containment/policy.py`), and action execution (`execution/sandbox.py`) all reference. Shared env strip list, shared capability grants from `argus.policy.yaml`, composable enforcement. Current three-model split creates maintenance burden and inconsistency risk. Implementation: refactor into `argus/containment/unified.py` that the three current modules delegate to.

4. **Broader escalation triggers** — see Tier 2 item 8 (beyond current Builder escalation rules).

### Tier 2: Operational (Phase 2B / 2C — visibility + generalization)

5. **Additional contract kinds (incremental).** **`bug_fix`** and **`signal_instrumentation`** are **shipped** (`execution_contract.py`, `contract_registry.py`). Further kinds (e.g. `feature_implementation`, `dependency_update`) or richer templates remain **incremental** — not a reopening of “no non-story contracts.”

6. **~~Branch isolation for monorepo workspaces.~~** **Done** for **`argus_root_worktree`** via **`git_worktree_isolation.py`** (worktree under `runs/builder/worktrees/`). Plain **`argus_root`** still skips nested-style branch isolation — by design unless operators enable the worktree path.

7. **Builder execution history in dashboard.** **Partial:** Streamlit Overview includes multi-product Builder rows, per-product snapshot, persisted **`builder_activity`** expander, and `console_data` indexes the artifact. Remaining: richer per-run history / dedicated tab if desired. Implementation was: `console_data` + `argus/builder/multi_product_view.py` + `argus/portfolio/builder_activity.py`.

8. **Reconcile-to-escalation pipeline (beyond current Builder rules).** Escalate on: `containment_applied: none` in production, `trust_degraded_*` flags, `execution_outcome: blocked`, repeated `partial` outcomes for the same product, diff size exceeding threshold. Implementation: escalation rules in `run_builder_reconcile` or a post-reconcile hook.

9. **Unify import models.** Converge importer and GitHub onboarding into a single `clone → scaffold → bootstrap` pipeline. Implementation: refactor `github_onboarding.py` to delegate to a shared import core; deprecate the inconsistency.

10. **Fix creation driver coverage heuristic.** `_detect_gaps` in `creation.py` should actually count existing products per driver. Implementation: populate `driver_product_coverage` from inventory mission data.

### Tier 3: Vision (closing the loop)

11. **Builder → portfolio feedback (strategic).** A **minimal** **`argus.builder_outcome.v1`** per product (`runs/builder/outcome/<id>/latest.json`) plus summaries in **`builder_activity`** provides a **narrow, auditable** readout (conservative `attribution_status`, optional signal continuity). **Feeding `evaluate_portfolio_outcomes` and strategy** remains future work. **Distinct from** Phase 2B **`builder_activity`** coordination rollup alone — the outcome bridge is **Phase 3 entry**, not full Memory.

12. **Planning → Builder bridge.** Create a typed path from planning `recommended_actions` to Builder task candidates. Gate with explicit `builder_eligible: true` flag. Implementation: extend `planning/plan_actions.py` to emit Builder-compatible task specs.

13. **Defense-in-depth: Landlock filesystem restriction.** **Partially implemented (Linux):** `landlock_launcher` applies a **write-focused** allowlist after bwrap; see `builder_containment` **`landlock_*`** fields. **Remaining:** tighter read policy, optional seccomp, non-Linux parity (none — honest skip in artifacts).

14. **Defense-in-depth: seccomp-BPF syscall filtering.** Define a minimal syscall allowlist for the agent subprocess. Block dangerous syscalls (`mount`, `ptrace`, `pivot_root`, `unshare`). Bwrap supports `--seccomp` natively.

15. **Ongoing sync for imported repos.** Periodic pull from remote, re-evaluate signals/findings, detect drift. Implementation: extend `importer/replay.py` with a `sync-latest` mode.

16. **End-to-end validation harness.** Automated test: scaffold → git init → prepare → invoke with bwrap → verify containment artifact → verify scope check → verify branch review → verify escalation on deliberate breach → merge → verify portfolio outcome.

---

## 14. Recommended Next Validation Sequence

Validation steps for the remaining hardening work. Items marked ✓ are verifiable with current code; items marked ○ require implementation first.

**Containment verification (current code):**

1. ✓ **Bubblewrap sandbox active** — run `argus builder invoke --execute --backend agent` on a test product; verify invoke record shows `containment_applied: bwrap`, `trust_degraded_unsandboxed: false`.
2. ✓ **Env stripping works** — verify `sanitized_env_stripped_count > 0` in containment artifact; confirm `SSH_AUTH_SOCK`, `GITHUB_TOKEN`, `AWS_*` are in `stripped_keys_sample`.
3. ✓ **no_new_privs applied** — on Linux, verify `no_new_privs_applied: true`, `trust_degraded_missing_no_new_privs: false`.
4. ✓ **Branch isolation works** — verify invoke record shows `branch_isolation_status: ok`, `git_builder_branch` is set, changes stay on builder branch.
5. ✓ **Scope verification catches breach** — deliberately modify a file under `argus/` in a test; run reconcile; verify `builder_scope_check` shows `path_scope_breach: true`, `argus_core_breach: true`.
6. ✓ **Merge readiness gates correctly** — verify `builder_branch_review` shows `unsafe` on scope breach; `merge_candidate` only with bwrap + clean scope + successful outcome.
7. ✓ **Sandbox refusal without bwrap** — temporarily hide `bwrap` from PATH; verify Builder refuses to execute unless `--allow-unsandboxed` is passed.

**Remaining hardening (selective — much of this is now implemented):**

8. ✓ **Network policy** — verify `builder_containment.network_mode`; test `--network disabled` → argv contains `--unshare-net` / subprocess cannot reach network (environment permitting).
9. ✓ **Product-scoped mount (nested product)** — verify `filesystem_scope_mode: product_scoped` and `repo_root_mount_mode: read_only` on invoke when applicable; attempt write outside product subtree fails in sandbox.
10. ✓ **Permission gate** — set policy to deny `builder_execute`; verify invoke refuses with `project_permission_decision.execution_proceeds: false`.
11. ✓ **Escalation** — trigger a serious scope/trust condition; verify `builder_escalation_emit` and/or packet under `runs/escalations/latest/` per rules.

**End-to-end validation (requires multiple implementations):**

12. ○ **Full creation flow** — propose → scaffold → bootstrap → signals → Builder iteration → reconcile → portfolio outcome includes new product with correct lifecycle status.
13. ○ **Full import flow** — clone repo → scaffold → bootstrap → signals → verify product appears in portfolio with correct import state.
14. ◐ **Dashboard shows Builder state** — multi-product table, per-product snapshot, and persisted **`builder_activity`** expander exist; deeper per-run history still optional (**Phase 2B**).
15. ○ **Autonomous runner + Builder** — wire Builder invoke into the autonomous runner with all gates active; validate guardrails — **deferred** until portfolio feedback + contracts + UX catch up (see roadmap).

---

## 15. Security and Progress Roadmap (aligned with `docs/plans/ephemeral-roadmap.md`)

This section uses **letters A–D** for *this document’s* sequencing of safety vs capability. **`ephemeral-roadmap.md`** uses **Phase 2 / 2A / 2B / 2C** for Builder. **They are aligned**, not competing taxonomies:

| ephemeral-roadmap.md | This section | Meaning |
|----------------------|--------------|---------|
| **Phase 2 — Hands** | Preconditions for “operator Builder is real” | Delivered for local operator use |
| **Phase 2A — Seal the Hands** | **Phase A — Seal the sandbox** (same work) | Containment + policy + escalation + explicit network + doctor + **graded FS** (product-scoped, worktree-scoped, legacy) — **complete with refined caveats** |
| **Phase 2B — Operational Builder** | **Phase B** below | Visibility, dashboard, polish — **in progress** (incl. portfolio **`builder_activity`** rollup) |
| **Phase 2C — Generalize Hands** | Overlaps **Phase B/C** here | **Substantially complete with caveats** — registry + **`bug_fix`** / **`signal_instrumentation`** + **`set-target`** + worktree/scoped FS/Landlock; **incremental** extensions remain |
| Phases 3+ (Memory, Legs, …) | **Phase C/D** below | Portfolio feedback, planning bridge, autonomous Builder |

**“Seal the Hands”** and **“Seal the sandbox”** both mean: **outer envelope + policy + trust visibility** — not two separate engineering programs.

### Phase A — Seal the sandbox (= Phase 2A — Seal the Hands) — **status: complete with refined caveats**

*Goal: Operator-triggered Builder is **policy-aware**, **contained**, and **inspectable**.*

| Theme | Status |
|--------|--------|
| Bubblewrap + env strip + `no_new_privs` (Linux) + Landlock (Linux, when applicable) | Done |
| Product-scoped FS (when `product_scoped`) | Done |
| Argus-root worktree–scoped FS + worktree isolation (when `argus_root_worktree`) | Done (not parity with nested-product scoped) |
| Permission gate `builder_execute` | Done |
| Post-reconcile escalation (rules) | Done |
| Explicit network modes + `argus builder doctor` | Done |

**Caveats:** plain **`argus_root`** / **`legacy_repo_rw`** (graded vs worktree path); default network open by design; Linux-only **Landlock** / **`no_new_privs`**; autonomous runner **not** wired.

### Phase B — Operational + visibility (= Phase 2B + parts of 2C)

*Goal: **Phase 2B** dashboard/history/polish; **Phase 2C** contract + substrate generalization **largely shipped** (caveats: plain `argus_root`, hand-wired registry); **incremental** contract kinds ≠ Phase 2B.*

| Item | Notes |
|------|--------|
| Portfolio **`builder_activity`** artifact | `runs/portfolio/builder_activity/latest.json` — deterministic rollup; **not** Memory/outcomes (see roadmap Phase 3 boundary) |
| Dashboard Builder panel | Tier 2 §7; multi-product + snapshot + persisted rollup expander shipped |
| Contract templates beyond `content_slot` | **`bug_fix`** / **`signal_instrumentation`** **shipped**; Tier 2 §5 — **incremental** further kinds |
| Monorepo worktree / scoped FS / Landlock | Tier 2 §6 — **shipped** (polish optional) |
| Broader escalation triggers | Tier 2 §8 |
| Unified containment policy surface | Tier 1 §3 |

### Phase C — Portfolio feedback & planning bridge (ephemeral Phase 3–4)

*Goal: Builder outcomes and planning tie to portfolio reasoning — **not** required for “Hands are real.” **`builder_activity` (Phase 2B) is visibility/coordination only** — it does **not** satisfy Phase C.*

### Phase D — Defense-in-depth & autonomous Builder (ephemeral Phase 7+)

*Goal: Further syscall / read restrictions, seccomp, autonomous runner — **Landlock write layer is partially in place** (Linux); full defense-in-depth + autonomous Builder **explicitly later** than operator-scale Hands.*

---

### Relationship table (compact)

| Ephemeral roadmap | This doc (§15) |
|--------------------|----------------|
| Phase 2 Hands | Operator Builder delivered |
| Phase 2A Seal the Hands | Phase A — Seal the sandbox (**done w/ refined caveats**) |
| Phase 2B Operational Builder | Phase B (visibility; incl. **`builder_activity`**) |
| Phase 2C Generalize Hands | Phase B/C — **registry + non-story contracts + worktree/scoped substrate largely shipped**; **incremental** polish |
| Phase 3+ Memory / Legs / Autonomous | Phases C–D here |

---

*This document should be updated when autonomous Builder is wired, unified containment lands, or major Builder UX ships.*
