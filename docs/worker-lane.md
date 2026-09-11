# Worker lane (Argus-native)

## Steward vs worker boundary

| Role | Responsibility |
|------|----------------|
| **Argus core (steward)** | Decides **what** should happen next: priorities, product focus, risk posture, approval gates, and the **canonical rationale** tied to local artifacts. Issues **work orders** as the only approved handoff. |
| **Worker lane (executor)** | **Builds and executes** against an explicit work order: applies patches, runs bounded tooling, reports results. **Does not** re-rank the portfolio, substitute goals, or invent new scope beyond the order. |

This split keeps strategy and evidence in Argus; the worker remains a deterministic executor with a clear contract.

## Work orders (`argus.work_order.v1`)

A **work order** is the canonical JSON artifact from core → worker. It is **traceable and inspectable** (paths recorded under `source_artifact_paths`, acceptance criteria enumerated, risks stated).

Typical fields include:

- **Identity:** `work_order_id`, `product_id`, `request_type`
- **Provenance:** `selected_at_utc`, `selected_by`
- **Governance:** `priority`, `autonomy_mode`, `approval_required`, `status`
- **Intent:** `rationale`, `acceptance_criteria`, `implementation_seed`
- **Context:** `mission_context` (when available), `risk_notes`, `recommended_worker_mode`
- **Traceability:** `source_artifact_paths` (repo-relative or documented paths to Argus outputs)

Schema constant: **`argus.work_order.v1`** (see `argus/worker/work_orders.py`).

## Artifact paths

Under `runs/worker/work_orders/`:

| Path | Purpose |
|------|---------|
| `<work_order_id>.json` | Stamped work order (canonical id) |
| `<work_order_id>.md` | Markdown companion for humans |
| `latest/<product_id>__<request_type>.json` | Latest work order for that product + request type (and `.md`) |
| `actions/<work_order_id>/<action_id>.json` | Steward transition audit (see actions section) |

Creating or updating work orders is done via **`write_work_order_artifacts`** in code.

## Work order actions (`argus.work_order_action.v1`)

Approval and activation are **durable and inspectable**: steward transitions are **not** written into the stamped work order JSON. Instead, each transition appends a normalized record under **`runs/worker/work_orders/actions/<work_order_id>/<action_id>.json`**.

**Schema:** **`argus.work_order_action.v1`** — fields include `action_id`, `work_order_id`, `action_type`, `acted_at_utc`, `acted_by`, `note`, `previous_status`, `resulting_status`.

**Explicit action types (v1):**

| `action_type` | From (effective status) | To |
|---------------|---------------------------|-----|
| `approve` | `pending_approval` | `approved` |
| `reject` | `pending_approval` or `approved` | `rejected` |
| `cancel` | `pending_approval` or `approved` | `cancelled` |
| `reopen` | `rejected` or `cancelled` | `pending_approval` |

Invalid transitions fail with a clear error (no silent mutation).

### Effective status when loading

`find_work_order`, `load_latest_work_orders`, and `load_latest_work_order_for` **merge** action history into the returned dict:

- **`status`** — effective status after folding all valid actions in chronological order.
- **`status_stamped`** — the `status` field stored in the stamped JSON (issuance snapshot only).
- **`action_history`** — condensed list of actions for quick inspection (full notes remain on disk per action file).

The worker executor uses **`status`** (effective), not the stamped file alone.

### CLI (steward transitions)

```bash
argus worker approve-work-order --work-order-id <wo_...> [--note "..."] [--acted-by <id>]
argus worker reject-work-order --work-order-id <wo_...> [--note "..."]
argus worker cancel-work-order --work-order-id <wo_...> [--note "..."]
argus worker reopen-work-order --work-order-id <wo_...> [--note "..."]
```

- **`--json`** — emit `argus.worker.work_order_action_report.v1`.
- **`--no-save`** — validate the transition only; do not write under `actions/`.

Implementation: **`argus/worker/work_order_actions.py`** — **`apply_work_order_action`**.

## Worker execution

**Module:** `argus/worker/execute_work_order.py`

The worker lane consumes **exactly one** work order per invocation and emits **`argus.worker_execution_outcome.v1`**. It does **not** pick queue order, batch multiple orders, or call the Planner. Argus core remains the only decision-maker; the worker **applies** what the order describes for supported request types.

### Selection

- **`--work-order-id`** — load `runs/worker/work_orders/<id>.json`.
- **`--product-id`** + **`--request-type`** — load `runs/worker/work_orders/latest/<product_id>__<sanitized_request_type>.json` (same filename rules as work order writes).

Do not pass both modes at once.

### Status rules (v1)

| Work order `status` | Default behavior |
|---------------------|------------------|
| `approved` | Eligible for execution (see supported request types below). |
| `pending_approval` | **Blocked** — use **`approve-work-order`** (or `--allow-pending-approval` for non-default runs). |
| `rejected` | **Blocked** — steward declined; use **`reopen-work-order`** if review should resume. |
| `cancelled` | **Blocked** — order was withdrawn; **`reopen-work-order`** only if cancellation was mistaken. |

**Escape hatch:** `--allow-pending-approval` allows `pending_approval` orders to run (tests or tightly controlled environments only). There is no v1 override for **`rejected`** or **`cancelled`**.

### Supported request types (v1)

| `request_type` | Behavior |
|----------------|----------|
| **`signal_instrumentation`** | **Real builder:** applies a minimal observability contract (see **`argus/products/apply_signal_instrumentation.py`**). Writes or updates **`signals.yaml`**, may merge suggested **`metrics.primary`** key refs, re-runs instrumentation evaluation for validation. Persists **`argus.product_signal_instrumentation_apply.v1`** under `runs/products/signal_instrumentation_apply/`. |
| **Anything else** | **Blocked** — no builder wired yet; execution outcome explains that only `signal_instrumentation` is implemented. |

### Builder behavior

- **`worker_mode`: `signal_instrumentation_apply`** — product contract mutation path (deterministic; no LLM; no edits outside manifest / primary metric refs and contract anchors).
- **`worker_mode`: `dry_skeleton`** — when **`--no-save`** skips all writes: instrumentation apply is not run against the product tree (no `signals.yaml` / artifact persistence).

The outcome may include **`instrumentation_apply`**: the full **`argus.product_signal_instrumentation_apply.v1`** payload when the request type is `signal_instrumentation`.

**Argus core (read-only):** The same apply schema under `runs/products/signal_instrumentation_apply/latest/<product_id>.json` is merged with the latest **scan** artifact for portfolio lifecycle, operator queue, dashboards, and autonomy memory — **no** extra worker runs; descriptive refinement only (see [product-signal-instrumentation.md](product-signal-instrumentation.md), [portfolio-lifecycle.md](portfolio-lifecycle.md)).

### Outcome artifact

Schema: **`argus.worker_execution_outcome.v1`**. Fields include: `execution_id`, `work_order_id`, `product_id`, `request_type`, `started_at_utc`, `finished_at_utc`, `worker_mode`, `execution_status` (`success` \| `failed` \| `partial` \| `blocked`), `implementation_plan_summary`, `implementation_spec_summary`, `files_touched`, `validation_summary`, `notes`, `source_work_order_path`, optional **`instrumentation_apply`**.

CLI/report wrapper schema: **`argus.worker.execute_work_order_report.v1`** (includes `outcome`, `saved_paths`, `exit_code`, `error`, `inputs`).

### Output paths

| Path | Purpose |
|------|---------|
| `runs/worker/executions/<execution_id>.json` | Stamped execution outcome |
| `runs/worker/executions/<execution_id>.md` | Markdown companion |
| `runs/worker/executions/latest/<product_id>__<request_type>.json` | Latest outcome for that product + request (and `.md`) |

If the work order cannot be loaded (missing file), a stamped outcome may still be written under `runs/worker/executions/` with **empty** `product_id` / `request_type` when unknown; **`latest/`** is skipped in that case.

### CLI

```bash
argus worker execute-work-order --work-order-id <wo_...>
argus worker execute-work-order --product-id <id> --request-type <type>
argus worker execute-work-order --work-order-id <id> --json
argus worker execute-work-order --work-order-id <id> --no-save
argus worker execute-work-order --work-order-id <id> --products-dir /path/to/parent/of/products
```

- **`--json`** — emit `argus.worker.execute_work_order_report.v1`.
- **`--no-save`** — evaluate only; no writes under `runs/worker/executions/`.
- **`--products-dir`** — recorded in report `inputs` for consistency with other worker commands (v1 dry path does not require it).

### Limits (explicit)

- One work order per run; no queue consumption or prioritization inside the worker.
- No autonomous invocation loop; no Planner integration.
- Only **`signal_instrumentation`** performs product mutation; scope is **observability contract** surfaces (manifest / primary refs), not business logic refactors.

## Signal instrumentation → work orders (Argus core issuance)

When latest **`argus.product_signal_instrumentation.v1`** shows **`instrumentation_status`** in **`weak`**, **`sparse`**, or **`missing`** (and the payload is **`ok`**), Argus core may issue a **`signal_instrumentation`** work order (`request_type` = `signal_instrumentation`, `selected_by` = `argus_core`). Products assessed as **`adequate`** do **not** receive an instrumentation work order from this issuer.

Implementation: **`argus/products/instrumentation_work_orders.py`** — **`issue_instrumentation_work_orders`** / **`evaluate_instrumentation_work_order_issuance`**. Rationale is grounded in the instrumentation artifact; optionally, if portfolio lifecycle synthesis already lists the product under instrumentation pressure, one extra cross-check sentence is added (and `runs/portfolio/lifecycle/latest.json` may be listed in `source_artifact_paths`). Mission id / structured mission from **`product.yaml`** are threaded into **`mission_context`** when present.

This **does not** change product code, run a worker, or auto-approve execution — it only materializes inspectable work orders under `runs/worker/work_orders/`.

## CLI (inspection)

```bash
argus worker show-work-orders
argus worker show-work-orders --json
argus worker show-work-orders --product-id <id>
argus worker show-work-orders --work-order-id <wo_...>
argus worker show-work-orders --products-dir /path/to/parent/of/products
```

Read-only: lists `latest/*.json` or loads one order by id. Does **not** mutate product trees. For execution, use **`execute-work-order`** (above).

## CLI (issue instrumentation work orders)

```bash
argus worker issue-instrumentation-work-orders
argus worker issue-instrumentation-work-orders --json
argus worker issue-instrumentation-work-orders --no-save
argus worker issue-instrumentation-work-orders --product-id <id>
argus worker issue-instrumentation-work-orders --products-dir /path/to/parent/of/products
```

- **`--no-save`** — evaluate and print only; no writes under `runs/worker/work_orders/`.
- Requires existing **`runs/products/signal_instrumentation/latest/<product_id>.json`** (run `argus products instrument-signals` first).

Issuance payload schema: **`argus.instrumentation_work_order_issuance.v1`**.

## Intended evolution

1. Argus portfolio / policy / operator surfaces converge on **what** to do next.
2. Core (or an approved operator path) calls **`create_work_order`** + **`write_work_order_artifacts`** when a unit of work is ready to hand off.
3. The worker invokes **`execute_work_order`** (or a future real builder) per **explicit** selection; optional automation may **schedule** calls but must not substitute Argus’s prioritization inside the worker.
4. Argus remains authoritative on **whether** an order should exist; the worker reports **how** execution completed.

**Current scope:** contract + work order persistence + instrumentation issuance + inspection CLI + **v1 dry execution** with structured outcomes (no autonomous worker loop, no real patch application in v1).

## See also

- Repository operating principles: `docs/argus-context/08-operating-principles.md`
- Execution safety elsewhere: `docs/execution.md`, `argus approval` / autonomy docs
