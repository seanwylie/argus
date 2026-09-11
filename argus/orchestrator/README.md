# Orchestrator (`argus.orchestrator`)

This package implements the **analysis loop** behind **`argus loop run`**: a deterministic, local,
file-based pipeline with a manifest under `runs/loop/<run_id>/`.

It is **not** a remote job runner or a general workflow engine; it sequences Argus stages in-process
and records structured stage outputs.

**`argus orchestration state`** lives in the same package but is **not** part of **`loop run`**: it only **reads** existing `runs/*` data and writes **`runs/orchestration/`** (state, optional **tasks**, optional **advancements**). **`loop run`** does **not** refresh orchestration files. **Refinement** (including **implementation_plan**) advances through **`argus refine`** sessions; orchestration may surface **`implementation_plan_generate`** (or other actions) as the **next** legal step—**task/state-driven**, not implied by the loop stages.

**Phase 1 governance mapping:** every handler in `STEP_EXECUTION_REGISTRY` must have an entry in `ORCHESTRATION_ACTION_PHASE1_KEYS` (`argus/orchestrator/orchestration_phase1.py`). Import fails if they diverge. Inspect with **`argus orchestration phase1-mapping`** (or **`--json`** for machine-readable report).

## State-driven snapshot (`argus orchestration state`)

**Phase-1 contract** (canonical `action_id` strings, `orchestration_posture`, `orchestration_status_reason_codes` parity, cross-product metadata, and **`runs/orchestration/latest/`** artifact chain) is summarized in **`docs/model-contracts.md`** under *Phase-1 orchestration / operator contract*.

**Separate** from the linear loop: **`argus orchestration state --product-id <id> [<id> …]`** or **`--all`** (optional **`--products-dir`**) reads durable
artifacts under `runs/signals/`, **`runs/temporal/latest/`**, `runs/audit/`, `runs/refinement/`, and `runs/execution/` and
writes **`runs/orchestration/latest/<product_id>.json`** (`schema: argus.orchestration_state.v1`).
The same write path also refreshes **`runs/orchestration/operator_snapshot/<product_id>.json`** and **`.md`**
(`schema: argus.operator_snapshot.v1` — consolidated operator view; see **`docs/operator-snapshot.md`**).
When **`next_action`** is not **`none`**, also writes **`runs/orchestration/tasks/latest/<product_id>.json`** (`schema: argus.orchestration_task.v1`) — a machine-readable pending task ( **`task_type`**, **`action`**, **`reason_codes`**, **`blockers`**, **`source_state_ref`**, **`inputs`**, **`dependencies`**) aligned with that snapshot; if **`next_action`** becomes **`none`**, the task file is removed.
With **multiple ids** or **`--all`**, also writes **`runs/orchestration/latest/index.json`** (`schema: argus.orchestration_index.v1`) listing each product’s path and headline **`orchestration_status`**, and **`runs/orchestration/latest/fleet_governance_rollup.json`** (`schema: argus.orchestration_fleet_governance_rollup.v1`) — a compact per-product fleet rollup with links.
Each bundle includes **`orchestration_status`** / **`orchestration_status_reason`** / **`orchestration_status_reason_codes`**, additive **`orchestration_posture`** (`schema: argus.orchestration_posture.v1` — execution-feedback crosswalk, retry-reopened flags, repeat-threshold summaries), structured **`waiting_inputs[]`** (missing grounded reviews, implementation-plan prerequisites, **pending approvals** under `runs/approval/records/`, observability gaps), **`artifacts.execution.pending_approvals`**, plus legacy **`overall_status`**.
It also includes **`import_health`**, **`readiness_reason`** (human string from importer first-pass gating), additive **`readiness`** (`schema: argus.orchestration_readiness.v1` — explicit **readiness_tier**, **understanding_debt**, **confidence_gate**; see **`docs/orchestration-readiness.md`**), and structured **`next_action_policy`** (declarative rule trace for the resolved **`next_action`**; see **`docs/next-action-policy.md`**).
It computes **eligible next actions** (deterministic `action_id` strings), including:
`signals_collect`, `audit_run`, `temporal_refresh`, **`findings_generate`** (when **`runs/signals/latest`** exists and is not time-stale), **`decisions_generate`** (when signals are fresh and **`runs/findings/latest/{product_id}.json`** exists), **`ideas_generate`** (when signals are fresh and **`runs/findings/latest`** and **`runs/decisions/latest/{product_id}.json`** exist), **`escalation_packet_generate`** (when findings exist and structural escalation posture matches policy), `refinement_start_product_spec`, **`refinement_start_idea`** (when **`runs/ideas/latest.json`** matches the product and a deterministic top idea can be selected; opens a draft/review session only), **`implementation_plan_generate`**
(when product_spec is finalized but no implementation_plan session exists), `refinement_run`,
`refinement_submit_reviews_in`, `escalation_consider` (human_review_required, **rejected** terminal
sessions, or **refinement_not_converged_stuck** when convergence artifacts show `converged: false`
at max rounds, plus **audit** gaps: `product_gap` stub/missing, `product_gap` partial, **security** stub).
**Temporal:** `signals_collect` is also eligible when `runs/temporal/latest` `worst_freshness_status`
is `stale` or `expired`. When signals are fresh, **`temporal_refresh`** may appear to recompute
`runs/temporal/latest` from `runs/signals/latest` without re-running adapters. When both **`temporal_refresh`** and **`signals_collect`** are eligible with fresh signals, ordering prefers **`temporal_refresh`** first; when multiple Phase-2 chain actions are simultaneously eligible (**`signals_collect`**, **`findings_generate`**, **`decisions_generate`**, **`ideas_generate`**, **`refinement_start_idea`**, **`escalation_packet_generate`**), ordering normalizes them to observe → interpret → decide → candidate ideas → **start idea refinement (when eligible)** → govern. State hygiene (EOA pending, temporal vs signals, Phase-2 chain) runs on merged eligibility **before** execution-feedback deprioritization so **`executed`** tail moves are not undone (see **`eligibility_facts.eligible_actions_order_rule`**, semicolon-separated when reordering stages apply — implemented in **`eligibility.py`**). Each eligible row includes **`reason_codes`**; **`eligibility_facts`** summarizes
signal/temporal/audit drivers. **`escalation_eligible`** and **`escalation_triggers`**
record durable escalation posture (not only `escalation_consider` in `eligible_actions`): e.g.
**convergence_failed_blocking_grounded_reviews** when max-round convergence did not converge and
`reviews/round_*.json` has a **grounded** `blocking` review; **review_input_wait_exceeded** when
`reviews_in` is still missing after **72h** in `in_review`; **compound_critical_artifact_gaps** when
multiple orthogonal gaps apply (signals/temporal line vs audit age vs on-disk audit content gaps).

**Explicit blockers** include `refinement_cycle_incomplete` (draft exists but `reviews/round_*.json`
missing — **blocked_waiting_input**) and `refinement_not_converged_stuck`. The bundle includes
`artifacts.refinement.review_state` (`absent`, `in_progress`, `awaiting_review_input`, `finalized`,
`human_review_required`, `rejected`, `not_converged_stuck`) and `allowed_review_states`.

**`state`** does not run subprocesses, LLM calls, or refinement/signal collection: it **evaluates and writes** the snapshot (and syncs **`tasks/latest/`** from **`next_action`**). Use **`orchestration advance`** separately to record durable next-step **advancement** under **`advancements/`** (**`--execute`** runs the in-process step executor when **`action_status`** is **`queued`**); use **`cursor-prompt` / `cursor-ingest`** for an optional interpretation sidecar. Use **`--no-write`** to print without writing; **`--json`** prints one product’s full state, or the **index** payload when multiple products are selected.

## Advancement record (`argus orchestration advance`)

**`argus orchestration advance --product-id <id>`** evaluates the same eligibility snapshot, selects the **first**
`eligible_actions` entry (same ordering as **`next_action`** when not blocked), and writes
**`runs/orchestration/latest/advancements/<product_id>.json`** (`schema: argus.orchestration_advancement.v1`) with
**`selected_action`**, **`selected_at_utc`**, **`transition_reason`**, **`source_eligibility_facts`** (copy of
evaluation facts), and **`action_status`**: **`queued`** (intent recorded for the next operator/automation step),
**`blocked`** (e.g. **`blocked_waiting_input`** — honest wait for refinement/operator input), or **`skipped`** (no
eligible actions). **`--execute`** runs a small in-process executor for supported **`action_id`** values
(including **`signals_collect`**, **`audit_run`**, **`orchestration_state_refresh`**, **`temporal_refresh`**, **`findings_generate`**, **`decisions_generate`**, **`ideas_generate`**, **`escalation_packet_generate`**, **`refinement_start_product_spec`**, **`refinement_start_idea`**, **`implementation_plan_generate`**, **`refinement_run`**, **`refinement_submit_reviews_in`**, **`escalation_consider`**, **`execution_outcomes_apply`**) and records **`executed` / `failed` / `queued_unhandled`** on the advancement payload; without **`--execute`**, the command does **not** invoke subprocesses or external tools. **`dispatched`** is reserved for
future runners that attach execution receipts. By default **`--no-refresh-state`** is off: the command also
refreshes **`runs/orchestration/latest/<product_id>.json`** so inspection stays aligned.

## Bounded progression (`argus orchestration run-progression`)

**`argus orchestration run-progression --product-id <id> [--max-steps N]`** runs a **single-process** loop: evaluate eligibility → **`advance`** one step (by default with the same in-process **execution** as **`advance --execute`**) → re-evaluate, until **`blocked_waiting_input`**, **`blocked_waiting_approval`**, **`escalated`**, **`complete`**, no eligible **`next_action`**, **`queued_unhandled`** / **`execution_failed`**, **`advance`** returns **skipped/blocked**, eligibility **fingerprint** is unchanged after an executed advance (deterministic fixpoint), or **`max_steps`** (default **8**). **`--no-execute`** records intent only (like **`advance`** without **`--execute`**). Does **not** use subprocesses or background workers. **`--json`** emits **`argus.orchestration_progression_run.v1`** (includes **`step_count`**, **`actions_taken`**, **`terminal_status`**, **`terminal_reason`**; **`artifact_paths`** when a durable record was written). By default also writes **`argus.orchestration_progression_run_artifact.v1`** under **`runs/orchestration/latest/progression_runs/<id>.json`** and **`runs/orchestration/progression_runs/generations/<run_id>.json`**; use **`--no-write-artifact`** to skip.

The repeatable checklist in **`docs/proof-run.md`** runs **`run-progression`** after **`orchestration state`** and includes **jq** assertions for the on-disk progression artifact (skip those checks when **`--no-write-artifact`**).

## Cursor orchestration review (optional)

**`argus orchestration cursor-prompt --product-id <id>`** prints a **Cursor** prompt (stdout) with a bounded snapshot of deterministic orchestration state and the JSON contract for **`argus.orchestration_cursor_review.v1`**.

**`argus orchestration cursor-ingest --product-id <id> --file <path>`** validates that JSON and writes **`runs/orchestration/review/<product_id>.json`** (`argus.orchestration_review_bundle.v1`) with **`orchestration_review`**, **`deterministic_orchestration_fingerprint`**, and **`ingest_history`** on reruns (prior fingerprint + timestamp — **not** silent wipe). **Argus** does not call LLM APIs; **`runs/orchestration/latest/`** is unchanged by ingest (refresh state separately with **`orchestration state`**).

## Orchestrator boundary (planning & escalation)

**`argus loop run` includes only the analysis spine.** Weekly synthesis, **orchestration eligibility snapshots**, and **escalation packets** live
outside this loop so cadence, triggers, and human review stay explicit:

| Concern | Command | Canonical outputs |
|---------|---------|-------------------|
| Weekly operating plan | `argus planning weekly` | `runs/planning/` (e.g. `weekly.md`) |
| Action contracts from priorities | `argus planning actions` | `runs/planning/actions.json` |
| Eligibility / next action / task pick-up | `argus orchestration state` | `runs/orchestration/latest/`, optional `runs/orchestration/tasks/latest/` |
| Escalation when rules fire | `argus escalation generate <product_id>` | `runs/escalations/` (durable packets; complements **`escalation_triggers`** in orchestration state) |

Do **not** expect `argus loop run` to write those directories (including **`runs/orchestration/`**).

For a **longer** end-to-end harness (doctrine, capabilities, history, trends, **ideas**, experiments,
advisors, **planning snapshot**, plan-actions, approval sampling, execution dry-run, dashboard), use
**`argus loop full`** (`argus/loop/harness.py`). It does **not** run simulation or portfolio read—use
**`argus simulate`** / **`argus portfolio`** separately. Escalation is **not** integrated; **`summary.json`**
includes a **`chain`** hint listing **`argus escalation generate <product_id>`** per target.

**`argus autonomy run`** (scheduled / supervised cycles) is a separate subsystem: it runs its own
staged pipeline under `runs/autonomy/<run_id>/` and may emit a planning copy there; it is not
`argus loop run`.

## `argus loop run` — what runs

Stages execute in **`LOOP_STAGE_ORDER`** (`stages.py`):

| Order | Stage       | What it does |
|-------|-------------|--------------|
| 1 | `discover`  | Builds product inventory; fails if `--product` is invalid or if **any** invalid `product.yaml` exists in the tree (same strictness as inventory validation). |
| 2 | `signals`   | Collects signals via adapters; writes latest bundles under `runs/signals/`. |
| 3 | `findings`  | Generates findings; writes `runs/findings/latest/<product>.json`. |
| 4 | `decisions` | Generates lifecycle + candidates, saves per-product decisions, ranks portfolio, writes `runs/decisions/latest/` (including `portfolio.json` when ranking produces rows). |

Exit code **0** only if every stage **succeeds** (`ok: true`).

## What `argus loop run` does **not** do

- Does **not** run `argus planning weekly`, `argus planning actions`, or `argus escalation generate`.
- Does **not** run portfolio refresh explicitly (but overlaps heavily: signals → findings → decisions).
- Does **not** run history snapshots, trends, experiments, advisors, simulation, economics, or dashboard.
- Does **not** execute shell commands or subprocess actions (no execution hooks in this loop).

Use **`argus loop full`** or individual commands for those outputs.

## Artifacts

Each run creates:

- `runs/loop/<run_id>/manifest.json` — run metadata and per-stage summary lines.
- `runs/loop/<run_id>/stages/<stage>/output.json` — stage payload (`schema`: `argus.loop.stage.v1` where applicable).
- `runs/loop/<run_id>/events.jsonl` — append-only stage events.
- `runs/loop/<run_id>/stages/<stage>/log.jsonl` — per-stage JSONL logs (runners).

Canonical product artifacts live under `runs/signals/`, `runs/findings/`, `runs/decisions/` as usual.

## After the loop: planning and escalation

After a successful loop (or anytime artifacts are fresh enough):

```bash
uv run argus planning weekly --out runs/planning/weekly.md
uv run argus planning actions
uv run argus escalation generate <product_id>
```

## Failure and `--continue-on-error`

If a stage fails, the loop stops unless **`--continue-on-error`** is set (see CLI help).
The manifest records failed stages.

## Source files

| File | Role |
|------|------|
| `stages.py` | `LoopStage` enum, `LOOP_STAGE_ORDER`, `StageResult`. |
| `runner.py` | Stage implementations for discovery, signals, findings, decisions. |
| `loop.py` | `run_analysis_loop` — iteration, manifest, events. |
| `cli.py` | `argus loop run` and `argus loop full` dispatch. |
| `eligibility.py` | `evaluate_product_orchestration` — deterministic eligibility from artifacts. |
| `artifact_snapshot.py` | Load signals/audit/refinement/execution paths (read-only). |
| `state_pass.py` | Write `runs/orchestration/latest/<product_id>.json`, optional `index.json`, and **`runs/orchestration/tasks/latest/`** via `task_artifact.py`. |
| `task_artifact.py` | Build/sync `argus.orchestration_task.v1` next to state writes. |
| `advancement.py` | `advance_orchestration` — durable advancement record under `latest/advancements/`. |
| `progression.py` | `run_orchestration_progression` — bounded evaluate/advance loop (`run-progression` CLI); progression run artifacts. |
| `step_executor.py` | `execute_orchestration_action` — in-process mapping from `action_id` to stage entry points (`advance --execute`, default `run-progression`). |
| `execution_feedback.py` | `write_orchestration_execution_feedback` / `load_orchestration_feedback_summary` — `argus.orchestration_execution_feedback.v1` under `runs/execution/<id>/`; **eligibility** reads summaries for blockers, escalation, and eligible-action ordering. |
| `state_cli.py` | `argus orchestration` subcommands: `state`, `advance`, `run-progression`, `cursor-prompt`, `cursor-ingest`. |
| `cursor_review.py` | Validate `argus.orchestration_cursor_review.v1`; state fingerprint. |
| `review_ingest.py` | `runs/orchestration/review/<id>.json` bundle + ingest history. |
| `review_prompt.py` | Build Cursor prompt text. |
| `state_models.py` | Schema id and action id constants. |
