# Argus operator dashboard (browser console)

Local-first **Streamlit** UI that reads Argus **operator artifacts** from disk (`runs/**/latest.json`). It does not execute pipelines; it reflects whatever was last written by CLI or batch jobs.

The first tab, **Governance & actions**, is **actionable**: it triages products via `phase1_summary_for_product`, lists pending Phase 2 approvals (with responses through `record_operator_response`), shows recent `runs/execution/*/run.json` governed outcomes, and offers safe buttons that invoke `python -m argus …` or `advance_orchestration` (dry advance) — the same pathways as the CLI.

## What it shows

Tabs (left to right): **Governance & actions**, **Autonomous session**, **Needs you**, **Overview**, **Queue**, **Lifecycle**, **Learning**, **Interventions**.

Each section maps to a known path under the repo root:

| Artifact key | Path |
|--------------|------|
| **Runner service heartbeat** | `runs/portfolio/runner_service/latest.json` |
| **Autonomous runner session** | `runs/portfolio/autonomous_runner/latest.json` |
| **Escalation inbox (“Needs you”)** | `runs/portfolio/escalation_inbox/latest.json` |
| Operator summary | `runs/dashboard/operator_summary/latest.json` |
| Narrative | `runs/dashboard/narrative/latest.json` |
| Operator queue | `runs/portfolio/operator_queue/latest.json` |
| Portfolio lifecycle | `runs/portfolio/lifecycle/latest.json` |
| Portfolio strategy | `runs/portfolio/strategy/latest.json` |
| Learning synthesis | `runs/policy/learning_synthesis/latest.json` |
| Intervention inbox | `runs/portfolio/intervention_inbox/latest.json` |

Missing files, invalid JSON, or non-object roots are handled gracefully: the UI shows an empty state and the sidebar lists artifact status.

Loader and view-model logic live in `argus/dashboard/console_data.py` (tests: `tests/test_operator_console_data.py`). Governance helpers live in `argus/dashboard/governance_data.py` and `argus/dashboard/governance_ui.py` (tests: `tests/test_governance_dashboard_data.py`). The Streamlit entrypoint is `argus/dashboard/app.py`.

### Autonomous session tab (operator console)

This tab has two stacked panels: **runner service heartbeat** (cadence wrapper) and **latest autonomous session**. Both are **read-only**: they reflect whatever was last written to disk.

#### Runner service heartbeat (top of tab)

If you use **`argus portfolio run-service`**, Argus writes **`argus.portfolio_runner_service.v1`** to `runs/portfolio/runner_service/latest.json` (plus stamped copies on transitions). The dashboard shows:

- **Status badges** — `current_status` (e.g. `running`, `sleeping`, `stopped`) and, when finished, `stop:<reason>` (e.g. `stop:completed`). If the file says the service is still `running` / `sleeping` / `starting` but the heartbeat has not been updated in a while, a **stale** warning appears (`heartbeat_stale` badge + explanation).
- **Metrics** — loop count (sessions completed in this service invocation), heartbeat age in seconds, cadence interval from `inputs.interval_seconds`, and whether an embedded `last_autonomous_session` payload is present on the JSON (unusual unless you used `--include-autonomous-session`).
- **Timing** — service run id, started/finished/updated UTC, last autonomous run window, last session id, last autonomous stop reason, **next planned run** (or a caption when none is scheduled).
- **Service stop** — when `current_status` is `stopped`, service-level `stop_reason` and `stop_reason_codes` (distinct from the autonomous session below).
- **Last run summary** — truncated `last_run_summary` from the heartbeat payload.
- **Raw JSON** expander for the full heartbeat.

**What to look at first:** If you run the portfolio on a **schedule**, check this block before the autonomous session: **running** vs **sleeping** vs **stopped**, **next planned run**, and any **stale** warning. If the heartbeat is missing, the service has not been started or `--no-save` was used.

#### Latest autonomous session (below the divider)

This panel centers on the **latest bounded autonomous session** produced by `argus portfolio run-autonomous` (or invoked inside `run-service`). 

**What it shows**

- **Status badges** — e.g. `dry_run`, `allow_promotion`, and an outcome label such as `completed`, `stopped_quiescent`, `stopped_intervention_heavy`, `stopped_manual`, `stopped_pipeline_error` (derived from `stop_reason` and inputs).
- **Session metadata** — `session_id`, started/finished UTC timestamps, `cycles_run`, `stop_reason`, `stop_reason_codes`.
- **Per-cycle progression (table)** — one row per entry in `per_cycle_outcomes` (normalized in `build_autonomous_per_cycle_table_rows` in `argus/dashboard/console_data.py`). Columns: **Cycle**, **Outcome**, **Actions**, **Promotions**, **Notes**. At most **10** rows are shown; if there were more iterations, the table keeps the **most recent** cycles and the caption states how many were omitted. Long cells are truncated for readability. **Promotions** shows session-level promotion execution only on the **last** row (promotions run after the bounded cycle loop in the runner). **Notes** includes material-change and intervention counts where present, operator summary headline when present, and the session `stop_reason` on the last row. If `per_cycle_outcomes` is missing or empty, the UI explains that instead of showing an empty grid. A **Per-cycle outcomes (raw JSON)** expander exposes the underlying array for full detail when the table is too lossy.
- **Session summary** — the same narrative block as in the session artifact (`session_summary`).
- **Lifecycle-aware context** — primary signal, session notes, priority hints, stop/continue bias (from `lifecycle_session_influence`).
- **Promotions** — recommendations, compact tables for promotable actions, execution steps, and blocked promotions (when present on the payload).
- **Artifacts refreshed** — canonical paths touched when the session persisted pipeline stages.
- **Raw JSON** — optional expander for the full autonomous session payload.

**How to read the per-cycle table**

- **Cycle** — `cycle_index` from the session artifact (iteration number).
- **Outcome** — short string from the portfolio cycle guardrails: overall operator recommendation and quiescence recommendation when present; or `refresh_failed` / `incomplete` if the pipeline stopped early.
- **Actions** — which stages completed in order (`refresh → cycle → lifecycle → summary → narrative`), with `✗` if a stage failed.
- **Promotions** — `—` on all but the last row; on the last row, a compact summary of `promotion_execution` steps (or `skipped: …`), because execution is session-scoped.
- **Notes** — telemetry (e.g. material change count, flagged intervention count, headline) and, on the **last** row, `stop=<reason>` matching why the session ended.

**What to look at first (demo order)**

1. **Runner heartbeat** (above) — is the service alive, sleeping until the next run, or stopped? Any stale warning?
2. **Badges + stop reason** — why the session ended and whether it was a dry run.
3. **Per-cycle table** — step-by-step progression vs. only the final summary (use raw JSON if you need every field).
4. **Session summary** — one-screen story of what ran and how many cycles completed.
5. **Promotions** — what Argus could promote, what ran (if `--allow-promotion`), and what stayed blocked.
6. **Lifecycle context** — soft signals that influenced the session narrative.
7. **Raw JSON** — only when proving a point about schema or fields.

### “Needs you” panel (escalation inbox)

The **Needs you** tab is the autonomy-boundary queue: it reads **`runs/portfolio/escalation_inbox/latest.json`** (schema `argus.escalation_inbox.v1`), produced by **`argus portfolio escalation-inbox`**.

**Default view** — **action required**: rows where `in_active_queue` and `requires_operator_action` are true (compact table: category, severity, product, evidence, requested action, source, ack state).

**Supporting sections** (expanders):

- **Informational** — routine context (e.g. “plan already exists”) that does not block you by default.
- **Snoozed / resolved** — items you have already snoozed or resolved (audit / peace of mind).
- **All categories & severities** — full-inbox counts for demos when actionable work is mixed with deferred rows.

**Empty states**

- Missing file → run `argus portfolio escalation-inbox`.
- No open items → short explanation to refresh after activity.
- Nothing actionable → **“No operator action required”** (success callout).

**Raw JSON** expander for full payload.

**Needs you vs Interventions**

| | **Needs you** (escalation inbox) | **Interventions** (intervention inbox) |
|--|----------------------------------|----------------------------------------|
| **Purpose** | Only issues that **genuinely** need a human for autonomy / approval / unblock | **All** meaningful intervention rows from the portfolio intervention report |
| **Artifact** | `runs/portfolio/escalation_inbox/latest.json` | `runs/portfolio/intervention_inbox/latest.json` |
| **CLI** | `argus portfolio escalation-inbox` (+ ack/resolve/snooze) | `argus portfolio intervention-inbox` (+ intervention actions) |
| **Use when** | Explaining why **unattended automation** should pause or what **must** be decided | Day-to-day triage of flagged products and intervention categories |

Start demos with **Needs you** when you want “what must I do?” — use **Interventions** for the full intervention backlog.

## Run locally

1. Install dependencies (includes the optional `dashboard` group):

   ```bash
   uv sync --group dashboard
   ```

2. From the **repository root**:

   ```bash
   ./tools/run_dashboard.sh
   ```

   Or:

   ```bash
   uv run --group dashboard streamlit run argus/dashboard/app.py --server.port 8501
   ```

3. Open the URL Streamlit prints (default **http://localhost:8501**).

### Environment

| Variable | Meaning |
|----------|---------|
| `ARGUS_REPO_ROOT` | Repo root used to resolve `runs/`. If unset, the app resolves from `argus/dashboard/app.py` (two levels up). |
| `ARGUS_DASHBOARD_PORT` | Port for `tools/run_dashboard.sh` and `tools/run_dashboard.py` (default `8501`). |
| `UV` | Optional absolute path to the `uv` binary for non-interactive runners (`run_dashboard.sh` uses `UV_EXE="${UV:-uv}"`). |

Use **Refresh data** in the sidebar to clear the in-memory cache after new artifacts land. Optional **Wall mode** uses a meta refresh so the page reloads periodically (demo-friendly).

## Persistent startup (Linux systemd user unit)

1. Ensure `uv` is available for **non-interactive** user sessions. If `systemctl --user status` shows **exit status 127**, `uv` was not on `PATH` — the example unit sets `Environment=PATH=%h/.local/bin:…`, and `tools/run_dashboard.sh` prepends `~/.local/bin` when `uv` is missing. You can also set `Environment=UV=/full/path/to/uv` in the unit so `run_dashboard.sh` invokes that binary (see `UV_EXE` in the script).

2. Copy the example unit:

   ```bash
   mkdir -p ~/.config/systemd/user
   cp deploy/systemd/argus-dashboard.service.example ~/.config/systemd/user/argus-dashboard.service
   ```

3. Edit `WorkingDirectory`, `Environment=ARGUS_REPO_ROOT=`, and `ExecStart` to match your clone.

4. Enable and start:

   ```bash
   systemctl --user daemon-reload
   systemctl --user enable --now argus-dashboard.service
   ```

5. To have the dashboard start after **reboot** without an interactive login, enable **lingering** for your user:

   ```bash
   loginctl enable-linger "$USER"
   ```

The service runs `tools/run_dashboard.sh` on the fixed port from `ARGUS_DASHBOARD_PORT` (default `8501`).

## Demo tips

1. Generate or refresh artifacts (e.g. dashboard summary, portfolio lifecycle, operator queue) so tabs are populated.

2. Walk through **Autonomous session**: **Runner service** if you use `run-service`, then the **autonomous session** panel if you ran `run-autonomous` (or both via `run-service`). Then **Needs you** if you ran `escalation-inbox` — then **Overview** → narrative and strategy posture, **Queue** (what to work next), **Lifecycle** (portfolio state), **Learning** (policy synthesis), **Interventions** (full intervention inbox).

3. Expand **Raw … (JSON)** panels only when you need to prove grounding; the main view is summary-first.

4. If something is empty, point to the artifact path in the caption and the CLI command suggested in the empty state.

## Tests

```bash
uv run pytest tests/test_operator_console_data.py
```
