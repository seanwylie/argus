# Documentation

**Agent / quick onboarding:** Start with **[`argus-context/README.md`](argus-context/README.md)** — **`north-star.md`** (vision + burn-down), **`06-open-gaps.md`** (gaps vs reality), **`08-operating-principles.md`** (rules); then **`architecture/state-of-the-system.md`** and this index for depth. The historical `00`–`08` spine is mostly under **`docs/`** (see table below), not duplicated as separate files in `argus-context/`.

This tree holds **doctrine**, **architecture**, and **operating** documentation for Argus and its ecosystem.

Existing material (for example under `Architecture/Portability/`) is preserved as reference and templates. New Argus-specific docs can grow alongside it.

## CLI surface

Install the package (see repository `README.md`), then run **`uv run argus --help`**. Command groups (see **[architecture.md](architecture.md)** for the subsystem map):

- **`argus products`** — **create** (scaffold), list, validate, show product nodes, **`permissions show`** (Phase 1 `yes`/`no`/`confirm` policy in `products/<id>/argus.policy.yaml`; see [project-permissions-phase1.md](project-permissions-phase1.md)), **`instrument-signals`** (deterministic signal coverage / observability readiness; see [product-signal-instrumentation.md](product-signal-instrumentation.md)), **`propose-creation`** (portfolio gap analysis → candidate product proposals; see [product-creation.md](product-creation.md)), **`scaffold-creation`** (turn a proposal into `products/<id>/`; see [product-creation-scaffold.md](product-creation-scaffold.md))
- **`argus signals`** — collect, **ingest-snapshots**, **snapshot-types**, **show**, **reality**, optional **cursor-prompt** / **cursor-ingest** (review sidecar under `runs/signals/review/`), list adapters
- **`argus findings`** — generate, show, summary
- **`argus decisions`** — generate, portfolio, show, **history**, **churn** (per-product generation memory under `runs/decisions/generations/`)
- **`argus lifecycle`** — show, **kill-score**, **report** (lifecycle and kill posture helpers)
- **`argus mission`** — **`show`** — effective mission / ethos (`config/mission_profiles.yaml`, `runs/mission/current.json`, or `ARGUS_MISSION_ID`; see [mission-model.md](mission-model.md))
- **`argus policy`** — **`show-operator`** — effective operator policy (`config/operator_policy.yaml` merged over code defaults; see [operator-policy.md](operator-policy.md)); **`experiment`** — read-only compare queue / quiescence / intervention / cycle synthesis across profiles (see [operator-policy-experiments.md](operator-policy-experiments.md)); **`feedback`** — correlate policy materiality with portfolio outcomes over time (see [operator-policy-feedback.md](operator-policy-feedback.md)); **`effectiveness`** — mission-segmented outcome rates vs current policy snapshot (see [operator-policy-effectiveness.md](operator-policy-effectiveness.md)); **`recommend`** — propose human-reviewable policy adjustments from feedback, outcomes, patterns, and intervention trends (see [operator-policy-recommendations.md](operator-policy-recommendations.md)); **`learning-synthesis`** — mission-conditioned learning summary from outcomes, effectiveness, recommendations, patterns, and experiments (see [operator-learning-synthesis.md](operator-learning-synthesis.md))
- **`argus portfolio`** — **`refresh`**, **`show`**, **`allocate`** (attention % from local artifacts), **`operator-queue`** (rank products by operator snapshot / orchestration state; see [operator-queue.md](operator-queue.md)), **`builder-activity`** (rollup of recent Builder invoke/reconcile truth to **`runs/portfolio/builder_activity/latest.json`**, plus Phase 3 **`runs/portfolio/builder_outcome_validation/latest.json`** when outcome artifacts exist — coordination/visibility/calibration only; not outcomes strategy or learning; see [plans/ephemeral-roadmap.md](plans/ephemeral-roadmap.md) Phase 2B), **`progress`** (one bounded `advance_orchestration` per top-N product; see [portfolio-progression.md](portfolio-progression.md)), **`quiescence`** (compare queue / snapshots / progression vs prior baseline; see [portfolio-quiescence.md](portfolio-quiescence.md)), **`delta-report`** (portfolio-wide “what changed” vs prior baseline; see [portfolio-delta-report.md](portfolio-delta-report.md)), **`intervention`** (deterministic stuck-loop / intervention hints from queue, snapshots, progression, delta history; see [portfolio-intervention.md](portfolio-intervention.md)), **`intervention-inbox`** / **`intervention-ack`** / **`intervention-resolve`** / **`intervention-snooze`** / **`intervention-ignore`** / **`intervention-escalate`** (human-in-the-loop queue over intervention flags; see [intervention-inbox.md](intervention-inbox.md)), **`cycle`** (one bounded chain: queue → progression → quiescence → delta → intervention + summary; see [portfolio-cycle.md](portfolio-cycle.md)), **`schedule`** (bounded multi-cycle sessions with guardrails; see [portfolio-scheduler.md](portfolio-scheduler.md)), **`history`** (canonical listing + trend summaries over stamped portfolio artifacts; see [portfolio-history.md](portfolio-history.md)), **`outcomes`** (did recent cycles improve state? see [portfolio-outcomes.md](portfolio-outcomes.md)), **`patterns`** (cross-product systemic patterns; see [portfolio-patterns.md](portfolio-patterns.md)), **`strategy`** (whole-portfolio strategic posture; see [portfolio-strategy.md](portfolio-strategy.md)), **`lifecycle`** (creation / active / wind-down synthesis; see [portfolio-lifecycle.md](portfolio-lifecycle.md)), **`run-autonomous`** (bounded autonomous session; see [autonomous-runner.md](autonomous-runner.md)), **`run-service`** (cadence + heartbeat; see [autonomous-runner-service.md](autonomous-runner-service.md)), **`replay`** (read-only forensic replay of portfolio artifacts; see [replay.md](replay.md))
- **`argus builder`** — Operator execution loop: **`set-target`** (non-story **`primary_target`**; optional **`--prepare`** to chain **`prepare`**), **`prepare`**, **`invoke`**, **`reconcile`**, **`review`**, **`merge`** — see [builder-execution-contract.md](builder-execution-contract.md). Also: **`doctor`** (host readiness: `bwrap`, `setpriv`, `git`; **`--json`**, **`--strict`**); **`work-orders`**, **`render-brief`** (signal-contract gaps; inspectable only); **`creation-propose`**, **`creation-apply`** (Phase 1; use **`creation-apply --write`** only after review)
- **`argus strategy`** — **`set`** / **`show`** operating strategy (`runs/strategy/current.json`)
- **`argus simulate`** — deterministic **best / expected / worst** preview for a product id or **`--experiment`**
- **`argus experiments`** — create, list, show, update-status; **propose**, **rank**, **evaluate** (see `--help`)
- **`argus input`** — add/list/show/remove structured human inputs (`runs/input/`)
- **`argus advisors`** — list, run, **consensus**
- **`argus capabilities`** — list, **evaluate**, **gaps**, **request**, **`resume`**
- **`argus economics`** — **analyze**, **portfolio**, **resources** (resource registry + cost ingest + orphan/unmapped detection)
- **`argus actions`** — validate, dry-run, show (contracts only; no execution)
- **`argus execution`** — dry-run (validate + sandbox), run (opt-in subprocess), show (`runs/execution/`)
- **`argus doctrine`** — show parsed `products/<id>/doctrine.yaml`
- **`argus autonomy`** — show / set mode, policy; spawn / run / scheduler (see `--help`)
- **`argus approval`** — list / request / approve / reject / evaluate (execution gate)
- **`argus doctor`** — local health checks (inventory, staleness, doctrine YAML, autonomy config/state, pending approvals, open capability requests, portfolio vs findings, strategy, trends vs history, experiments, advisor JSON, economics resource linkage, temporal integrity, decision-assessment presence vs decisions, high escalation pressure, ideas bundle JSON, execution lock file)
- **`argus audit`** — **`run`**, **`prompt`** / **`cursor-prompt`**, **`ingest-cursor`** / **`ingest-agent`** — multi-angle product bundle under `runs/audit/` ([audit-architecture.md](audit-architecture.md))
- **`argus refine`** — staged artifact refinement (drafts, council, convergence) under `runs/refinement/` ([artifact-refinement.md](artifact-refinement.md))
- **`argus self`** — **audit** meta-review (stdout / `--json`)
- **`argus history`** — **`snapshot`** (capture portfolio state to `runs/history/`), **`diff`**, **`product`** timeline
- **`argus trends`** — **`analyze`**, **`summary`**, **`drift`** (rules over `runs/history/`; writes `runs/trends/`)
- **`argus temporal`** — **`ingest`**, **`adapters`**, **`show`**, **`freshness`**, **`summary`**, **`refresh`** ([temporal.md](temporal.md), [temporal-intelligence.md](temporal-intelligence.md))
- **`argus planning`** — **`weekly`** synthesis (`runs/planning/`)
- **`argus adapters`** — **`list`** / **`run`** — adapter layer (collect → normalize → signals); see [adapters.md](adapters.md)
- **`argus validate`** — **`artifacts`** — JSON checks under `runs/` (optional loop run id)
- **`argus reset`** — **`--soft`** / **`--portfolio`** / **`--all`** — clear local `runs/` (keep `runs/README.md`) and optionally `products/`; use **`--dry-run`** first; portfolio/all require **`--confirm`** tokens when not dry-run (see `--help`)
- **`argus orchestration`** — **`state`** / **`advance`** / **`run-progression`** / **`replay`** / **`cursor-prompt`** / **`cursor-ingest`** — **eligibility-driven** snapshots from existing `runs/*` data (no subprocess orchestration in Argus): **`state`** writes `runs/orchestration/latest/<id>.json`, **`runs/orchestration/operator_snapshot/<id>.json`** (+ `.md` companion), and when **`next_action` ≠ `none`**, **`runs/orchestration/tasks/latest/<id>.json`** (`argus.orchestration_task.v1`); **`index.json`** when `--all` or multiple **`--product-id`**; **`advance`** writes **`latest/advancements/`** (optional **`--execute`** for in-process step execution); **`run-progression`** loops evaluate/advance (default execute) and writes **progression run** artifacts unless **`--no-write-artifact`**; **`replay`** read-only reconstructs context from persisted artifacts under **`runs/orchestration/replay/<id>/`** ([replay.md](replay.md)); optional Cursor review under **`runs/orchestration/review/`** (see [orchestrator README](../argus/orchestrator/README.md), [operator-snapshot.md](operator-snapshot.md))
- **`argus loop`** — **`run`** — **four** stages only (discover → signals → findings → decisions); **not** the full product pipeline (no audit/refinement/orchestration/planning/escalation here). Planning/escalation/orchestration state are **separate** CLIs. **`full`** — **16** stages through dashboard, incl. ideas ([loop-harness.md](loop-harness.md)); escalation packets still **separate** (`summary.json` `chain`)
- **`argus dashboard`** — **generate** static HTML from `runs/` artifacts; **`summary`** — one-page operator summary (JSON + Markdown; see [operator-summary.md](operator-summary.md)) including a compact **Builder** block (attention vs routine, recent rows) when **`runs/portfolio/builder_activity/latest.json`** exists; **`summary`** also refreshes that Builder rollup and writes **`runs/portfolio/builder_outcome_validation/latest.json`** (aggregate Phase 3 observational calibration — not Builder scoring); **`narrative`** — cross-cycle story from history, outcomes, deltas, patterns, interventions, cycles (see [operator-narrative.md](operator-narrative.md))
- **`argus escalation`** — generate / list / show (packets under `runs/escalations/`)
- **`argus ideas`** — **`generate`** / **`list`** — structured ideas (`runs/ideas/`; see [idea-generation.md](idea-generation.md))

End-to-end refresh: **`argus portfolio refresh`** (writes `runs/portfolio/latest/` and updates `runs/decisions/latest/portfolio.json`).

Escalation (policy halt, not execution): **`argus escalation generate|list|show`** — durable transition packets under `runs/escalations/` when deterministic trigger rules fire; complementary to **`escalation_eligible` / `escalation_triggers`** on **`argus orchestration state`** (a **derived** eligibility snapshot, not an execution queue—use **`escalation generate`** for packets).

| Doc | Topic |
|-----|-------|
| [architecture.md](architecture.md) | Mental model, subsystem map, data flow |
| [proof-run.md](proof-run.md) | Repeatable end-to-end check (signals → findings → audit → ideas → refine → orchestration state → **`run-progression`** with durable progression artifact); optional **section 5.1** pytest for Phase-2 orchestration (**`experiments_propose`** included) |
| [temporal.md](temporal.md) | Temporal bundles, freshness rules, CLI |
| [temporal-intelligence.md](temporal-intelligence.md) | Reality vs interpretation; decisions, advisors, autonomy, dashboard |
| [stub-inventory.md](stub-inventory.md) | Stubs, placeholders, incomplete vs real (inventory + priorities) |
| [system-flow.md](system-flow.md) | Step-by-step operator loop |
| [loop-harness.md](loop-harness.md) | `argus loop full` stage list vs `loop run` |
| [autonomy.md](autonomy.md) | Autonomy modes, policy caps, enforcement |
| [autonomy-rollout.md](autonomy-rollout.md) | Bounded rollout tiers (0–4), matrix, guardrails, escalation |
| [execution.md](execution.md) | Opt-in subprocess runs, artifacts, safety stack |
| [doctrine.md](doctrine.md) | Optional per-product `doctrine.yaml` |
| [capabilities.md](capabilities.md) | Gap analysis and capability requests |
| [model-contracts.md](model-contracts.md) | IDs, canonical types, serialization notes |
| [product-model.md](product-model.md) | `products/<id>/product.yaml` — required/optional fields, `raw_extensions`, `import_state`, validation |
| [project-permissions-phase1.md](project-permissions-phase1.md) | **`products/<id>/argus.policy.yaml`** — Phase 1 `yes`/`no`/`confirm` project permissions (`argus products permissions show`) |
| [product-creation-scaffold.md](product-creation-scaffold.md) | **`argus products scaffold-creation`** — proposal → on-disk product (`argus.product_creation_scaffold.v1`) |
| [product-creation.md](product-creation.md) | **`argus products propose-creation`** — mission-grounded product proposals from portfolio gaps (`argus.product_creation_proposals.v1`) |
| [product-signal-instrumentation.md](product-signal-instrumentation.md) | **`argus products instrument-signals`** — observability readiness (`argus.product_signal_instrumentation.v1`) before optimization loops |
| [importer-operations.md](importer-operations.md) | Importer **status**, **drift**, **replay** via `tools/import_product.py` |
| [importer-discovery-classification.md](importer-discovery-classification.md) | Importer `product_shape` heuristic (python/js/mixed/static/…) — evidence only, no runtime claims |
| [orchestration-readiness.md](orchestration-readiness.md) | **`readiness`** on orchestration state (`readiness_tier`, `understanding_debt`, `confidence_gate`) |
| [next-action-policy.md](next-action-policy.md) | Declarative **`next_action_policy`** object on orchestration state |
| [operator-snapshot.md](operator-snapshot.md) | Per-product **operator snapshot** (`runs/orchestration/operator_snapshot/<id>.json` + `.md`) |
| [mission-model.md](mission-model.md) | **Mission / ethos** — `argus mission show`, `config/mission_profiles.yaml`, `runs/mission/mission_effective.json` |
| [mission-policy-integration.md](mission-policy-integration.md) | Mission → **effective operator policy** (bounded; signals/audit/findings unchanged) |
| [mission-provenance-artifacts.md](mission-provenance-artifacts.md) | **`mission_context`** on stamped operator/portfolio/dashboard artifacts (which mission at generation time) |
| [operator-policy.md](operator-policy.md) | **Operator policy** — tunable thresholds/weights (`argus.operator_policy.v1`, `config/operator_policy.yaml`, `argus policy show-operator`) |
| [operator-policy-experiments.md](operator-policy-experiments.md) | **`argus policy experiment`** — read-only comparison of profiles vs queue, quiescence, intervention, cycle synthesis (`argus.operator_policy_experiment.v1`) |
| [operator-policy-feedback.md](operator-policy-feedback.md) | **`argus policy feedback`** — outcomes vs policy thresholds over time (`argus.operator_policy_feedback.v1`) |
| [operator-policy-recommendations.md](operator-policy-recommendations.md) | **`argus policy recommend`** — heuristic proposals (`argus.operator_policy_recommendations.v1`; uses feedback + **effectiveness** + patterns; mission-scoped rows; does not change policy) |
| [operator-policy-effectiveness.md](operator-policy-effectiveness.md) | **`argus policy effectiveness`** — mission-segmented outcome associations (`argus.operator_policy_effectiveness.v1`; descriptive, not causal) |
| [operator-learning-synthesis.md](operator-learning-synthesis.md) | **`argus policy learning-synthesis`** — merged cautious lessons (`argus.operator_learning_synthesis.v1`; no policy mutation) |
| [operator-queue.md](operator-queue.md) | Portfolio **operator queue** (`runs/portfolio/operator_queue/latest.{json,md}`) |
| [portfolio-progression.md](portfolio-progression.md) | **`argus portfolio progress`** — bounded multi-product orchestration advances |
| [portfolio-quiescence.md](portfolio-quiescence.md) | **`argus portfolio quiescence`** — whether another pass is warranted; material deltas vs prior baseline (thresholds shared with [portfolio-delta-report.md](portfolio-delta-report.md)) |
| [argus-object-model.md](argus-object-model.md) | Domain types (`ProductNode`, `SignalRecord`, …) |
| [experiments.md](experiments.md) | Proposals, ranking, lifecycle, evaluation |
| [advisors.md](advisors.md) | Archetypes, LLM option, consensus, logging |
| [simulation.md](simulation.md) | Deterministic scenarios, limitations |
| [portfolio.md](portfolio.md) | Allocation inputs and interpretation |
| [portfolio-delta-report.md](portfolio-delta-report.md) | **`argus portfolio delta-report`** — what changed vs prior baseline (queue, readiness, confidence) |
| [portfolio-intervention.md](portfolio-intervention.md) | **`argus portfolio intervention`** — which products need intervention vs another routine pass |
| [intervention-inbox.md](intervention-inbox.md) | **`argus portfolio intervention-inbox`** — ack / snooze / resolve / ignore / escalate (`argus.intervention_inbox.v1`) |
| [portfolio-cycle.md](portfolio-cycle.md) | **`argus portfolio cycle`** — one bounded operator cycle and bundle under `runs/portfolio/cycle/` |
| [portfolio-scheduler.md](portfolio-scheduler.md) | **`argus portfolio schedule`** — repeated bounded cycles with quiescence/intervention guardrails (`runs/portfolio/scheduler/`) |
| [portfolio-history.md](portfolio-history.md) | **`argus portfolio history`** — portfolio artifact spine + per-product trends |
| [portfolio-outcomes.md](portfolio-outcomes.md) | **`argus portfolio outcomes`** — trajectory vs stamped deltas/quiescence + progression/intervention |
| [portfolio-patterns.md](portfolio-patterns.md) | **`argus portfolio patterns`** — cross-product systemic patterns (`argus.portfolio_patterns.v1`) |
| [portfolio-strategy.md](portfolio-strategy.md) | **`argus portfolio strategy`** — derived strategic posture (`argus.portfolio_strategy.v1`) |
| [portfolio-lifecycle.md](portfolio-lifecycle.md) | **`argus portfolio lifecycle`** — portfolio-wide lifecycle lanes (`argus.portfolio_lifecycle.v1`) |
| [autonomous-runner.md](autonomous-runner.md) | **`argus portfolio run-autonomous`** — bounded refresh→cycle→lifecycle→summary→narrative session (`argus.portfolio_autonomous_runner.v1`) |
| [autonomous-runner-service.md](autonomous-runner-service.md) | **`argus portfolio run-service`** — cadence wrapper + heartbeat (`argus.portfolio_runner_service.v1`) |
| [replay.md](replay.md) | Read-only **orchestration** / **portfolio** replay (`runs/*/replay/`) |
| [signals-and-adapters.md](signals-and-adapters.md) | Signals, adapters, CLI, persistence |
| [findings-engine.md](findings-engine.md) | Rule-based findings from signals |
| [decisions-and-lifecycle.md](decisions-and-lifecycle.md) | Lifecycle scores, decision intents, priority model |
| [decision-history.md](decision-history.md) | Decision memory and action churn (`argus decisions history|churn`) |
| [dashboard.md](dashboard.md) | Local HTML portfolio dashboard (`argus dashboard`) |
| [operator-summary.md](operator-summary.md) | **`argus dashboard summary`** — single-pane operator snapshot (`argus.operator_summary.v1`) |
| [operator-narrative.md](operator-narrative.md) | **`argus dashboard narrative`** — cross-cycle operator story (`argus.operator_narrative.v1`) |
| [idea-generation.md](idea-generation.md) | Exploit / explore / invent ideas (`runs/ideas/`, selection, diversity) |
| [history.md](history.md) | Timestamped portfolio snapshots and diffs (`argus history`) |
| [trends.md](trends.md) | Trend and drift analysis over snapshot history (`argus trends`) |
| [plans/ephemeral-roadmap.md](plans/ephemeral-roadmap.md) | **Active roadmap** — Phase 1 Eyes → Phase 2 Hands → Phase 2A Seal the Hands (refined caveats) → **2B** Operational Builder (incl. portfolio **`builder_activity`**) → **2C** Generalize (substantially complete with caveats) → Phase 3 Memory (distinct from visibility rollup) |
| [builder-execution-contract.md](builder-execution-contract.md) | Builder **set-target** (non-story **`primary_target`**) → **prepare / invoke / reconcile** → **review / merge**; containment, permissions, scope, merge readiness |

## Local development

After `uv sync --group dev`, use **`uv run ruff check .`**, **`uv run ruff format .`**, and **`uv run pytest -q`** as described in the repository root [README.md](../README.md#development).
