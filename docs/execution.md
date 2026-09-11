# Execution (opt-in subprocess)

Argus **execution** runs an `ActionContract` as a subprocess only when explicitly enabled and after validation, autonomy, and (usually) approval.

## Entry points

- **`argus execution dry-run`** — load contract, dry-run, sandbox check; no subprocess.
- **`argus execution run`** — requires `--enable-execution` or `ARGUS_EXECUTION_ENABLED=1`, or autonomous path with `ARGUS_AUTONOMOUS_SAFE_EXECUTION` / `--autonomous` where applicable.
- **`argus actions execute`** — shell-oriented path using `/bin/sh -c`; same gates.

## Artifacts

- Runs persist under `runs/execution/<run_id>/` (`run.json`, stdout/stderr logs).
- Feedback: execution snapshots can be ingested as **signals** (see `signals-and-adapters.md`).

## ID consistency

- `run_id` — `exec_<timestamp>_<hex>` (`argus.execution.engine.new_run_id`).
- `action_id` / `product_id` — come from the contract; tie to approvals and autonomy state.

## Safety stack (order matters)

1. Load + structural validation  
2. Dry-run + sandbox (`validate_execution_sandbox`)  
3. **Autonomy** (`enforce_autonomy_or_raise` in `run_subprocess`)  
4. **Approval** (`require_execution_approval`) unless auto-rules apply  
5. Subprocess  

Nothing here executes network calls or cloud APIs by default—only the declared command line.

## Provider cleanup (shutdown / archive)

Autonomous **shutdown** includes a structured **`plan_provider_cleanup`** seam (`argus/autonomy/provider_cleanup.py`) and an **`ArchivePlan`** in shutdown reports (`archive_plan` on `ProductShutdownReport`). Defaults remain **no-op stubs**; wiring boto3, DNS, or billing teardown belongs in capability-approved execution paths, not silent CLI side effects.

Product scaffold **`scripts/*.sh`** hooks exit successfully without network access; replace with real commands only where policy allows.

## Cohesion

Execution does **not** re-derive decision confidence—contracts carry `product_id` and autonomy re-checks policy. For **shutdown**, external teardown is **never** implied by `execution run`; use explicit provider integration after human approval (see [stub-inventory.md](stub-inventory.md)).
