# Model contracts and identifiers

Canonical domain types live under **`argus/core/models/`** (see [argus-object-model.md](argus-object-model.md)). Package-specific wrappers (e.g. `ExperimentEvaluation`, `TrendSummary`) extend behavior but do **not** redefine core entities.

## Identifier conventions

| Field | Typical prefix / shape | Where used |
|-------|-------------------------|------------|
| `product_id` | Slug from `product.yaml` `id` | All subsystems; foreign key in signals, findings, decisions, experiments, simulation |
| `experiment_id` | `exp_<timestamp>_<hex>` | `runs/experiments/`, simulation `experiment_id`, evaluation |
| Finding `id` | From `argus.findings.ids` / rules | Findings bundles, decision linkage |
| Decision candidate `id` | Opaque string | Decision bundles |
| History / snapshot | Directory names under `runs/history/snapshots/` | Trends, planning |
| `proposal_id` | `pr_<product_id>_<hash>` | Experiment proposals (deterministic) |
| `run_id` (execution) | `exec_<UTC>_<hex>` | `runs/execution/<run_id>/` |
| `approval_id` | `appr_<UTC>_<hex>` | `runs/approval/records/` |
| `packet_id` / escalation id | `esc_<UTC>_<product>` (typical) | `runs/escalations/` |
| `request_id` (capability) | `creq_<UTC>_<hex>` | `runs/capabilities/requests/` |

**Autonomy** artifacts (`runs/autonomy/autonomy.json`, `state.json`) use `schema: argus.autonomy.v1` / `argus.autonomy.state.v1`; they are **not** domain entities in `argus.core.models` but are operator configuration.

**Doctrine** is validated into `ProductDoctrine` (`argus.doctrine.models`); on disk it is YAML only (`products/<id>/doctrine.yaml`).

### Product signal manifest (declarations)

Products may declare **which signals matter**, **where they come from**, **freshness expectations**, and **metadata for Argus**. Schema id: **`argus.product_signal_manifest.v1`**.

| Location | Precedence |
|----------|------------|
| `products/<id>/signals.yaml` | Used when present (optional `product_id` must match `product.yaml` `id`). |
| `product.yaml` key `signal_manifest` | Used when `signals.yaml` is absent. |

Validation and loading: `argus.products.signal_manifest` (`validate_signal_manifest_dict`, `load_product_signal_manifest`). Parsed data is attached to `ProductNode.signal_manifest` after successful `validate_manifest`.

**Use at collection time:** `collect_for_product` enables adapters when either `product.yaml` `signals:` lists the type **or** an enabled manifest row declares that `source_type`, then runs `reconcile_manifest_declarations` (`argus.signals.manifest_collect`) so every enabled declaration has a matching collected row or a deterministic synthetic row (`source` **`manifest_declaration`**, stable `sig-decl-…` id). `save_collection` passes `signal_manifest` and `product_root` into `attach_canonical_to_records`. Each `SignalRecord` is matched to at most one manifest row (`argus.signals.manifest_bridge.match_manifest_entry`, or by `payload.manifest_signal_id` for gap rows); matches set canonical **`category`** from the manifest category, **`freshness_sla`** (string) from the manifest, trust mapping from `ManifestTrustLevel`, and provenance keys `manifest_signal_id` / `category_source`. Unmatched legacy rows keep category from `SignalType`, SLA from payload metadata.

**Temporal SLA seconds:** `argus.temporal.freshness_compute.compute_signal_freshness_view` prefers parsing the **canonical** `freshness_sla` string, then payload sidecar `freshness_sla`.

**Operator workflow (minimal):**

1. Declare expectations in `signals.yaml` or `product.yaml` → `signal_manifest` (`argus.product_signal_manifest.v1`).
2. `uv run argus signals collect [<product_id>]` → `runs/signals/latest/<id>.json` + `runs/temporal/latest/<id>.json` (sidecar).
3. `uv run argus signals show <id>` — human summary includes manifest entry count and whether the temporal file exists; `--json` adds an `operator` object (`worst_freshness_status`, `temporal_bundle_present`, …).
4. `uv run argus temporal freshness <id>` — bucket + `freshness_status` counts; `uv run argus temporal summary` for all products.
5. If temporal is missing or wrong after editing signals only: `uv run argus temporal refresh <id>` (recompute from latest signals bundle).
6. Portfolio/degraded visibility: `uv run argus doctor` (includes temporal checks when signals are expected).

### Local snapshot manifest (per-adapter file listing)

**Distinct** from the product signal manifest. Under `products/<id>/metrics/snapshots/local/`, optional `manifest.json` uses schema **`argus.local_snapshot_manifest.v1`** and is read only by **`LocalSnapshotAdapter`** to enumerate which snapshot files to ingest and to emit explicit missing/invalid records. It does not replace or merge with `argus.product_signal_manifest.v1`.

Use **string** IDs in JSON artifacts. Timestamps are **ISO 8601** strings (UTC with offset) unless noted otherwise.

## CLI validation and loop summaries

- **`argus validate artifacts`** — Reports **`argus.validate_report.v1`** (`ok`, `checked_files`, `issues[]`). An optional loop **`run_id`** adds checks for `runs/loop/<run_id>/manifest.json` and `summary.json`.
- **`argus loop full`** — Run-level **`summary.json`** uses **`argus.loop.full_summary.v1`** (`stages[]` entries include `stage`, `ok`, `duration_ms`, optional `error`). Includes **`chain`**: `{ "escalation": "separate", "next_commands": ["uv run argus escalation generate <product_id>", ...] }` so escalation stays an explicit post-step.
- **`argus capabilities resume`** — After capability requests resolve: clear pauses, re-validate actions, enqueue executable work (`--no-queue` re-validates only); see CLI help.

Signal **adapter** contracts and the pipeline are documented in **[adapters.md](adapters.md)** (package `argus.adapters`).

### Timestamps (common fields)

| Field | Typical meaning |
|-------|-----------------|
| `observed_at` | On `SignalRecord`: when the underlying observation applies (UTC). |
| `fetched_at` / bundle `collected_at_utc` | When Argus **ingested** or **wrote** the bundle (collection clock). |
| `generated_at_utc` | Findings/decisions/temporal summary generation time. |
| `consultation_as_of_utc` | Advisor grounding clock (artifact inspection time). |

Do not confuse **`SignalType.TEMPORAL`** (time-aware **snapshot** signals) with **“temporal”** in decision freshness naming, which refers to **operational** signal types (metrics, analytics, cost, health) for gating.

### Orchestration state (schemas)

| Schema | Role |
|--------|------|
| `argus.orchestration_state.v1` | Per-product snapshot from **`argus orchestration state`**: **`orchestration_status`** (`eligible` \| `blocked_waiting_input` \| **`blocked_waiting_approval`** \| `stale_refresh_needed` \| `complete` \| `escalated`) + **`orchestration_status_reason`** + **`orchestration_status_reason_codes`** (sorted machine codes); **`waiting_inputs[]`** — durable rows (`kind`, `reason_codes`, `detail`, optional `session_id`, `expected_paths`, `approval_ids`) for refinement gaps, implementation-plan prerequisites, **pending approvals**, and observability (signals / temporal / audit); artifact phases (product_spec, implementation_plan, **refinement** with `review_state` + `allowed_review_states`), signals, **temporal** (`runs/temporal/latest`), audit (including `angle_status`), execution (**`pending_approvals`** under `artifacts.execution`); **`eligibility_facts`**; **`escalation_eligible`** + **`escalation_triggers`**; `eligible_actions[]` (`action_id`, `reason`, **`reason_codes`**); `blockers[]`; legacy **`overall_status`**; `next_action`. Written under `runs/orchestration/latest/<product_id>.json`. |
| `argus.orchestration_index.v1` | Batch index from **`argus orchestration state --all`** or **multiple `--product-id`** values: `products[]` with `product_id`, `orchestration_status`, `orchestration_status_reason`, `artifact_path`; written to `runs/orchestration/latest/index.json`. |
| `argus.orchestration_fleet_governance_rollup.v1` | **Additive** fleet rollup from the same batch evaluation: compact `products[]` (priority, `next_action`, `has_eligible_actions`, `eligible_action_ids`, escalation codes, `refinement_review_state`, `orchestration_posture` subset, **`phase2_posture`** — `findings_generate_eligible`, `decisions_generate_eligible`, `ideas_generate_eligible`, `escalation_packet_generate_eligible`, `chain_furthest_ready` (`none` \| `observe_refresh` \| `interpret` \| `decide` \| `ideas` \| `govern`), optional repo-relative **`artifact_links`** for signals/findings/decisions latest, **`runs/ideas/latest.json`** when its `product_id` matches, and **latest escalation packet** (`runs/escalations/latest/esc_*.json` for the product, by `created_at` then path) when present; per-product `artifact_links`); **`idea_refinement_posture`** — compact booleans from `eligibility_facts` (`refinement_start_idea_eligible`, `idea_refinement_session_present_non_terminal`, `refinement_submit_reviews_in_eligible`, `refinement_run_eligible_for_idea_session`, `next_action_is_refinement_submit_reviews_in`) plus optional **`idea_session`** (`phase`, `session_id`, `status`, `current_round` from `artifacts.refinement.idea`); **`experiment_posture`** — `experiments_propose_eligible` (from `eligibility_facts` or fallback to `experiments_propose` in `eligible_action_ids`), `experiment_proposals_present`, optional `proposal_count` from **`runs/experiments/proposals/latest/<product_id>.json`**, and **`artifact_links.experiments_latest_repo_relative`** when that file exists; top-level `artifact_links` to `index.json` / optional batch artifacts; written to **`runs/orchestration/latest/fleet_governance_rollup.json`**. |
| `argus.orchestration_advancement.v1` | From **`argus orchestration advance`**: durable record under `runs/orchestration/latest/advancements/<product_id>.json` — `selected_action`, `selected_at_utc`, `transition_reason`, `source_eligibility_facts`, `action_status` (`queued` \| `blocked` \| `skipped` before **`--execute`**; with **`--execute`** and a queued selection, may become `executed` \| `failed` \| `queued_unhandled`), `snapshot_orchestration_status`, `snapshot_next_action`, optional `eligible_action_index`, optional `executed_at_utc` / `execution_detail` / `execution_error`. In-process execution only (no subprocess orchestration). |
| `argus.orchestration_progression_run.v1` | **Stdout / `--json` summary** from **`argus orchestration run-progression`**: `product_id`, `max_steps_requested`, `steps_executed`, **`step_count`** (same as `steps_executed`), `stopped_reason`, **`actions_taken[]`** (per-step `selected_action`, `action_status`, path), **`terminal_status`**, **`terminal_reason`**, `advancement_steps[]` (each step’s advancement payload + path), `final_state` (last `argus.orchestration_state.v1` evaluation); optional **`artifact_paths`** (repo-relative) when a durable artifact was written. |
| `argus.orchestration_progression_run_artifact.v1` | **Durable run record** from **`argus orchestration run-progression`** (unless **`--no-write-artifact`**): `run_id`, `product_id`, `started_at_utc`, `completed_at_utc`, `step_count`, `actions_taken`, `terminal_status`, `terminal_reason`, **`orchestration_fingerprint`**, **`final_state_summary`**, `advancement_steps`, `final_state`. Written to **`runs/orchestration/latest/progression_runs/<product_id>.json`** (latest pointer) and **`runs/orchestration/progression_runs/generations/<run_id>.json`** (timestamped copy). Repeatable checklist assertions: **`docs/proof-run.md`** (section 5). |
| `argus.orchestration_execution_feedback.v1` | **In-process orchestration outcome** (one JSON per step): `product_id`, `action_id`, **`orchestration_action_status`** (`executed` \| `failed` \| `queued_unhandled`), `success`, `finished_at_utc`, `observed_at_utc`, `execution_detail`, `execution_error`, **`provenance`** (`source` = `orchestration_step_executor`, `kind` = `in_process`, `source_ref`). Written under **`runs/execution/<product_id>/orchestration_feedback_*.json`**. **`evaluate_product_orchestration`** aggregates **latest outcome per `action_id`** within lookback (`eligibility_facts`: **`orchestration_feedback_by_action_id`**, **`orchestration_feedback_failed_action_ids`**, **`orchestration_feedback_queued_unhandled_action_ids`**, **`orchestration_feedback_recent_failed`**, **`orchestration_feedback_recent_queued_unhandled`**, plus **`orchestration_feedback_latest`** globally); **`blockers`** per failed **`action_id`**; **`waiting_inputs`** lists **`action_ids`** for queued_unhandled; escalation triggers; deprioritizes the **first eligible** action when its per-action latest status is **`failed`**, **`queued_unhandled`**, or **`executed`** (reason codes **`orchestration_failed_action_deprioritized`**, **`orchestration_unhandled_action_deprioritized`**, **`orchestration_same_action_after_executed_success`**). On **`signals collect`**, **`ExecutionAdapter`** surfaces these as **`SignalType.EXECUTION`** when **`type: execution`** is enabled. |
| `argus.orchestration_task.v1` | When **`next_action` ≠ `none`** (same write as orchestration state): **`runs/orchestration/tasks/latest/<product_id>.json`** — **`task_type`** (mapped from **`action`**), **`action`**, **`created_at`** (evaluation time), **`status`** (`pending`), **`reason_codes`**, **`blockers`**, **`source_state_ref`**, **`inputs`**, **`dependencies.artifact_paths`**; removed when **`next_action`** is **`none`**. |
| `argus.orchestration_batch_advancement.v1` | From **`argus orchestration batch-advance`**: durable **`runs/orchestration/latest/batch_advancement.json`** — selected product, advancement payload / paths, and **`batch_advancement_fairness`** (rotation / anti-starvation metadata). |
| `argus.orchestration_operator_summary.v1` | **`runs/orchestration/latest/operator_summary.json`** — compact links to **`index.json`**, **`batch_advancement.json`**, selected **`orchestration_state`**, and **`advancements/<product_id>.json`** when present (derived from the batch body; no re-evaluation). Additive **`idea_refinement_posture`** and **`experiment_posture`** (same shapes as fleet rollup per-product) plus **`compact_summary_lines`** entries when the selected product’s latest persisted state has idea-refinement or experiment-proposal signals (`experiments: propose_ready`, `experiments: proposals_present`). |

#### Phase-1 orchestration / operator contract (frozen semantics)

Stable for tooling and docs; behavior evolves only with explicit contract bumps elsewhere.

- **Canonical `action_id` strings** — Single source: **`argus/orchestrator/state_models.py`** (`ACTION_*`). In-process **`advance --execute`** / **`run-progression`** dispatch is implemented in **`step_executor.execute_orchestration_action`** for exactly: `signals_collect`, `audit_run`, `orchestration_state_refresh`, `refinement_start_product_spec`, `refinement_start_idea`, `implementation_plan_generate`, `refinement_run`, `refinement_submit_reviews_in`, `escalation_consider`, `execution_outcomes_apply`, `temporal_refresh`, `findings_generate`, `decisions_generate`, `ideas_generate`, `experiments_propose`, `experiments_prioritize`, `experiments_create`, `experiments_activate`, `experiments_evaluate`, `experiments_close_stale`, `experiments_surface_findings`, `decisions_refresh_from_surfaced_findings`, `ideas_refresh_from_surfaced_findings`, `escalation_packet_generate`. Any other `action_id` records **`queued_unhandled`** feedback. Eligibility may still surface other ids (e.g. from refinement); those are not executed in-process until mapped.
- **`orchestration_posture`** (`argus.orchestration_posture.v1`) — Additive structured execution-feedback view: **`execution_feedback_crosswalk`** (next-action vs recent failed / queued_unhandled), **`retry_reopened`** (`active`, `action_ids` — failed-deprioritize suppressed + still failed per summary), **`repeat_execution_feedback_thresholds`** (`failures` / `queued_unhandled` rows mirroring repeat-threshold **`escalation_triggers`**).
- **`orchestration_status_reason_codes`** — Sorted deduped union of: every **`reason_codes`** entry on **`waiting_inputs[]`**, plus (when applicable) **`orchestration_execution_feedback_repeated_failures`**, **`orchestration_execution_feedback_repeated_queued_unhandled`**, and **`orchestration_retry_reopened`** (parity with repeat-threshold escalation and retry-reopened posture — see **`evaluate_product_orchestration`**).
- **Cross-product prioritization / fairness** — **`emit_orchestration_batch`** attaches **`cross_product_prioritization`** to **`index.json`** (`ranked_product_ids`, `entries[]` with **`priority_rank`**, **`priority_tuple`**, **`priority_labels`**). **`orchestration_priority_tuple`** lexicographic order: repeated-failure threshold → repeated-queued-unhandled threshold → any escalation trigger → has eligible actions → retry-reopened suppression (from failed-deprioritize suppressed ids). **`batch_advancement.json`** may record **`batch_advancement_fairness`** when rotation rules apply.
- **Latest artifact chain (repo-relative)** — Per-product state: **`runs/orchestration/latest/<product_id>.json`**. Multi-product index: **`runs/orchestration/latest/index.json`** (optional **`batch_advancement_*`** repo-relative link fields when **`batch_advancement.json`** exists). **Fleet governance rollup:** **`runs/orchestration/latest/fleet_governance_rollup.json`**. Batch selection record: **`runs/orchestration/latest/batch_advancement.json`**. Operator summary: **`runs/orchestration/latest/operator_summary.json`**. Single-step intent: **`runs/orchestration/latest/advancements/<product_id>.json`** (`argus.orchestration_advancement.v1`). Progression run record: **`runs/orchestration/latest/progression_runs/<product_id>.json`** and timestamped **`runs/orchestration/progression_runs/generations/<run_id>.json`**.

#### Phase-2 orchestration (handled analysis + governance actions)

Frozen with Phase-1 dispatch documentation above: same **`action_id`** source (**`state_models.py`**) and **`step_executor.execute_orchestration_action`** surface. Phase-2 actions extend the operator path from **observe → interpret → decide → candidate ideas → experiments → govern** using **deterministic** in-process handlers (no subprocesses; orchestration **`ideas_generate`** uses **no LLM** and **no advisor expansion** by default).

**Handled Phase-2 `action_id` values (in-process):** `findings_generate`, `decisions_generate`, `ideas_generate`, `experiments_propose`, `experiments_prioritize`, `experiments_create`, `experiments_activate`, `experiments_evaluate`, `experiments_close_stale`, `experiments_surface_findings`, `decisions_refresh_from_surfaced_findings`, `ideas_refresh_from_surfaced_findings`, `escalation_packet_generate` — same dispatch surface as Phase-1 (`step_executor.execute_orchestration_action`).

**Chain position (Phase-2 experiment corridor):** After **`decisions_generate`**, the canonical **`eligible_actions`** order places **`ideas_generate`** then **`experiments_propose`** then **`experiments_prioritize`** then **`experiments_create`** then **`experiments_activate`** then **`experiments_evaluate`** then **`experiments_close_stale`** then **`experiments_surface_findings`** then **`decisions_refresh_from_surfaced_findings`** then **`ideas_refresh_from_surfaced_findings`** then **`refinement_start_idea`** (when those rows are simultaneously eligible). **`experiments_propose`** turns the post-decide spine into a **tracked-hypothesis proposal run** (not approval or execution authority). It shares the **same gating spine as `ideas_generate`** (fresh **`runs/signals/latest`**, **`runs/findings/latest/<product_id>.json`**, **`runs/decisions/latest/<product_id>.json`**). When present, ideas and refinement sessions inform proposal content; successful **`execution_detail`** may include **`ideas_latest_present_for_product`** and **`refinement_session_count`** (inspect-only context). **`experiments_prioritize`** ranks proposals from the persisted **`runs/experiments/proposals/latest/<product_id>.json`** only (no implicit re-proposal); it requires that file plus the same spine (**`experiments_prioritize_eligible`**). **`experiments_create`** materializes the **top-ranked** row from the persisted **`runs/experiments/prioritization/latest/<product_id>.json`** into **`runs/experiments/<experiment_id>.json`** (`argus.experiment.v1`) using the same quota checks as **`argus experiments create`**; it does **not** re-propose or re-prioritize (**`experiments_create_eligible`** requires ranked rows + quota). **`experiments_activate`** transitions **one** persisted experiment from **`proposed`** to **`active`** using **`runs/experiments/<experiment_id>.json`** only (deterministic selection; no re-proposal or re-prioritization; **`experiments_activate_eligible`**: same Phase-2 spine as **`experiments_evaluate`** plus ≥1 **`proposed`** experiment and registry-allowed transition). Activation is **in-repo lifecycle state** only—not external execution, rollout, spend, or shipping authority. **`experiments_evaluate`** runs the same deterministic **`run_evaluations`** path as **`argus experiments evaluate`**, updating **`runs/experiments/<experiment_id>.json`** in place with evaluation fields (**`experiments_evaluate_eligible`**: spine + at least one non-terminal experiment). **`experiments_close_stale`** marks **non-terminal** experiments **failed** when they meet an explicit age rule on **`max(created_at, start_at)`** vs evaluation time (**`stale_close_age_reference_clock_v1`**, minimum age **`argus.experiments.stale_close.STALE_CLOSE_MIN_AGE_DAYS`** days — see code); **`experiments_close_stale_eligible`**: same Phase-2 spine as **`experiments_evaluate`**, ≥1 experiment on disk, and ≥1 row matching the rule with **`can_transition(·, failed)`**. Stale close is **in-repo hygiene** only—not external shutdown or shipping authority. **`experiments_surface_findings`** writes **`runs/findings/experiment_surfaced/latest/<product_id>.json`** (`schema`: **`argus.findings_experiment_surfaced.v1`**) — a **derived** sidecar of finding-shaped rows from persisted experiment JSON (stable id **`exp_surface:<product_id>:<experiment_id>`** per experiment; dedupe on rerun; **`evidence.provenance`** = **`experiment_surfaced`**, **`evidence.synthetic`** = true, **`evidence.surfacing_rule`**). It does **not** replace **`runs/findings/latest/<product_id>.json`**; **`decisions_generate`** / assessment merge canonical findings with this sidecar when present. **`experiments_surface_findings_eligible`**: same Phase-2 spine as **`ideas_generate`**, ≥1 persisted experiment, ≥1 with terminal **`completed`/`failed`** status and/or non-empty **`last_evaluation_verdict`**. **`decisions_refresh_from_surfaced_findings`** re-materializes **`runs/decisions/latest/<product_id>.json`** and **`runs/decision_assessment/latest/<product_id>.json`** using the same merged finding input as **`decisions_generate`** (**`runs/findings/latest/<product_id>.json`** + **`runs/findings/experiment_surfaced/latest/<product_id>.json`**); it does not re-run experiments or collect new evidence. **`decisions_refresh_from_surfaced_findings_eligible`**: fresh signals, loadable canonical findings latest, valid experiment-surfaced sidecar with ≥1 loadable finding, and sidecar **`generated_at_utc`** strictly **after** **`runs/decisions/latest/<product_id>.json`** when that file exists (see **`eligibility_facts`**). **`ideas_refresh_from_surfaced_findings`** re-runs the ideas pipeline with **merged** canonical + experiment-surfaced findings as the finding-derived input (same bounded orchestration LLM/advisor flags as **`ideas_generate`**); **`ideas_refresh_from_surfaced_findings_eligible`**: same spine as **`ideas_generate`**, valid experiment-surfaced sidecar with ≥1 loadable **`exp_surface`** finding, and **`generated_at_utc`** on the sidecar strictly **after** **`runs/ideas/latest.json`** for the product when that file exists (see **`eligibility_facts`**).

| `action_id` | Loop role | Narrow eligibility (inspect **`reason_codes`** / **`eligibility_facts`**) | Primary artifact schema(s) written |
|-------------|-----------|-----------------------------------------------------------------------------|-----------------------------------|
| `findings_generate` | **Interpret** — rule-derived findings from **`runs/signals/latest`** | Fresh signals bundle (`findings_generate_signals_ready`) | `argus.findings_bundle.v1` |
| `decisions_generate` | **Decide** — lifecycle assessment + ranked candidates | Fresh signals + **`runs/findings/latest/<product_id>.json`** (`decisions_generate_findings_ready`) | `argus.decisions_bundle.v1`; assessment sidecar **`argus.decision_context_assessment.v1`** under **`runs/decision_assessment/latest/`** |
| `ideas_generate` | **Candidate backlog** — deterministic ideas bundle (not approval or execution authority) | Fresh signals + **`runs/findings/latest`** + **`runs/decisions/latest/<product_id>.json`** (`ideas_generate_decisions_ready`; see **`ideas_generate_eligible`**) | `argus.ideas_bundle.v1` under **`runs/ideas/`** (including **`runs/ideas/latest.json`**) |
| `experiments_propose` | **Experiments** — deterministic experiment proposal run (candidate tracked hypotheses; not approval or execution authority) | Same post-decide spine as **`ideas_generate`** (`experiments_propose_eligible`; **`reason_codes`** include **`experiments_propose_ready`**) | **`argus.experiment_proposals_run.v1`** under **`runs/experiments/proposals/latest/<product_id>.json`**. On success, **`execution_detail.schema`** is **`argus.experiment_proposals_run.v1`**; expect **`proposals_latest_path`** (repo-relative), optional **`proposal_artifact_paths`**, **`proposal_count`**, **`authority_note`**. |
| `experiments_prioritize` | **Experiments** — deterministic ranking of persisted proposals (advisory; not approval or execution authority) | Post-decide spine **and** **`runs/experiments/proposals/latest/<product_id>.json`** present (`experiments_prioritize_eligible`; **`reason_codes`** include **`experiments_prioritize_ready`**) | **`argus.experiment_prioritization_run.v2`** under **`runs/experiments/prioritization/latest/<product_id>.json`**. On success, **`execution_detail.schema`** is **`argus.experiment_prioritization_run.v2`**; expect **`prioritization_latest_path`**, **`ranked_count`**, optional **`top_experiment_id`** / **`top_score`**, **`authority_note`**. |
| `experiments_create` | **Experiments** — materialize top-ranked proposal to a tracked experiment JSON (in-repo work object; not external execution) | Same spine as **`experiments_prioritize`**, **`runs/experiments/prioritization/latest/<product_id>.json`** with ≥1 ranked row, **`check_experiment_create_allowed`** (`experiments_create_eligible`; **`experiments_create_ready`**) | **`argus.experiment.v1`** under **`runs/experiments/<experiment_id>.json`**. On success, **`execution_detail.schema`** is **`argus.experiment.v1`**; expect **`experiment_id`**, **`experiment_path`**, **`source_prioritization_path`**, **`source_proposal_id`**, **`source_rank`**, **`authority_note`**, **`quota`** summary; optional **`dedupe_skipped`** when an experiment with the same **`source_proposal_id`** already exists. |
| `experiments_activate` | **Experiments** — move one persisted experiment **`proposed` → `active`** (in-repo lifecycle only; not execution authority) | Same Phase-2 spine as **`experiments_evaluate`**, ≥1 persisted experiment, ≥1 **`proposed`**, registry allows **`proposed` → `active`** (`experiments_activate_eligible`; **`experiments_activate_ready`**) | **`argus.experiment.v1`** updated in place under **`runs/experiments/<experiment_id>.json`**. On success, **`execution_detail.schema`** is **`argus.experiment.v1`**; expect **`experiment_id`**, **`experiment_path`**, **`previous_status`**, **`new_status`**, **`selection_rule`** (and optional **`source_rank`** / **`source_proposal_id`** when prioritization applies), **`authority_note`** (in-repo state only). |
| `experiments_evaluate` | **Experiments** — deterministic evaluation of non-terminal experiments (analysis/feedback; not execution authority) | Phase-2 spine (**`ideas_generate`**-equivalent gates) and ≥1 **non-terminal** experiment JSON (`experiments_evaluate_eligible`; **`experiments_evaluate_ready`**) | Logical evaluation schema **`argus.experiment_evaluation.v1`**; durable updates are **`argus.experiment.v1`** files under **`runs/experiments/<experiment_id>.json`** ( **`last_evaluation_*`** fields). On success, **`execution_detail.schema`** is **`argus.experiment_evaluation.v1`**; expect **`evaluation_artifact_paths`**, **`evaluated_count`**, optional **`top_experiment_id`** / **`top_composite_score`**, **`authority_note`**. |
| `experiments_close_stale` | **Experiments** — fail **stale** non-terminal experiments (in-repo lifecycle hygiene; not external shutdown authority) | Same Phase-2 spine as **`experiments_evaluate`**, ≥1 persisted experiment, ≥1 matches **`stale_close_age_reference_clock_v1`** (`experiments_close_stale_eligible`; **`experiments_close_stale_ready`**) | **`argus.experiment.v1`** updated in place. On success, **`execution_detail.schema`** is **`argus.experiment.v1`**; expect **`affected_experiment_ids`**, **`experiment_paths`**, **`status_transitions`**, **`stale_close_rule`**, **`stale_close_min_age_days`**, **`authority_note`**. |
| `experiments_surface_findings` | **Experiments** — project persisted experiment outcomes into finding-shaped **derived** evidence (in-repo; not telemetry or execution authority) | Phase-2 spine (**`ideas_generate`**-equivalent gates), ≥1 experiment JSON, ≥1 usable outcome (`experiments_surface_findings_eligible`; **`experiments_surface_findings_ready`**) | **`argus.findings_experiment_surfaced.v1`** under **`runs/findings/experiment_surfaced/latest/<product_id>.json`**. On success, **`execution_detail.schema`** matches; expect **`experiment_surfaced_latest_path`**, **`surfaced_finding_count`**, **`affected_experiment_ids`**, **`surfacing_rule`**, **`authority_note`** (projection only). |
| `decisions_refresh_from_surfaced_findings` | **Decide** — re-materialize decisions + decision assessment from merged persisted findings only (deterministic; not new external evidence) | Fresh signals, canonical findings latest, ≥1 loadable surfaced finding, sidecar **`generated_at_utc`** newer than decisions latest when present (`decisions_refresh_from_surfaced_findings_eligible`; **`decisions_refresh_from_surfaced_findings_ready`**) | **`argus.decisions_bundle.v1`** under **`runs/decisions/latest/<product_id>.json`** (timestamped generation path); **`argus.decision_context_assessment.v1`** under **`runs/decision_assessment/latest/<product_id>.json`**. On success, **`execution_detail`** includes **`schema`**, **`decisions_latest_path`**, **`generation_path`**, **`assessment_latest_path`**, **`candidate_count`**, **`surfaced_findings_used_count`**, **`lifecycle_stage`**, **`authority_note`**. |
| `ideas_refresh_from_surfaced_findings` | **Ideas** — re-materialize ideas bundle using merged canonical + experiment-surfaced findings (deterministic; not new external evidence) | Same Phase-2 spine as **`ideas_generate`**, ≥1 loadable surfaced finding, sidecar **`generated_at_utc`** newer than ideas latest when present (`ideas_refresh_from_surfaced_findings_eligible`; **`ideas_refresh_from_surfaced_findings_ready`**) | **`argus.ideas_bundle.v1`** under **`runs/ideas/`** (timestamped + **`latest.json`**). Bundle **`meta.ideas_findings_input`** records merged mode and **`surfaced_findings_used_count`**. On success, **`execution_detail`** includes **`schema`**, **`ideas_latest_path`**, **`generation_path`**, **`idea_count`**, **`surfaced_findings_used_count`**, **`authority_note`**. |
| `escalation_packet_generate` | **Govern** — durable escalation packet (policy + orchestration triggers) | **`runs/findings/latest`** + structural escalation posture (`escalation_packet_generate_posture_ready`); excludes execution-feedback-only triggers | `argus.escalation_packet.v1` under **`runs/escalations/latest/`** |

**Relation to Phase-1:** `escalation_consider` records an **operator posture snapshot** under **`runs/orchestration/escalation_consider/<product_id>/latest.json`**. **`escalation_packet_generate`** uses the same **`generate_packet_for_product`** path as **`argus escalation generate`** and writes **`runs/escalations/`** when triggers fire (dedupe may skip a write with **`executed`** + **`dedupe_skipped`** in **`execution_detail`**).

**Progression proof:** On a minimal product tree, **`argus orchestration run-progression --execute`** may execute **`signals_collect` → `findings_generate` → `decisions_generate`** before a fingerprint stop; with audit/temporal fixtures that keep the Phase-2 spine eligible, the same progression may continue through **`ideas_generate` → `experiments_propose` → `experiments_prioritize` → `experiments_create` → `experiments_activate` → `experiments_evaluate` → `experiments_close_stale`** (when those actions are eligible; deterministic order among simultaneously eligible chain members); see **`tests/test_orchestration_progression.py`** and **`docs/proof-run.md`** (Phase-2 contract pytest).

### Temporal artifacts (schemas)

| Schema | Role |
|--------|------|
| `argus.temporal_bundle.v1` | Derived per-product recency view under `runs/temporal/`; `signals[]` mirror `TemporalSignal` JSON; **`worst_freshness_status`** when the writer aggregates rows. |
| `argus.temporal_signal.v1` | Serialized `TemporalSignal` (includes `freshness_score`, `freshness_bucket`; **`freshness_status`** when enriched). |
| `argus.temporal_summary.v1` | `argus temporal summary` aggregate. |
| `argus.dashboard_temporal.v2` | Dashboard aggregate temporal block (per-product rollups + portfolio summary). |
| `argus.doctor_temporal.v1` | Doctor temporal checks. |
| `argus.idea.v2` | Structured idea row (`argus.idea_generation.models.Idea`). |
| `argus.ideas_bundle.v1` | `runs/ideas/latest.json` generation output (`IdeasBundle`). |
| `argus.dashboard_ideas.v1` | Dashboard JSON slice built from the ideas bundle (see `argus.dashboard.ideas_block`). |
| `argus.decision_context_assessment.v1` | `DecisionContextAssessment` in `runs/decision_assessment/latest/<id>.json` and embedded `decision_context` on decision bundles. May include optional **`advisor_alignment_score`**, **`advisor_conflict_flag`**, **`advisor_summary`** when advisor/council artifacts exist ([llm-integration.md](llm-integration.md)). |

Product binding: snapshot paths may use synthetic ids **`_global_`** and **`_portfolio_`** for non-product trees; collection bundles are keyed by real `product_id` or those synthetic ids consistently.

### Product audit (multi-angle)

| Schema | Role |
|--------|------|
| `argus.audit_bundle.v1` | `runs/audit/<product_id>/bundle.json` — nine `angles` keys (always); per-angle `angle_status`, `summary_lines` (≥1); `inputs_fingerprint_by_angle`, `inputs_fingerprint_bundle`. |
| `argus.audit_summary.v1` | Legacy Product Gap only: `runs/audit/<product_id>/latest.json` (idea gating, `load_latest_audit`). |
| `argus.audit_angle.<name>.v1` | Per-angle payload under `bundle.angles` (nine keys; each angle has its own schema id). |
| `argus.audit_cursor_scan.v1` | Optional under `bundle.angles.<id>.cursor_scan`. **Strict ingest validation** — required fields: `angle_id`, `summary_lines`, `findings`, `risks`, `enhancements`, `confidence`, `repo_evidence_refs`, `limitations`, `provenance` (`cursor_codebase_scan`). See [audit-cursor-scan-contract.md](audit-cursor-scan-contract.md). |
| `argus.audit_cursor_scan_batch.v1` | Ingest file: `{ "schema", "angles": { "<angle_id>": <audit_cursor_scan.v1> } }`. |
| `argus.audit_cursor_scan_view.v1` | **CLI output only** (`argus audit show <id> --cursor-scan`): `product_id`, `inputs_fingerprint_bundle`, `angles` map with each value the `cursor_scan` object or `null`. |

### Signals — optional Cursor interpretation (sidecar)

| Schema | Role |
|--------|------|
| `argus.signal_cursor_review.v1` | Ingest file root for `argus signals cursor-ingest` — `product_id`, `summary_lines`, `findings`, `risks`, `enhancements`, `confidence`, `repo_evidence_refs`, `limitations`, `provenance` (`cursor_signal_review`). |
| `argus.signal_review_bundle.v1` | Written under `runs/signals/review/<product_id>.json` — references deterministic bundle fingerprint (`deterministic_signals_fingerprint`), `signal_review`, `ingest_history` on reruns. |

### Orchestration — optional Cursor interpretation (sidecar)

| Schema | Role |
|--------|------|
| `argus.orchestration_cursor_review.v1` | Ingest root for `argus orchestration cursor-ingest` — same shape as signal cursor review (`summary_lines`, `findings`, `risks`, `enhancements`, `confidence`, `repo_evidence_refs`, `limitations`, `provenance` must be `cursor_orchestration_review`). |
| `argus.orchestration_review_bundle.v1` | Written under `runs/orchestration/review/<product_id>.json` — references `deterministic_orchestration_fingerprint` (content of `runs/orchestration/latest/<id>.json` excluding wall-clock `evaluated_at_utc`), `orchestration_review`, `ingest_history` on reruns. Does **not** modify `latest/<id>.json`. |

## Deterministic core vs Cursor-enhanced audit

| Layer | Role |
|-------|------|
| **Deterministic audit** | `argus audit run` — local, bounded scans; nine angle keys in `runs/audit/<product_id>/bundle.json` with `angle_status` and `summary_lines`. No Cursor CLI required. |
| **Optional Cursor scan ingest** | Operator runs `argus audit prompt` / `argus audit cursor-prompt`, pastes JSON from Cursor, then **`argus audit ingest-cursor`** or **`ingest-agent`** `--file …` to merge validated **`cursor_scan`** blocks. Inspect with **`argus audit show <id> --cursor-scan`**. A later `argus audit run` **preserves** existing `cursor_scan` (`merge_deterministic_with_prior_cursor`). |

## Deterministic signals vs optional Cursor signal review

| Layer | Role |
|-------|------|
| **Deterministic collection** | `argus signals collect` — adapters + manifest reconciliation → `runs/signals/latest/<id>.json` (`argus.signal_collection.v1`). No LLM calls in Argus. |
| **Optional Cursor signal review** | Operator runs `argus signals cursor-prompt`, then `argus signals cursor-ingest --file …` to write **`runs/signals/review/<id>.json`** (`argus.signal_review_bundle.v1`) with validated **`signal_review`** (`argus.signal_cursor_review.v1`, `provenance: cursor_signal_review`). Does **not** modify `runs/signals/latest/`. |

## Deterministic orchestration vs optional Cursor orchestration review

| Layer | Role |
|-------|------|
| **Deterministic state** | `argus orchestration state` / `advance` — eligibility and advancement from `runs/*` artifacts (`argus.orchestration_state.v1` under `latest/`). No LLM in Argus. |
| **Optional Cursor review** | Operator runs `argus orchestration cursor-prompt`, then `argus orchestration cursor-ingest --file …` to write **`runs/orchestration/review/<id>.json`** (`argus.orchestration_review_bundle.v1`) with validated **`orchestration_review`**. Does **not** modify `runs/orchestration/latest/`. |

Refinement **grounded** reviews are file-based (`reviews_in/round_*.json`); `backend_used: cursor` marks the council profile, not a separate Argus network call for that path.

## Shared vocabulary (cohesion)

Use these terms consistently across code and docs:

| Term | Primary definition |
|------|---------------------|
| **confidence** | `DecisionContextAssessment.confidence_score` — trust in the evidence base, not optimism. |
| **uncertainty** | `uncertainty_score` — unknown/conflicted state. |
| **risk** | `risk_score` — cost, severity, lifecycle posture. |
| **freshness** | Operational signal recency (`argus.decision.freshness`) vs **collection recency** (`runs/temporal/`); do not mix with LLM “confidence.” |
| **novelty** / **diversity** | Idea pipeline only (`argus.idea_generation`); not interchangeable with decision confidence. |
| **escalation_pressure** | Assessment + autonomy/escalation packets; deduped writes where configured. |
| **orchestration_status** | On **`argus.orchestration_state.v1`**: `eligible`, `blocked_waiting_input`, `blocked_waiting_approval`, `stale_refresh_needed`, `complete`, `escalated` — mutually exclusive headline (see **`waiting_inputs[]`** for detail rows). |
| **waiting_inputs** | Structured rows on orchestration state (`kind`, `reason_codes`, …); not the same as **`blockers`** (explicit gating list on the same bundle). |
| **next_action** | Prioritized **`action_id`** from eligibility, or **`none`**; when not `none`, **`tasks/latest/`** mirrors it. |
| **selected_action** | Field on **`argus.orchestration_advancement.v1`** (intent record from **`orchestration advance`**), not on the state schema. |
| **orchestration_review** | Validated **`argus.orchestration_cursor_review.v1`** payload inside **`argus.orchestration_review_bundle.v1`** (optional sidecar). |

## Types not duplicated

- **`ProductNode`**, **`SignalRecord`**, **`Finding`**, **`DecisionCandidate`**: only in `argus.core.models`.
- **`Experiment`** (tracked hypothesis): `argus.experiments.models` — distinct from **`ExperimentProposal`** (suggestion only).
- **`ActionContract`**: `argus.actions.models`.
- **`EscalationPacket`**: `argus.escalation.models`.
- **`TrendSummary`**: `argus.trends.models` (analysis output, not a core duplicate).

## Self-audit vs core findings

**`SelfFinding`** (`argus.self.findings`) is a separate critique / meta-audit shape for operator-facing reports. It is intentionally not merged into **`Finding`** to avoid conflating product signals with repo-health signals. Capability gaps may appear in both capability evaluation and self-audit; the former is structured registry-based, the latter is narrative prioritization (deduped in code where noted).

## Serialization

Use **`argus.core.serialize`** (`to_jsonable`, `dumps_json`) for dataclasses and stable JSON. Persisted bundles declare a `schema` string where applicable (e.g. `argus.findings_bundle.v1`).
