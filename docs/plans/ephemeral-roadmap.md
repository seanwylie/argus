# 🧭 **Argus Unified Roadmap (vCurrent)**

This replaces the ephemeral roadmap as the **active roadmap**, while keeping its spirit.

---

## 📍 Current position (checkpoint — code-grounded)

| Track | Status |
| ----- | ------ |
| **Eyes (Phase 1)** | Partial — signals and adapters are real; depth and portfolio influence still uneven |
| **Hands (Phase 2)** | **Delivered** for **operator-triggered, local** Builder use — prepare → invoke → reconcile → status/review/merge are operational |
| **Seal the Hands (Phase 2A)** | **Complete with refined caveats** — outer sandbox, policy gate, escalation, explicit FS/network modes, host readiness are in code. **Caveats are graded:** plain **`argus_root`** (no nested product git) remains **degraded**; **`argus_root_worktree` + `argus_root_worktree_scoped`** is **materially stronger** (worktree + scoped mounts + Linux Landlock when applied). **Non–content_slot** contracts and **autonomous** Builder remain out of scope for 2A. |
| **Operational Builder (Phase 2B)** | **Active / in progress** — visibility, UX, dashboard, portfolio **`builder_activity`** (partial; see below) |
| **Generalize Hands (Phase 2C)** | **Substantially complete with caveats** — minimal **contract registry**, **`bug_fix`** and **`signal_instrumentation`** contracts with reconcile outcomes, **`argus builder set-target`** for manual non-story targets, Argus-root **`git worktree`** + **`argus_root_worktree_scoped`** (bwrap + Linux Landlock when applicable), merge-readiness and containment fields. **Caveats:** plain **`argus_root`** remains degraded vs nested / worktree-scoped paths; non-story flows are **operator-usable** but **not** an unbounded plugin surface; extra contract kinds and polish are **incremental**. **Portfolio memory / learning / strategy attribution** and **autonomous Builder** stay **out of scope** for 2C. |
| **Next emphasis** | **2B** (visibility, history, dashboard, operator UX) **in parallel** with **incremental** 2C follow-ups (extra kinds, ergonomics) — **2C** is **not** “substrate only”; **2B** does **not** subsume contract/substrate work already shipped. |

**Terminology:** **Phase 2A — “Seal the Hands”** and the architecture doc’s **“Phase A — Seal the sandbox”** refer to the **same hardening track** (outer sandbox + policy + visibility of trust). “Seal the sandbox” emphasizes the bubblewrap envelope; “Seal the Hands” emphasizes Builder as the execution limb. They are not parallel competing programs.

---

## 🧠 Phase 1 — **Eyes (Real Signals)**

**Goal:** Argus sees reality, not file snapshots.

**State:** *Partially complete, but still shallow*

### Capabilities

- API-based signal ingestion (Stripe, GA4, PostHog, AWS)
- Temporal feeds (market, trends, competitors)
- Credential system (externalized secrets)
- Scheduled adapters in runner

### Why it matters

Everything downstream depends on signal quality.

### Gate to move forward

- At least one product has **live signals flowing continuously**
- Signals materially influence findings/decisions (not just present)

---

## ✋ Phase 2 — **Hands (Builder Execution)**

**Goal:** Argus can safely modify code under operator control.

**State:** *Delivered for local, operator-scale use* — not portfolio-autonomous; integration with the autonomous runner remains **out of scope** until later phases.

### What exists

- Builder execution loop (prepare → invoke → reconcile)
- Execution contracts — **`content_slot`** is the most exercised template; **`bug_fix`** and **`signal_instrumentation`** are registered (`contract_registry.py`) with dedicated outcomes and **`set-target`** ergonomics; further kinds remain **incremental**, not unbounded
- Git baseline + diff capture
- Branch isolation: **nested product git** (`builder/…` branches); **Argus monorepo** via **`argus_root_worktree`** + worktree path (Phase **2C** substrate — see Phase 2C section)
- Scope enforcement (path + semantic) in reconcile
- Execution outcome classification
- Merge readiness model (`merge_candidate` / `review_required` / …)
- Local merge workflow (`argus builder merge`)
- **Containment:** `bwrap`, stripped env, `no_new_privs` (Linux), **Landlock** write allowlist inside bwrap when applicable (Linux; skipped for **`legacy_repo_rw`**), product-scoped or worktree-scoped filesystem mounts when those modes apply, **explicit network modes** (default / allow_all / disabled)
- Permission gate on invoke (`builder_execute`)
- Escalation emission on serious reconcile/invoke signals
- **`argus builder doctor`** (host readiness)
- Trust degradation + audit artifacts on invoke/reconcile

👉 This is a **real subsystem** for **operators who invoke Builder deliberately** — not a sketch.

---

## 🔒 Phase 2A — **Seal the Hands (Critical Hardening)**

**Goal:** Make operator-triggered Builder **trustworthy enough to use routinely**, with honest limits documented.

**State:** *Complete **with caveats*** — the checklist below is **done in code** where noted; remaining gaps are **called out explicitly** (they roll into 2B / 2C or later rather than blocking the “sealed” label).

### Delivered (implemented)

| Item | Notes |
|------|--------|
| **Product-scoped filesystem** | Repo root read-only + `products/<id>/` (and related paths) read-write when **`filesystem_scope_mode: product_scoped`** applies. **Caveat (graded):** **Plain `argus_root`** (no nested product `.git`) or explicit artifacts outside the product dir still use **`legacy_repo_rw`** — wide RW bind. **`argus_root_worktree` + `argus_root_worktree_scoped`** uses RO main checkout + RW worktree + RW main **`.git`** — **not** identical to product-scoped, but **narrower** than legacy full-repo RW on the primary checkout. |
| **Permission gate** | **`builder_execute`** evaluated via `project_permissions/gate.py` before agent/Cursor execute. |
| **Escalation** | Deterministic **Builder escalation** after reconcile (see `builder_escalation_emit`); not “silent breach only in JSON.” |
| **Host readiness** | **`argus builder doctor`** — static PATH/env/tooling signals. |
| **Explicit network policy** | **`network_mode`** on containment; **`--network`** / **`ARGUS_BUILDER_NETWORK_MODE`**; optional **`disabled`** → `bwrap --unshare-net`. Default allows network by design (not a hidden gap). |

### Caveats (not “Seal” blockers; tracked in 2B / 2C)

- **Autonomous / unattended** Builder is still **not** the target of Phase 2A — the autonomous runner does **not** invoke Builder.
- **Monorepo / Argus-root git is graded, not uniformly weak:** **Plain `argus_root`** (no worktree) — no per-product branch isolation; **`filesystem_scope_mode`** tends to **`legacy_repo_rw`**; trust is **degraded**. **`argus_root_worktree`** with **`argus_root_worktree_scoped`** — dedicated **`git worktree`**, scoped bwrap mounts, optional **Linux Landlock**; **stronger** than plain monorepo-root, still **not** nested-product **`product_scoped`** parity.
- **Linux-only seatbelts:** `no_new_privs` and **Landlock** apply on Linux; other platforms record honest skip/degradation in artifacts.
- **Open-ended** contract diversity (many task types, plugin-style extensibility) remains **incremental** after 2C — **not** the same as “no non-story contracts yet.”
- **Dashboard / execution history UX** = Phase **2B**, not 2A.

### Gate to move forward (satisfied for operator Builder)

- Execution is **contained** (with recorded exceptions), **policy-gated**, **escalation-aware**, and **filesystem policy is explicit** in artifacts — including network and FS scope modes.

---

## 🛠 Phase 2B — **Operational Builder**

**Goal:** Builder is a **reliable operator tool** — visibility and polish, not new core execution semantics.

### Portfolio-facing visibility (late 2B) — **`builder_activity`**

The smallest **honest, non-alarm** Builder → portfolio slice is **`runs/portfolio/builder_activity/latest.json`** (stamped copies + `latest.md`), emitted by **`argus portfolio builder-activity`**. The same artifact is **refreshed** when you run **`argus dashboard summary`** (observational operator reporting — **not** outcomes/strategy/autonomous loops). It rolls up **deterministic** fields from existing invoke/reconcile/status truth (merge readiness, execution outcome, scope breach flags, escalation pointers, paths to source artifacts). The Streamlit Overview tab can show the persisted rollup.

**Two paths — do not confuse them:**

| Path | Role |
|------|------|
| **Alarm** | Serious reconcile/invoke signals → **`builder_escalation_emit`** → packets under **`runs/escalations/latest/`** → escalation inbox (operator attention) |
| **Non-alarm portfolio** | Routine visibility → **`builder_activity`** — coordination/visibility only; **no** scoring, **no** “Builder helped,” **no** strategy attribution |

**What `builder_activity` is *not* (by design):** portfolio **outcomes**, **learning**, **memory**, causal claims, or product-success measurement. Those belong to **Phase 3 — Memory** and related portfolio machinery — **not** this artifact.

### Add (ongoing)

- Dashboard Builder panel (or equivalent surfacing) — partial: multi-product table + single-product snapshot + persisted rollup expander exist
- Execution history visibility (deeper history still optional)
- Clear trust signals (sandbox, NNP, scope, network mode, review) without reading raw JSON
- Manual workflow polish (merge + cleanup + review clarity)

### Gate to move forward

- Operator can **understand what happened** and **trust labels** without spelunking `runs/builder/**/latest.json` for every run.

---

## 🧩 Phase 2C — **Generalize Hands**

**Goal:** Structural and operational generalization — **real** contract and substrate paths beyond story-slot-only / nested-git-only assumptions — **without** claiming universal coverage or autonomy readiness.

**State:** **Substantially complete with caveats** (not “exploratory only”). **2B** = visibility / operator UX / dashboard / history; **2C** = **contract + substrate + registry + manual-target ergonomics** — orthogonal tracks.

### In code (2C — shipped; caveats below)

- **Contracts:** Minimal **registry** (`contract_registry.py`); **`bug_fix`** and **`signal_instrumentation`** (`execution_contract.py`, outcome modules); **`argus builder set-target`** (`set_manual_target.py`) for non-story targets without hand-editing JSON
- **Substrate:** Argus-root **`git worktree`** isolation (`git_worktree_isolation.py`); **`argus_root_worktree_scoped`** bwrap + **Linux Landlock** when applicable; merge-readiness + **`builder_containment`** fields; honest degradation for plain **`argus_root`** and non-Linux platforms

### Caveats (intentionally kept)

- **Plain `argus_root`** (no worktree): still **weaker** than nested **`product_scoped`** or **`argus_root_worktree`** + scoped modes — see architecture doc
- **Linux-only** seatbelts: `no_new_privs`, Landlock — recorded skip/degradation elsewhere
- **Non-story** paths: **operator-usable**, still **somewhat hand-wired** — **registry** dispatches contract builders and reconcile outcomes; **prepare** (`next_expansion_prepare.py`) owns markdown templates; **not** a plugin marketplace
- **Additional** contract kinds / vacuous-scope tightening: **incremental** — not “finished forever”

### Gate (core 2C)

- Satisfied for **operator-triggered** local use: Builder is **not** story-slot-only; monorepo has a **real** isolation path when enabled; **portfolio memory / learning / strategy attribution** and **autonomous Builder** remain **deferred** (Phase 3+).

---

## 🔄 Phase 3 — **Memory (Close the Loop)**

**Goal:** Argus learns from what it builds.

This is the most important missing piece.

**Boundary:** **Phase 2B `builder_activity`** is **not** Phase 3 Memory. It is a **read-only coordination rollup** of Builder truth at a point in time. Phase 3 Memory (here) means **feedback into outcomes, strategy, and decisions** — attribution, deltas, and closed-loop learning — which **`builder_activity` explicitly does not do**.

### Minimal bridge (started — narrow, auditable)

**`argus.builder_outcome.v1`** — per-product JSON under **`runs/builder/outcome/<product_id>/latest.json`**, emitted when **`argus portfolio builder-activity`** (or dashboard summary refresh that writes builder activity) runs. Derived only from existing Builder status + optional **`runs/signals/latest/<product>.json`** continuity. Includes **`attribution_status`** (`not_enough_data` | `no_material_change_detected` | `possible_positive_signal_change` | `possible_negative_signal_change`) and a **non-causal** `delta_summary`. **`comparison_provenance`** records what window/basis was used for that readout (`comparison_window_status` such as `unavailable` | `latest_only` | `continuity_based` | `bounded_recent_window`, plus `comparison_basis`, `comparison_window_note`, and **`compared_artifact_refs`**). **Phase 3B (narrow):** **`observation_timing`** compares **artifact timestamps** only (latest Builder invoke/reconcile clock vs signals bundle `collected_at_utc`) with a small `observation_timing_status` vocabulary — not real-world latency or causality. **`recent_observation_summary`** rolls up the current outcome plus up to four prior minimal rows from **`history_tail.json`** (updated when outcomes are written), with a deterministic **`recent_observation_pattern`** label — not Builder scoring. Summaries in **`builder_outcome_summaries`** include **`outcome_compact_line`**, **`outcome_operator_one_liner`** (adds timing + short recent pattern), **`comparison_evidence_strength`** (`none` | `weak` | `moderate`), and **`outcome_disclaimer_short`**. The same bridge is **surfaced in operator-facing surfaces** without new UI: operator summary JSON/markdown (preview line after the Builder rollup header, outcome text on **recent activity** rows, dedicated Builder outcome subsection) and **`runs/portfolio/builder_activity/latest.md`** (Phase 3 line under each product). This is **not** broad memory, strategy mutation, portfolio ranking, or autonomous reasoning — it answers a modest observational question: *what do artifacts suggest changed (if anything), how do artifact clocks line up, and is there a short repeat shape in recent outcomes?* **`argus.builder_outcome_validation.v1`** under **`runs/portfolio/builder_outcome_validation/latest.json`** aggregates counts and short product lists across on-disk outcomes for **calibration** (embedded in operator summary JSON/markdown when present) — still not effectiveness scoring. A small deterministic **interpretation** block (**`validation_headline`**, **`validation_interpretation_lines`**, **`validation_attention_level`**) summarizes aggregate shape for operators without claiming product quality or Builder success.

### Add (broader — still future)

#### 1. Deeper Builder outcome use

- richer signal delta (before/after)
- tighter integration with **`evaluate_portfolio_outcomes`**

#### 2. Portfolio feedback integration

- outcomes influence:
  - strategy
  - decisions
  - planning

### Why this matters

Right now:

> Builder changes code

But Argus cannot fully answer:

> did that help?

### Gate to move forward

- Builder execution affects:
  - portfolio outcomes
  - strategy adjustments
  - future decisions

---

## 🧭 Phase 4 — **Bridge Planning → Builder**

**Goal:** Argus chooses what Builder should execute.

### Add

- Typed bridge:
  - planning → builder tasks
- Explicit flag:
  - `builder_eligible: true`

### Important constraint

Builder must remain:

> **mechanical executor, not decision-maker**

### Gate to move forward

- No manual “pick next task”
- Planning can nominate executable work safely

---

## 🚀 Phase 5 — **Legs (Deployment & Infra)**

**Goal:** Argus can deploy what it builds.

### Add

- Infrastructure scaffolding (CDK/Terraform)
- Deployment pipelines
- Environment management (staging/prod)
- Cost signals feeding back

### Gate to move forward

- Build → deploy → observe loop is real

---

## 🧰 Phase 6 — **Tools (External Services)**

**Goal:** Products use real-world services.

### Add

- Service registry
- SDK integration patterns
- Credential provisioning via escalation

### Gate to move forward

- Products are not toy apps
- They can:
  - charge money
  - call APIs
  - serve users

---

## 🤖 Phase 7 — **Autonomous Builder (Controlled)**

**Goal:** Builder enters the autonomous loop safely — **still bounded, gated, auditable**.

### Prerequisites (all must be true; several are **post–2A** product work)

- Sandboxed execution (**done** for operator path)
- Product-scoped mounts where applicable (**done** with caveats); monorepo **worktree + scoped FS** partially addressed (**2C** substrate — not full “universal coverage”)
- Permission gate (**done**)
- Escalation integration (**done** for defined rules)
- Explicit network policy (**done**; default open is intentional)
- Broader contract diversity than today’s registry (**incremental** beyond 2C core; not a blank slate)
- Portfolio feedback loop (**Phase 3**)

### Only then

- Autonomous runner can invoke Builder
- Still bounded
- Still gated
- Still auditable

**Do not conflate** “Phase 2 delivered” with “autonomous Builder ready.”

---

## 🌍 Phase 8 — **Scale (Portfolio Organization)**

**Goal:** Argus behaves like an autonomous product org.

- Multi-product execution
- Cross-product learning
- Resource allocation
- Lifecycle optimization

---

# 🧠 The Key Insight

This is the unification:


| Old Ephemeral      | New Reality |
| ------------------ | ----------- |
| Hands = future     | Hands = **real for operator-triggered local use** |
| Seal = hypothetical | Seal the Hands = **largely done** (caveats **refined** — graded monorepo trust, not “monorepo = no isolation”) |
| Build capability   | Build + contain + verify + **record** (network, FS scope, Landlock on Linux, policy) |
| Focus on execution | Focus on **operator visibility (2B)** + **incremental** contract/ergonomics polish; **2C** structural deliverables (registry, non-story contracts, worktree/scoped substrate) are **largely in code** (caveats above) |

