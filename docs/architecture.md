# Argus architecture

## Mental model

Argus treats each product under `products/<id>/` as a **node** described by `product.yaml`. The core loop is:

**product.yaml → (optional doctrine.yaml) → signals → findings → decisions → (optional ideas) → experiments → advisors → simulation → portfolio allocation → planning → (optional actions → approval → execution)**

Human **input** (constraints, strategy notes) and **strategy mode** (`runs/strategy/current.json`) nudge interpretation, **decision priority**, and **experiment ranking**. Strategy also sets deterministic **uncertainty posture**: freshness confidence factors when operational signals are missing/stale, and **leap-of-faith** damp/lift for launch-like work (see `argus.strategy.modes.StrategyProfile`, `argus.strategy.uncertainty`, `argus.decision.freshness`). Optional **doctrine** (`products/<id>/doctrine.yaml`) adds findings and scoring nudges. **Autonomy** (`runs/autonomy/`) caps execution by mode before subprocesses run. **Capabilities** track gaps when execution or features are blocked. **Self-audit** (`argus self`) meta-reviews repo health (see [model-contracts.md](model-contracts.md)).

Nothing in Argus executes shell commands against products by default; the CLI generates **inspectable artifacts** under `runs/`. Execution is **opt-in** (`argus execution`, `argus actions execute`) and gated by validation, autonomy, and approval.

### What is real vs stubbed

- **Real (fully implemented in-repo)** — Product discovery/validation, signals → findings → decisions, lifecycle, portfolio refresh, history/trends, **`argus planning weekly`** (writes `runs/planning/`), escalation **CLI** (packets under `runs/escalations/`), experiments, advisors (deterministic + optional LLM), simulation, economics, doctrine, autonomy policy, approval/execution gates, capabilities registry/requests, dashboard render, **`argus.adapters`** (pipeline, registry, built-in adapters; optional merge into signal collection — [adapters.md](adapters.md)), **`argus audit`** (multi-angle bundle under `runs/audit/`), `argus doctor`, `argus self` audit.
- **Stubbed or deferred** — **Advisors** default to deterministic stubs when no LLM. **Product scaffold** scripts are **local-safe no-ops** (`exit 0`, no network) until replaced. There is no published JSON Schema / OpenAPI tree yet (runtime types live in `argus/core/models/`). **Autonomy shutdown** includes **`resource_cleanup_stub`** / **`plan_provider_cleanup`** (no cloud teardown; structured seam). **Capabilities** registry is an awareness scaffold (not auto-implementation). Planning and escalation are **real** via **`argus planning`** and **`argus escalation`**; they are **outside** **`argus loop run`** by design (see [system-flow.md](system-flow.md)). Classic **`argus signals collect`** and the adapter layer are both documented in **[signals-and-adapters.md](signals-and-adapters.md)** alongside **[adapters.md](adapters.md)**.

Authoritative list: **[stub-inventory.md](stub-inventory.md)** (categories, files, priorities). Optional **`# ARGUS-STUB:…`** comments in code point to that doc.

## Subsystem map

| Area | Package / entry | Role |
|------|-------------------|------|
| Products | `argus.products`, `argus products` | Discovery, validation, scaffold |
| Doctrine | `argus.doctrine`, `argus doctrine` | Optional `doctrine.yaml`; findings + scoring nudges |
| Autonomy | `argus.autonomy`, `argus autonomy` | **Tiers 0–4**, `AutonomyPolicy` guardrails (actions/cost/spawn/experiment/**shutdown**/optional **min_confidence**), action matrix + **`explain`**, policy precedence (`policy_resolution`), quotas; enforcement before execution — [autonomy-rollout.md](autonomy-rollout.md) |
| Approval | `argus.approval`, `argus approval` | Human approval records for gated execution |
| Signals | `argus.signals`, `argus signals` | Adapters → `SignalRecord` → `runs/signals/` (includes **execution** feedback from `runs/execution/`) |
| Adapter layer | `argus.adapters`, `argus adapters` | Optional **collect → normalize → to_signal_records** pipeline and registry over classic `SignalAdapter`s; see [adapters.md](adapters.md) |
| Temporal | `argus.temporal`, `argus temporal` | Derived **freshness** over collections (`runs/temporal/`); snapshot ingest + CLI — [temporal.md](temporal.md), [temporal-intelligence.md](temporal-intelligence.md) |
| Findings | `argus.findings`, `argus findings` | Rules → `Finding` → `runs/findings/` |
| Lifecycle | `argus.lifecycle`, `argus lifecycle` | Stage + evidence → `LifecycleAssessment`, kill posture |
| Decisions | `argus.decision`, `argus decisions` | Candidates, priority, `runs/decisions/` |
| Ideas | `argus.idea_generation`, `argus ideas` | Structured ideas, selection, diversity metadata ([idea-generation.md](idea-generation.md)); `runs/ideas/` |
| Decision assessment | `argus.decision_assessment`, `argus confidence` | Confidence / uncertainty / risk / escalation pressure from local artifacts ([decision-confidence.md](decision-confidence.md)); `runs/decision_assessment/latest/` |
| Strategy | `argus.strategy`, `argus strategy` | Persisted mode → scoring weights, kill thresholds, freshness factors, leap-of-faith tuning |
| Portfolio | `argus.portfolio`, `argus portfolio` | Refresh pipeline, cross-product ranking |
| History | `argus.history`, `argus history` | Timestamped snapshots under `runs/history/` |
| Trends | `argus.trends`, `argus trends` | Drift rules over snapshot history |
| Planning | `argus.planning`, `argus planning` | Weekly synthesis → `runs/planning/` |
| ~~`argus.planner`~~ | *(deprecated)* | **Do not use.** Name retained only as a thin shim that re-exports `argus.planning` and emits `DeprecationWarning`. |
| Input | `argus.input`, `argus input` | Structured human records → nudges |
| Escalation | `argus.escalation`, `argus escalation` | Halt packets when triggers fire |
| Dashboard | `argus.dashboard`, `argus dashboard` | Static HTML from `runs/` |
| Economics | `argus.economics`, `argus economics` | Cost / revenue; **resources** links billable rows to products (`config/economics/`, `runs/economics/cost_ingest.json`) |
| Loop | `argus.orchestrator`, `argus loop run`; `argus.loop`, `argus loop full` | **`loop run`:** four stages (discover → signals → findings → decisions); manifest under `runs/loop/<run_id>/` ([`argus/orchestrator/README.md`](../argus/orchestrator/README.md)). Planning, **orchestration state**, and escalation are **separate commands** from `loop run`. **`loop full`:** extended harness (16 stages, discovery through dashboard — includes **ideas**, planning + plan-actions, approval sampling, execution dry-run; **not** simulation, portfolio read, or escalation packets; `summary.json` has `chain` for escalation CLI) — [loop-harness.md](loop-harness.md). |
| Experiments | `argus.experiments`, `argus experiments` | Hypothesis records under `runs/experiments/` |
| Advisors | `argus.advisors`, `argus advisors` | Archetype stubs → consensus |
| LLM (optional) | `argus.llm`, `argus llm` | Gated OpenAI client, idea expansion prompts, five-lane advisor council artifacts — [llm-integration.md](llm-integration.md) |
| Refinement | `argus.refinement`, `argus refine` | Staged draft → council → synthesis → regenerate → converge for ideas, product specs, implementation plans — [artifact-refinement.md](artifact-refinement.md) |
| Council | `argus.council`, `argus council` | Routed **grounded vs outsider** profiles, context policies, and convergence rules consumed by refinement — [councils.md](councils.md) |
| Capabilities | `argus.capabilities`, `argus capabilities` | Declared vs inferred gaps; **requests** when blocked; **`resume`** clears pauses after gaps close (see `argus capabilities resume --help`) |
| Actions | `argus.actions`, `argus actions` | Contract validation / dry-run only |
| Execution | `argus.execution`, `argus execution` | Opt-in subprocess runs; sandbox (cwd + command policy); logs under `runs/execution/` |
| Doctor | `argus.cli.doctor_cmd`, `argus doctor` | Inventory, staleness, **temporal** sidecars (`argus.doctor_temporal.v1`), doctrine/autonomy/approvals/capabilities, strategy, trends vs history, experiments, advisor JSON, economics, **audit JSON** (`runs/audit/`), **council default profiles** |
| Audit (MVP) | `argus.audit`, `argus audit` | **`bundle.json`** (`argus.audit_bundle.v1`): nine deterministic angles + optional **`ingest-cursor`** / **`ingest-agent`** overlay (`cursor_scan`); **`latest.json`** = Product Gap legacy; **context** uses `summary_lines` + coverage; idea nudges Product Gap only — [audit-architecture.md](audit-architecture.md), [audit-mvp.md](audit-mvp.md) |
| Simulation | `argus.simulation`, `argus simulate` | Heuristic scenario preview (product + optional experiment) |
| Self-audit | `argus.self`, `argus self` | Meta-findings, capability overlap, escalation patterns |
| Artifact validation | `argus.validation`, `argus validate artifacts` | Expected fields on JSON under `runs/` (optional loop run id) |

## Canonical types

Domain types live in **`argus.core.models`** (see [argus-object-model.md](argus-object-model.md) and [model-contracts.md](model-contracts.md)):

- `ProductNode`, `SignalRecord`, `Finding`, `DecisionCandidate`, lifecycle and portfolio rows  
- Action contracts and experiments have dedicated models under their packages; serialization uses **`argus.core.serialize`** (JSON) and YAML where products are loaded.

## `argus loop run` (analysis orchestrator)

The **`argus loop run`** command runs a **local, in-process** sequence of **four** stages with a JSON manifest. It updates `runs/signals/latest/`, `runs/findings/latest/`, and `runs/decisions/latest/`. **Planning**, **`argus orchestration`** (**`state`** / optional **`advance`** / optional Cursor review ingest), and **escalation** are **intentionally excluded**: run **`argus planning weekly`**, **`argus planning actions`**, **`argus orchestration state`** (and other orchestration subcommands as needed), and **`argus escalation generate`** as separate steps when you need those artifacts (see [system-flow.md](system-flow.md)).

This is **runtime orchestration** in the sense of “ordered stages and recorded artifacts,” but it is **not** remote execution, a scheduler, or the full product pipeline (no experiments, advisors, simulation, dashboard, **refinement**, or orchestration snapshots, etc., unless you use other commands or **`argus loop full`**).

**`argus autonomy run`** is a different pipeline (under `runs/autonomy/<run_id>/`) and may include its own planning stage copy; it is not the same as **`argus loop run`**.

## Data flow (detail)

1. **Discovery** — Scan `products/*/product.yaml`, validate shape, build inventory.
2. **Signals** — Per-product adapters emit normalized `SignalRecord` lists; latest bundles under `runs/signals/latest/`.
3. **Findings** — Rules consume signals and emit `Finding` records; latest under `runs/findings/latest/`.
4. **Lifecycle + decisions** — `LifecycleAssessment` from stage + findings; `DecisionCandidate` list with priority scores (strategy-aware when repo root is provided). Stub/gap metadata can annotate candidates; assessments reuse the same relevance rules for confidence (see `argus.decision.stub_awareness`).
5. **Ideas (optional)** — `argus ideas generate` → `runs/ideas/latest.json` ([idea-generation.md](idea-generation.md)).
6. **Portfolio** — Aggregate ranking and reports under `runs/decisions/latest/`, `runs/portfolio/latest/`.
7. **History / trends** — Read latest + snapshot history; deterministic summaries.
8. **Planning (CLI)** — `argus planning weekly` / `planning actions` write `runs/planning/`; not part of `argus loop run`.

## CLI surface

All commands are reachable as **`uv run argus <command> ...`**. Command groups are listed in **`README.md`**; step-by-step flow in **[system-flow.md](system-flow.md)**.
