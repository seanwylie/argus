# Argus reference

This is the full command, layout, and configuration reference. The short entry point is the
root [`README.md`](../README.md).

---

## ELI5 (explain like I’m five)

Imagine you have **several LEGO sets** (your products) in one big box. Argus is the **helper that checks each set**: counts the pieces it can see, notes what’s missing or dusty, says “this one looks ready to build” or “this one needs more parts,” and writes **little report cards** you can read later. It does **not** build the LEGO for you unless you **say it’s okay** and the rules allow it.

In software terms: Argus **looks at your repo**, **writes down what it found** in files you can open (`runs/`), **suggests what to do next**, and keeps **safety switches** so nothing risky runs by surprise. It’s meant to be **boring in a good way** — the same steps tomorrow give the same structured output, so you can trust the paper trail.

---

## Argus vs “just use an LLM”

Chat models are great at **conversation, drafting, and brainstorming**. Argus is built for something different: **repeatable, portfolio-scale operations** on real product trees, with **evidence you can audit**.

| Dimension | Typical LLM chat | Argus |
|-----------|------------------|--------|
| **Grounding** | Answers from training + your prompt; easy to sound confident without citing your repo | Starts from **declared products**, **collected signals**, and **versioned artifacts** under `runs/` |
| **Repeatability** | Same question can get different answers | Core pipelines are **deterministic** where designed (same inputs → same structured outputs); optional LLM paths are **labeled** and **non-authoritative** for scoring when used |
| **Auditability** | Hard to prove *why* a recommendation appeared | **JSON bundles** with IDs, timestamps, and metadata (e.g. idea `grounding`, hygiene flags, quality threshold stats); you can diff runs and trace **signal → finding → decision → idea** |
| **Temporal honesty** | May hallucinate “up to date” | **Freshness** and **staleness** are first-class; interpretation is separated from “what the clock says” (see below) |
| **Risk & execution** | No built-in gate for “run this on my machine” | **Autonomy tiers**, **approvals**, **dry-runs**, and **execution** boundaries are explicit in the design |

**Audit** in Argus is not a vibe — it means **multi-angle, artifact-backed reviews** (`argus audit`, capability maps) that **reduce** confidence when coverage is stubbed or missing, and **structured ingest** so human or agent output can be reconciled with the same schema. You can answer: *what did we record, when, and under which rules?* That’s the differentiator: **inspectable state**, not just a clever paragraph.

---

## What Argus is

**Argus** is a **local-first operator system** for managing a **portfolio of software products** that live in this monorepo. It is not a single application you deploy once: it is a **shared layer of observation, evaluation, and (optionally) action** that treats each product as a **first-class node** with its own tree, scripts, and contract (`product.yaml`).

In concrete terms, Argus:

- **Collects signals** — filesystem checks, metrics snapshots, health heartbeats, temporal sidecars, and other adapters — and stores **canonical, inspectable JSON** under `runs/`.
- **Derives findings** — rule-based or heuristic interpretations of those signals (what looks wrong, thin, or worth attention).
- **Ranks decisions** — lifecycle-aware **decision candidates** with explicit confidence, uncertainty, and risk — not emotional language, but structured posture (“move forward”, “gather data”, “hold”, etc.).
- **Proposes ideas** — deterministic **structured ideas** (exploit / explore / invent) for what to try next, without automatically running experiments or shipping code.
- **Coordinates posture** — **orchestration state** reads durable artifacts and surfaces **eligibility**, **next actions**, and **staleness** so a human knows what is safe to run next.

Argus is designed to be **inspectable**: you can open `runs/` (or use the CLI / dashboard) and see **what was observed**, **what was inferred**, and **what was recommended**, with IDs and timestamps. LLMs and advisors may **enrich** sidecars or expansion text, but **canonical scoring and sorting** for ideas remain **deterministic** unless you opt into optional paths documented in the docs.

---

## Why Argus exists

Running several products in one repository creates a **coordination problem**: each tree has its own health, metrics, docs, and risk profile, but no single place to answer:

- What is the **current reality** of each product (signals, freshness)?
- What does that **mean** for prioritization (findings, decisions)?
- What should we **try next** (ideas, experiments) **without** pretending automation is always safe?

Argus exists to **close that loop locally** — evidence under `runs/`, clear handoffs, and **explicit gates** (autonomy tiers, approvals, execution dry-runs) so that “smarter” behavior never means **opaque** behavior. The long-term vision is a **credible closed loop** from signal to shipped outcome with **graduated autonomy**; today’s codebase mixes **fully implemented** flows with **stubbed or partial** capabilities. The honest split is documented in [`docs/stub-inventory.md`](docs/stub-inventory.md).

---

## How Argus works

### The spine (recommended mental model)

At a high level, data flows like this:

`product.yaml` → *(optional `doctrine.yaml` + `signals.yaml`)* → **signals** → **findings** → **decisions** → **ideas** → *(optional: experiments, advisors, simulation, planning, execution)*

**Orchestration** (`argus orchestration …`) sits **beside** this spine: it **reads** `runs/signals`, `runs/temporal`, `runs/findings`, `runs/decisions`, audit freshness, etc., and writes **per-product state** under `runs/orchestration/` so you can see **eligibility** and **what to refresh** without starting a long autonomous loop.

### Core principle: temporal vs interpretation

| Layer | Role |
|-------|------|
| **Observations** | Signals and temporal metadata describe **what was recorded** (with clocks: `observed_at`, collection recency). |
| **Interpretation** | Findings rules and advisors **infer** meaning; they are **not** ground truth for “production is fine.” |
| **Decisions** | Candidates combine lifecycle, strategy, freshness, and **decision context** (confidence / uncertainty / risk). |

**Rule:** Freshness tells you whether evidence is **current enough to trust**; interpretation tells you what it **might mean**. Decisions must not treat narrative alone as telemetry.

### Products vs Argus core

- **Products** (`products/`) are the things Argus observes. Each product is a bounded node with its own stack, scripts, and layout. Argus does not assume products are Python-only.
- **Argus core** (`argus/`) is the shared implementation: orchestration, audit, planning, decisions, lifecycle, signals, adapters, CLI. It should interact with products through **metadata and declared actions** (`product.yaml`), not by importing product code.

---

## Idea pipeline and quality (recent direction)

Structured ideas are written under `runs/ideas/` (see [`docs/idea-generation.md`](docs/idea-generation.md)). The pipeline has been strengthened to keep outputs **grounded and operator-trustworthy** without changing the core schema:

- **Signal hygiene** — Signal-derived ideas **down-rank** rows that look like manifest gaps, sentinels, or placeholders when hygiene metadata is present.
- **Mechanical cleanup** — Deterministic **deduplication**, diversity bucket caps, a **hard max** on list size, and a **mechanical rank score** that already folds in hygiene, **tone alignment**, and **synthesis grounding** multipliers.
- **Tone alignment** — When decision/findings tone metadata exists, ideas can get a **rank multiplier** so overstated rollout language does not float to the top without a record.
- **Synthesis grounding** — Synthesis (“invent”) ideas carry optional **`grounding`** metadata (`grounding_kind`, `grounding_strength`, `grounding_sources`) tied to findings, signal records, or `runs/` paths where available; **weak** lattice-only or combinatorial-only rows are **down-ranked** in both mechanical selection and final **`rank_key`** sort.
- **Quality threshold** — After the first idea in a batch, additional ideas must clear a **minimum mechanical score** relative to the **batch-best** score (stricter for **weak synthesis fillers**). The bundle can end **below** the configured max when the tail would only add weak rows; **`meta`** records `stopped_early_for_quality` and `rejected_below_quality_threshold` when applicable.

Optional **LLM expansion** of ideas remains **additive** (canonical title/description for scoring unchanged). See [`docs/llm-integration.md`](docs/llm-integration.md).

### Freshness and plain language

Orchestration and portfolio tooling can attach **plain-language freshness explanations** (for example in portfolio priorities and dashboard/ELI5 surfaces) so operators see **why** something is stale or blocked — derived from the same durable artifacts, not ad hoc prose.

---

## Quick context (agents / new contributors)

**[`docs/argus-context/README.md`](docs/argus-context/README.md)** — **north-star**, **open gaps**, **operating principles**; then the full **`docs/`** tree for architecture, contracts, and CLI.

See **[docs/architecture.md](docs/architecture.md)** for subsystem map, **[docs/system-flow.md](docs/system-flow.md)** for the operator loop, **[docs/model-contracts.md](docs/model-contracts.md)** for IDs, canonical types, and **shared vocabulary** (confidence vs novelty, etc.), **[docs/temporal-intelligence.md](docs/temporal-intelligence.md)** for freshness vs interpretation, **[docs/decision-confidence.md](docs/decision-confidence.md)** for decision dynamics (not emotions), **[docs/idea-generation.md](docs/idea-generation.md)** for structured ideas, **[docs/proof-run.md](docs/proof-run.md)** for a repeatable local proof checklist, **[docs/stub-inventory.md](docs/stub-inventory.md)** for intentional stubs, and **[docs/autonomy.md](docs/autonomy.md)** / **[docs/autonomy-rollout.md](docs/autonomy-rollout.md)** / **[docs/execution.md](docs/execution.md)** / **[docs/doctrine.md](docs/doctrine.md)** for safety and policy layers.

GitHub Actions CI runs the test suite (`pytest`) on pushes and pull requests to `main` and `master`.

Security reporting and scope: see [SECURITY.md](SECURITY.md).

---

## CLI

The console entry point is **`argus`** ([`pyproject.toml`](pyproject.toml) → `project.scripts`).

```bash
# With uv (recommended)
uv sync              # use `uv sync --group dev` if you need Ruff or pytest (see Development)
uv run argus --help

# Or editable install
pip install -e .
argus --help
```

### Command groups

| Group | Purpose |
|-------|---------|
| `argus products` | **Create** scaffolds, discover, validate, and show `products/*/product.yaml` |
| `argus signals` | Collect, manifest-driven canonicalization, **reality**, optional **cursor-prompt** / **cursor-ingest** (review sidecar), **ingest business snapshots** (local JSON/CSV), inspect (`runs/signals/`) |
| `argus findings` | Rule-based findings from signals (`runs/findings/`) |
| `argus decisions` | Lifecycle-weighted candidates and cross-product portfolio ranking (`runs/decisions/`) |
| `argus lifecycle` | Lifecycle assessment, **kill-score**, **report** (read-only; see `--help`) |
| `argus portfolio` | **Portfolio refresh** (full local pipeline) and **show** last summary |
| `argus history` | **Snapshot** / **diff** / **product** timeline under `runs/history/` |
| `argus trends` | **analyze** / **summary** / **drift** from snapshot history (`runs/trends/`) |
| `argus temporal` | **ingest** / **adapters** / **show** / **freshness** / **summary** / **refresh** — snapshot ingest + derived recency (`runs/temporal/`; [docs/temporal.md](docs/temporal.md), [docs/temporal-intelligence.md](docs/temporal-intelligence.md)) |
| `argus planning` | **weekly** operating synthesis (`runs/planning/`) |
| `argus input` | Structured human input (constraints, strategy notes) under `runs/input/` |
| `argus strategy` | **set** / **show** operating strategy (`runs/strategy/current.json`) |
| `argus experiments` | Create/list/update experiments; **propose** / **rank** suggestions; **evaluate** (`runs/experiments/`) |
| `argus ideas` | **generate** / **list** — structured ideas from signals, findings, synthesis, mutation (`runs/ideas/`); includes hygiene, grounding, and quality thresholding (see above) |
| `argus orchestration` | **state** / **advance** / **run-progression** / **cursor-prompt** / **cursor-ingest** — durable posture from `runs/*`, eligibility, next steps (`runs/orchestration/`); portfolio priorities / trends generators where configured |
| `argus simulate` | **Deterministic** best / expected / worst preview for a product or `--experiment` |
| `argus advisors` | Run advisor archetypes and **consensus** (`runs/advisors/`); LLM optional |
| `argus self` | Self-audit / meta-review (`argus self audit`, stdout or `--json`) |
| `argus doctrine` | Show parsed `products/<id>/doctrine.yaml` |
| `argus autonomy` | **show** / **status** / **set** / **set-tier** / **explain** / **policy**; spawn/run/scheduler (`runs/autonomy/`); bounded tiers + guardrails — [docs/autonomy-rollout.md](docs/autonomy-rollout.md) |
| `argus approval` | **list** / **request** / **approve** / **reject** — execution gate (`runs/approval/`) |
| `argus capabilities` | List / **evaluate** / **gaps** / **request** / **`resume`** (`runs/capabilities/`) |
| `argus economics` | **analyze** / **portfolio** / **resources** (registry, cost ingest, orphan + high-cost/low-value) (`runs/economics/`) |
| `argus actions` | **validate** / **dry-run** / **show** action contracts (no execution) |
| `argus execution` | **dry-run** (validate + sandbox), **run** / **show** (`runs/execution/`; opt-in; cwd restricted) |
| `argus escalation` | **Generate / list / show** halt-and-handoff packets (`runs/escalations/`) |
| `argus adapters` | Adapter layer: **list** / **run** — collect → normalize → `SignalRecord` ([docs/adapters.md](docs/adapters.md)) |
| `argus validate` | **`artifacts`** — JSON shape checks under `runs/` (optional `runs/loop/<run_id>` for manifest/summary) |
| `argus loop` | **`run`** — **four** stages (discover → signals → findings → decisions; planning/escalation **separate**). **`full`** — extended harness **discovery through dashboard** (16 stages; incl. **ideas** + planning/actions + execution dry-run; **not** simulation or portfolio read—use `argus simulate` / `argus portfolio` separately; escalation **separate**, see `summary.json` `chain`). [docs/loop-harness.md](docs/loop-harness.md). Artifacts: `runs/loop/<run_id>/` |
| `argus doctor` | Manifests, staleness, doctrine, autonomy, approvals, capability requests, portfolio vs findings, trends, economics; JSON **`spine`** (adapter config, loop manifest, signals/latest parse, escalation chain hint) |
| `argus dashboard` | **Generate static HTML** from `runs/` (`--open` in browser) |
| `argus scan` | Legacy: print product IDs only (prefer `argus products list`) |
| `argus audit` | **`run`**, **`prompt`** / **`cursor-prompt`**, **`ingest-agent`** — multi-angle bundle under `runs/audit/` ([docs/audit-architecture.md](docs/audit-architecture.md)) |
| `argus refine` | Staged refinement (drafts, council, convergence) under `runs/refinement/` ([docs/artifact-refinement.md](docs/artifact-refinement.md)) |

Shared flag where applicable: `--products-dir` to override `<repo>/products`.

### New product scaffold

```bash
uv run argus products create my-app --type micro_saas
```

Creates `products/<slug>/` with `product.yaml`, `app/`, `scripts/*.sh`, `config/`, `metrics/`, and `README.md`. Templates: `content_stream`, `micro_saas`, `static_site`, `utility_api`, `mobile_companion`. Use `--force` to replace an existing directory.

Then **`uv run argus products bootstrap <slug>`** adds `doctrine.yaml`, metrics placeholders, and `experiments/seed.json` (filesystem only; see `docs/analytics-integrations.md`).

### One-shot pipeline

```bash
uv run argus portfolio refresh
```

This validates products, collects signals, generates findings and decisions, writes the decisions portfolio JSON, and emits a human summary plus `refresh.json` under `runs/portfolio/latest/`. Use `--per-product` for per-product text files. See [`argus/cli/README.md`](argus/cli/README.md) for details.

### Follow-on commands (same repo)

```bash
uv run argus trends summary
uv run argus experiments propose
uv run argus experiments rank
uv run argus portfolio allocate
uv run argus planning weekly
uv run argus dashboard
uv run argus doctor
```

Topic guides: [experiments](docs/experiments.md), [advisors](docs/advisors.md), [simulation](docs/simulation.md), [portfolio allocation](docs/portfolio.md).

---

## Development

Install optional dev dependencies (Ruff, pytest):

```bash
uv sync --group dev
```

Lint (`argus/` and `tests/` only, per `pyproject.toml`):

```bash
uv run ruff check .
```

Apply safe auto-fixes when needed:

```bash
uv run ruff check . --fix
```

Format with Ruff’s formatter (optional; a first run may touch many files until the tree is normalized):

```bash
uv run ruff format .
```

Run the test suite:

```bash
uv run pytest -q
```

Pytest is scoped to **`tests/`** at the repo root (`pyproject.toml` **`testpaths`**) so product trees under **`products/`** (which may ship their own tests and dependencies) do not break **`uv run pytest`**.

**Before you commit:** run `git status` and treat **modified/untracked** `argus/**`, `tests/**`, `docs/**`, and root `README.md` / `.gitignore` as source; **do not** commit contents under `runs/` except the tracked **`runs/README.md`** (other paths there are gitignored local output). Stage new modules explicitly (e.g. untracked `argus/**` or `tests/**` after a feature wave).

Optional coverage (terminal report with missing lines; dev deps include `pytest-cov`):

```bash
uv run pytest --cov=argus --cov-report=term-missing
```

---

## Repository layout

| Path | Role |
|------|------|
| `products/` | Product nodes; each has a single machine-readable entry point `product.yaml` (and optional `signals.yaml`, `doctrine.yaml`) |
| `argus/` | Core packages (orchestrator, audit, planning, decision, lifecycle, signals, adapters, cli). **`argus/planner/`** is a deprecated shim — import **`argus/planning/`** (or CLI `argus planning`). |
| `shared/` | Cross-cutting utilities (logging, schemas, config helpers, utils) — not product-specific |
| `runs/` | Local pipeline outputs (most subdirs are **gitignored** — see `.gitignore`; includes signals, temporal, findings, decisions, orchestration, loop, audit, refinement, ideas, portfolio, …). Inspect via CLI or keep copies of bundles you need to version. **Bounded retention** trims timestamped signal collections and Builder work-order stamped JSON by default (see **`runs/README.md`**). |
| `docs/` | Doctrine, architecture ([architecture.md](docs/architecture.md)), and operating docs ([object model](docs/argus-object-model.md)) |

---

## Product discovery

Argus discovers products by scanning:

`products/*/product.yaml`

Each `product.yaml` is the contract for that node’s identity, metrics hooks, signals, actions, and lifecycle hints.

---

## Status

The **canonical domain model** (products, signals, findings, decisions, runs) lives in `argus/core/models/` and is documented in `docs/argus-object-model.md`. **Local staged orchestration** exists for analysis (`argus loop run`, `argus portfolio refresh`, **`argus orchestration state`**, and related commands writing under `runs/`). **Remote fleet execution, cloud schedulers, and automation outside this repo** are out of scope for the core CLI—the default remains inspectable, local artifacts.

**What is implemented vs stubbed:** see **[docs/stub-inventory.md](docs/stub-inventory.md)** for a categorized inventory (empty `adapters/` and `shared/*` placeholders, scaffold scripts, optional advisor LLM, and more). **Orchestrator boundary:** **`argus loop run`** does **not** include planning or escalation—use **`argus planning weekly`**, **`argus planning actions`**, and **`argus escalation generate`** when needed ([`docs/system-flow.md`](docs/system-flow.md)). **`argus loop full`** chains many stages (see [docs/loop-harness.md](docs/loop-harness.md)) but still omits escalation; validate JSON with **`argus validate artifacts`**. See also [`argus/orchestrator/README.md`](argus/orchestrator/README.md) and [`docs/architecture.md`](docs/architecture.md).
